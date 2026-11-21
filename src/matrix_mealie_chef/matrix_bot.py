"""Matrix client authentication, session persistence, and event wiring."""

import asyncio
import contextlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol  # noqa: F401

import structlog
from nio import (  # noqa: F401
    AsyncClient,
    Event,
    InviteMemberEvent,
    JoinError,
    LoginError,
    LoginResponse,
    MatrixRoom,
    RoomMessageText,
    RoomSendError,
    SyncError,
    SyncResponse,
)

from matrix_mealie_chef.config import Settings
from matrix_mealie_chef.formatting import FormattedMessage  # noqa: F401

logger = structlog.get_logger(__name__)

SESSION_FILENAME = "session.json"


class MatrixBotError(Exception):
    """Base exception for Matrix bot failures."""


class MatrixAuthError(MatrixBotError):
    """Raised when Matrix authentication fails."""


class MatrixSyncError(MatrixBotError):
    """Raised when Matrix initial sync fails."""


@dataclass(frozen=True, slots=True)
class MatrixSession:
    """Persisted Matrix login credentials cached in `DATA_DIR/session.json`."""

    homeserver: str
    user_id: str
    device_id: str
    access_token: str


class MessageHandler(Protocol):
    """Protocol for handling incoming user messages."""

    async def handle_message(
        self,
        room_id: str,
        user_id: str,
        text: str,
    ) -> FormattedMessage | None: ...


def load_session(path: Path) -> MatrixSession | None:
    """Load a cached Matrix session from disk, or return None if missing/invalid."""
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("matrix_session_load_failed", path=str(path), error=str(exc))
        return None

    if not isinstance(raw, dict):
        return None

    homeserver = raw.get("homeserver")
    user_id = raw.get("user_id")
    device_id = raw.get("device_id")
    access_token = raw.get("access_token")
    if (
        isinstance(homeserver, str)
        and homeserver
        and isinstance(user_id, str)
        and user_id
        and isinstance(device_id, str)
        and device_id
        and isinstance(access_token, str)
        and access_token
    ):
        return MatrixSession(
            homeserver=homeserver,
            user_id=user_id,
            device_id=device_id,
            access_token=access_token,
        )
    return None


def save_session(path: Path, session: MatrixSession) -> None:
    """Persist a Matrix session to disk with restricted file permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(session), indent=2) + "\n", encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


class MatrixBot:
    """Manages Matrix authentication, sync state, and event handling."""

    def __init__(
        self,
        settings: Settings,
        *,
        handler: MessageHandler | None = None,
        client: AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._handler = handler
        self._allowed_users: frozenset[str] = frozenset(settings.allowed_user_ids)
        self._room_locks: dict[str, asyncio.Lock] = {}
        self._session_path = settings.data_dir / SESSION_FILENAME
        self._client = client or AsyncClient(
            homeserver=settings.matrix_homeserver,
            user=settings.matrix_user_id,
            device_id=settings.matrix_device_id,
        )
        self._started_at_ms: int = int(time.time() * 1000)
        self._used_cached_session = False

    @property
    def client(self) -> AsyncClient:
        """Return the underlying matrix-nio AsyncClient."""
        return self._client

    @property
    def started_at_ms(self) -> int:
        """Return the startup timestamp (epoch ms) used to ignore old messages."""
        return self._started_at_ms

    async def authenticate(self) -> None:
        """Authenticate using env access token, cached session, or password login."""
        if self._settings.matrix_access_token is not None:
            token = self._settings.matrix_access_token.get_secret_value()
            self._client.restore_login(
                user_id=self._settings.matrix_user_id,
                device_id=self._settings.matrix_device_id,
                access_token=token,
            )
            self._used_cached_session = False
            logger.info(
                "matrix_authenticated_with_env_token", user_id=self._settings.matrix_user_id
            )
            return

        cached = load_session(self._session_path)
        if (
            cached is not None
            and cached.homeserver == self._settings.matrix_homeserver
            and cached.user_id == self._settings.matrix_user_id
        ):
            self._client.restore_login(
                user_id=cached.user_id,
                device_id=cached.device_id,
                access_token=cached.access_token,
            )
            self._used_cached_session = True
            logger.info(
                "matrix_session_restored",
                user_id=cached.user_id,
                device_id=cached.device_id,
            )
            return

        await self._login_with_password()

    async def _login_with_password(self) -> None:
        if self._settings.matrix_password is None:
            raise MatrixAuthError("MATRIX_PASSWORD is required when no access token is available")

        response = await self._client.login(
            password=self._settings.matrix_password.get_secret_value(),
            device_name="Matrix Mealie Chef",
        )
        if isinstance(response, LoginError) or not isinstance(response, LoginResponse):
            message = getattr(response, "message", str(response))
            raise MatrixAuthError(f"Matrix password login failed: {message}")

        session = MatrixSession(
            homeserver=self._settings.matrix_homeserver,
            user_id=response.user_id,
            device_id=response.device_id,
            access_token=response.access_token,
        )
        save_session(self._session_path, session)
        self._used_cached_session = False
        logger.info(
            "matrix_logged_in_with_password",
            user_id=session.user_id,
            device_id=session.device_id,
        )

    async def initial_sync(self, *, timeout_ms: int = 10000) -> str:
        """Perform an initial sync to advance `next_batch` and skip pre-startup messages."""
        self._started_at_ms = int(time.time() * 1000)
        response = await self._client.sync(timeout=timeout_ms, full_state=False)

        if isinstance(response, SyncError):
            status_code = getattr(response, "status_code", None)
            if (
                status_code == "M_UNKNOWN_TOKEN"
                and self._used_cached_session
                and self._settings.matrix_password is not None
            ):
                logger.warning("matrix_cached_token_invalid_relogging_in")
                self._session_path.unlink(missing_ok=True)
                await self._login_with_password()
                response = await self._client.sync(timeout=timeout_ms, full_state=False)

        if not isinstance(response, SyncResponse):
            message = getattr(response, "message", str(response))
            raise MatrixSyncError(f"Initial Matrix sync failed: {message}")

        next_batch: str = response.next_batch
        logger.info("matrix_initial_sync_complete", next_batch=next_batch)
        return next_batch

    def register_callbacks(self) -> None:
        """Register Matrix event callbacks for room messages and invites."""
        if self._handler is None:
            raise MatrixBotError("Cannot register callbacks without a message handler")
        self._client.add_event_callback(self._on_message, RoomMessageText)
        self._client.add_event_callback(self._on_invite, InviteMemberEvent)

    async def _on_invite(self, room: MatrixRoom, event: Event) -> None:
        if not isinstance(event, InviteMemberEvent):
            return
        if event.membership != "invite" or event.state_key != self._settings.matrix_user_id:
            return
        if event.sender not in self._allowed_users:
            logger.info(
                "matrix_invite_ignored_unauthorized",
                room_id=room.room_id,
                sender=event.sender,
            )
            return

        response = await self._client.join(room.room_id)
        if isinstance(response, JoinError):
            logger.warning(
                "matrix_invite_join_failed",
                room_id=room.room_id,
                sender=event.sender,
                error=response.message,
            )
            return
        logger.info("matrix_room_joined", room_id=room.room_id, invited_by=event.sender)

    async def _on_message(self, room: MatrixRoom, event: Event) -> None:
        if not isinstance(event, RoomMessageText) or self._handler is None:
            return
        if event.sender == self._settings.matrix_user_id:
            return
        if event.server_timestamp < self._started_at_ms:
            logger.debug(
                "matrix_message_ignored_pre_startup",
                room_id=room.room_id,
                event_id=event.event_id,
            )
            return
        if event.sender not in self._allowed_users:
            logger.debug(
                "matrix_message_ignored_unauthorized",
                room_id=room.room_id,
                sender=event.sender,
            )
            return

        lock = self._room_locks.setdefault(room.room_id, asyncio.Lock())
        async with lock:
            await self._set_typing(room.room_id, typing=True)
            try:
                reply = await self._handler.handle_message(
                    room.room_id,
                    event.sender,
                    event.body,
                )
            except Exception:
                logger.exception(
                    "matrix_message_handler_crashed",
                    room_id=room.room_id,
                    sender=event.sender,
                )
                return
            finally:
                await self._set_typing(room.room_id, typing=False)

            if reply is not None:
                await self.send_message(room.room_id, reply)

    async def _set_typing(self, room_id: str, *, typing: bool) -> None:
        try:
            await self._client.room_typing(room_id, typing_state=typing, timeout=60000)
        except Exception as exc:
            logger.debug("matrix_typing_failed", room_id=room_id, typing=typing, error=str(exc))

    async def send_message(self, room_id: str, message: FormattedMessage) -> None:
        """Send a Markdown + HTML reply to a Matrix room."""
        content: dict[str, Any] = {
            "msgtype": "m.text",
            "body": message.body,
            "format": "org.matrix.custom.html",
            "formatted_body": message.formatted_body,
        }
        response = await self._client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content=content,
        )
        if isinstance(response, RoomSendError):
            logger.error(
                "matrix_room_send_failed",
                room_id=room_id,
                error=response.message,
            )

    def _register_invite_callback(self) -> None:
        self._client.add_event_callback(self._on_invite, InviteMemberEvent)

    def _register_message_callback(self) -> None:
        self._client.add_event_callback(self._on_message, RoomMessageText)

    async def run_forever(
        self,
        *,
        stop_event: asyncio.Event | None = None,
        sync_timeout_ms: int = 30000,
        initial_backoff: float = 2.0,
        max_backoff: float = 60.0,
    ) -> None:
        """Authenticate, perform initial sync, and run the sync loop with backoff on errors."""
        if self._handler is None:
            raise MatrixBotError("Cannot run MatrixBot without a message handler")

        await self.authenticate()
        # Accept pending invites during initial sync, but attach message handler only after
        # initial sync drains historical messages.
        self._register_invite_callback()
        await self.initial_sync()
        self._register_message_callback()

        shutdown = stop_event or asyncio.Event()
        backoff = initial_backoff

        while not shutdown.is_set():
            sync_task: asyncio.Task[SyncResponse | SyncError] = asyncio.create_task(
                self._client.sync(timeout=sync_timeout_ms, full_state=False)
            )
            stop_task: asyncio.Task[bool] = asyncio.create_task(shutdown.wait())
            delay: float | None = None
            try:
                _, pending = await asyncio.wait(
                    {sync_task, stop_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

                if shutdown.is_set():
                    break

                exc = sync_task.exception()
                if exc is not None:
                    logger.warning(
                        "matrix_sync_exception_reconnecting",
                        error=str(exc),
                        backoff_seconds=backoff,
                    )
                    delay = backoff
                else:
                    response = sync_task.result()
                    if isinstance(response, SyncResponse):
                        backoff = initial_backoff
                        continue

                    status_code = getattr(response, "status_code", None)
                    if (
                        status_code == "M_UNKNOWN_TOKEN"
                        and self._settings.matrix_password is not None
                    ):
                        logger.warning("matrix_token_invalid_relogging_in")
                        self._session_path.unlink(missing_ok=True)
                        try:
                            await self._login_with_password()
                        except MatrixAuthError as auth_exc:
                            logger.error("matrix_relogin_failed", error=str(auth_exc))

                    retry_after_ms = getattr(response, "retry_after_ms", None)
                    retry_after = (
                        retry_after_ms / 1000.0
                        if isinstance(retry_after_ms, (int, float)) and retry_after_ms > 0
                        else 0.0
                    )
                    delay = min(max(backoff, retry_after), max_backoff)
                    logger.warning(
                        "matrix_sync_error_backing_off",
                        status_code=status_code,
                        error=getattr(response, "message", str(response)),
                        backoff_seconds=delay,
                    )
            finally:
                if not sync_task.done():
                    sync_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await sync_task
                if not stop_task.done():
                    stop_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await stop_task

            if delay is not None:
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=delay)
                    break
                except TimeoutError:
                    backoff = min(backoff * 2.0, max_backoff)

    async def close(self) -> None:
        """Close the Matrix client HTTP session."""
        await self._client.close()
