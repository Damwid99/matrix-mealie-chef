"""Matrix client authentication, session persistence, and event wiring."""

import contextlib
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog
from nio import AsyncClient, LoginError, LoginResponse, SyncError, SyncResponse

from matrix_mealie_chef.config import Settings

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
        client: AsyncClient | None = None,
    ) -> None:
        self._settings = settings
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

    async def close(self) -> None:
        """Close the Matrix client HTTP session."""
        await self._client.close()
