import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from nio import (
    InviteMemberEvent,
    LoginError,
    LoginResponse,
    RoomMessageText,
    SyncError,
    SyncResponse,
)

from matrix_mealie_chef.config import Settings
from matrix_mealie_chef.formatting import FormattedMessage, format_markdown
from matrix_mealie_chef.matrix_bot import (
    MatrixAuthError,
    MatrixBot,
    MatrixSession,
    MatrixSyncError,
    load_session,
    save_session,
)


def _make_settings(
    tmp_path: Path,
    *,
    password: str | None = None,
    access_token: str | None = None,
) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        matrix_homeserver="https://matrix.example.com",
        matrix_user_id="@mealie-bot:example.com",
        matrix_password=password,  # type: ignore[arg-type]
        matrix_access_token=access_token,  # type: ignore[arg-type]
        matrix_device_id="MEALIE_BOT",
        allowed_user_ids=["@alice:example.com"],
        mealie_url="http://mealie:9000",
        mealie_api_token="mealie_secret",  # type: ignore[arg-type]
        data_dir=tmp_path,
    )


class FakeAsyncClient:
    def __init__(
        self,
        *,
        login_response: Any = None,
        sync_responses: list[Any] | None = None,
    ) -> None:
        self.login_response = login_response or LoginResponse(
            user_id="@mealie-bot:example.com",
            device_id="SERVER_DEV",
            access_token="syt_from_password",
        )
        self.sync_responses = list(sync_responses or [SyncResponse.from_dict({"next_batch": "s1"})])
        self.restored: list[tuple[str, str, str]] = []
        self.login_calls: list[dict[str, Any]] = []
        self.sync_calls: list[dict[str, Any]] = []
        self.closed = False
        self.callbacks: list[tuple[Any, Any]] = []
        self.joined_rooms: list[str] = []
        self.typing_calls: list[tuple[str, bool]] = []
        self.sent_messages: list[tuple[str, str, dict[str, Any]]] = []
        self.sync_forever_calls = 0
        self.sync_forever_errors: list[Exception] = []
        self.sync_forever_started = asyncio.Event()

    def restore_login(self, user_id: str, device_id: str, access_token: str) -> None:
        self.restored.append((user_id, device_id, access_token))

    async def sync_forever(self, **_: Any) -> None:
        self.sync_forever_calls += 1
        self.sync_forever_started.set()
        if self.sync_forever_errors:
            raise self.sync_forever_errors.pop(0)
        await asyncio.Event().wait()

    async def login(self, password: str | None = None, device_name: str = "") -> Any:
        self.login_calls.append({"password": password, "device_name": device_name})
        return self.login_response

    async def sync(self, **kwargs: Any) -> Any:
        self.sync_calls.append(kwargs)
        return self.sync_responses.pop(0)

    async def close(self) -> None:
        self.closed = True

    def add_event_callback(self, callback: Any, event_filter: Any) -> None:
        self.callbacks.append((callback, event_filter))

    async def join(self, room_id: str) -> SimpleNamespace:
        self.joined_rooms.append(room_id)
        return SimpleNamespace(room_id=room_id)

    async def room_typing(
        self,
        room_id: str,
        typing_state: bool = True,
        timeout: int = 30000,  # noqa: ASYNC109
    ) -> SimpleNamespace:
        self.typing_calls.append((room_id, typing_state))
        return SimpleNamespace()

    async def room_send(
        self, room_id: str, message_type: str, content: dict[str, Any]
    ) -> SimpleNamespace:
        self.sent_messages.append((room_id, message_type, content))
        return SimpleNamespace(event_id="$reply")


async def test_authenticate_prefers_env_access_token(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, password="secret", access_token="syt_env")
    fake_client = FakeAsyncClient()
    bot = MatrixBot(settings, client=fake_client)  # type: ignore[arg-type]

    await bot.authenticate()

    assert fake_client.restored == [("@mealie-bot:example.com", "MEALIE_BOT", "syt_env")]
    assert fake_client.login_calls == []
    assert not (tmp_path / "session.json").exists()


async def test_authenticate_password_login_saves_session(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, password="secret")
    fake_client = FakeAsyncClient()
    bot = MatrixBot(settings, client=fake_client)  # type: ignore[arg-type]

    await bot.authenticate()

    assert len(fake_client.login_calls) == 1
    saved = load_session(tmp_path / "session.json")
    assert saved == MatrixSession(
        homeserver="https://matrix.example.com",
        user_id="@mealie-bot:example.com",
        device_id="SERVER_DEV",
        access_token="syt_from_password",
    )


async def test_authenticate_restores_cached_session_without_password_login(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, password="secret")
    save_session(
        tmp_path / "session.json",
        MatrixSession(
            homeserver="https://matrix.example.com",
            user_id="@mealie-bot:example.com",
            device_id="CACHED_DEV",
            access_token="syt_cached",
        ),
    )
    fake_client = FakeAsyncClient()
    bot = MatrixBot(settings, client=fake_client)  # type: ignore[arg-type]

    await bot.authenticate()

    assert fake_client.restored == [("@mealie-bot:example.com", "CACHED_DEV", "syt_cached")]
    assert fake_client.login_calls == []


async def test_initial_sync_relogs_in_when_cached_token_is_invalid(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, password="secret")
    save_session(
        tmp_path / "session.json",
        MatrixSession(
            homeserver="https://matrix.example.com",
            user_id="@mealie-bot:example.com",
            device_id="STALE_DEV",
            access_token="syt_stale",
        ),
    )
    fake_client = FakeAsyncClient(
        sync_responses=[
            SyncError(message="Invalid access token", status_code="M_UNKNOWN_TOKEN"),
            SyncResponse.from_dict({"next_batch": "s_fresh"}),
        ]
    )
    bot = MatrixBot(settings, client=fake_client)  # type: ignore[arg-type]

    await bot.authenticate()
    next_batch = await bot.initial_sync()

    assert next_batch == "s_fresh"
    assert len(fake_client.login_calls) == 1
    refreshed = load_session(tmp_path / "session.json")
    assert refreshed is not None
    assert refreshed.access_token == "syt_from_password"


async def test_authenticate_raises_on_login_error(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, password="wrong")
    fake_client = FakeAsyncClient(login_response=LoginError(message="Forbidden"))
    bot = MatrixBot(settings, client=fake_client)  # type: ignore[arg-type]

    with pytest.raises(MatrixAuthError, match="Forbidden"):
        await bot.authenticate()


async def test_initial_sync_raises_on_sync_error(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, access_token="syt_env")
    fake_client = FakeAsyncClient(sync_responses=[SyncError(message="Gateway timeout")])
    bot = MatrixBot(settings, client=fake_client)  # type: ignore[arg-type]

    await bot.authenticate()
    with pytest.raises(MatrixSyncError, match="Gateway timeout"):
        await bot.initial_sync()


class FakeHandler:
    def __init__(
        self,
        reply: FormattedMessage | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.reply = reply or format_markdown("**Recipe ready**")
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    async def handle_message(
        self, room_id: str, user_id: str, text: str
    ) -> FormattedMessage | None:
        self.calls.append((room_id, user_id, text))
        if self.error is not None:
            raise self.error
        return self.reply


def _make_message_event(*, sender: str, body: str, timestamp: int) -> RoomMessageText:
    event = RoomMessageText.from_dict(
        {
            "event_id": "$ev1",
            "sender": sender,
            "origin_server_ts": timestamp,
            "content": {"msgtype": "m.text", "body": body},
        }
    )
    assert isinstance(event, RoomMessageText)
    return event


def _make_invite_event(
    *, sender: str, state_key: str, membership: str = "invite"
) -> InviteMemberEvent:
    event = InviteMemberEvent.from_dict(
        {
            "sender": sender,
            "state_key": state_key,
            "type": "m.room.member",
            "content": {"membership": membership},
        }
    )
    assert isinstance(event, InviteMemberEvent)
    return event


async def test_on_message_handles_allowed_user_with_typing_and_html_reply(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path, access_token="syt_env")
    fake_client = FakeAsyncClient()
    handler = FakeHandler()
    bot = MatrixBot(settings, handler=handler, client=fake_client)  # type: ignore[arg-type]
    bot.register_callbacks()

    room = SimpleNamespace(room_id="!kitchen:example.com")
    event = _make_message_event(
        sender="@alice:example.com",
        body="eggs and spinach",
        timestamp=bot.started_at_ms + 1000,
    )

    await bot._on_message(room, event)  # type: ignore[arg-type]

    assert handler.calls == [("!kitchen:example.com", "@alice:example.com", "eggs and spinach")]
    assert fake_client.typing_calls == [
        ("!kitchen:example.com", True),
        ("!kitchen:example.com", False),
    ]
    assert len(fake_client.sent_messages) == 1
    room_id, msg_type, content = fake_client.sent_messages[0]
    assert room_id == "!kitchen:example.com"
    assert msg_type == "m.room.message"
    assert content["body"] == "**Recipe ready**"
    assert content["format"] == "org.matrix.custom.html"
    assert "<strong>Recipe ready</strong>" in content["formatted_body"]


async def test_on_message_ignores_self_unauthorized_and_pre_startup(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, access_token="syt_env")
    fake_client = FakeAsyncClient()
    handler = FakeHandler()
    bot = MatrixBot(settings, handler=handler, client=fake_client)  # type: ignore[arg-type]
    room = SimpleNamespace(room_id="!kitchen:example.com")

    events = [
        _make_message_event(
            sender="@mealie-bot:example.com",
            body="my own message",
            timestamp=bot.started_at_ms + 1000,
        ),
        _make_message_event(
            sender="@stranger:example.com",
            body="hack me",
            timestamp=bot.started_at_ms + 1000,
        ),
        _make_message_event(
            sender="@alice:example.com",
            body="old message",
            timestamp=bot.started_at_ms - 1,
        ),
    ]
    for ev in events:
        await bot._on_message(room, ev)  # type: ignore[arg-type]

    assert handler.calls == []
    assert fake_client.sent_messages == []


async def test_on_invite_auto_joins_only_allowed_users(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, access_token="syt_env")
    fake_client = FakeAsyncClient()
    bot = MatrixBot(settings, handler=FakeHandler(), client=fake_client)  # type: ignore[arg-type]

    room_ok = SimpleNamespace(room_id="!allowed:example.com")
    room_nope = SimpleNamespace(room_id="!stranger:example.com")

    await bot._on_invite(
        room_ok,  # type: ignore[arg-type]
        _make_invite_event(sender="@alice:example.com", state_key="@mealie-bot:example.com"),
    )
    await bot._on_invite(
        room_nope,  # type: ignore[arg-type]
        _make_invite_event(sender="@stranger:example.com", state_key="@mealie-bot:example.com"),
    )
    await bot._on_invite(
        room_nope,  # type: ignore[arg-type]
        _make_invite_event(
            sender="@alice:example.com",
            state_key="@mealie-bot:example.com",
            membership="join",
        ),
    )

    assert fake_client.joined_rooms == ["!allowed:example.com"]


async def test_on_message_serializes_room_processing_and_clears_typing_on_error(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path, access_token="syt_env")
    fake_client = FakeAsyncClient()
    timeline: list[str] = []
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    class SlowHandler:
        async def handle_message(
            self, room_id: str, user_id: str, text: str
        ) -> FormattedMessage | None:
            timeline.append(f"start:{text}")
            if text == "first":
                first_started.set()
                await release_first.wait()
            timeline.append(f"end:{text}")
            return format_markdown(text)

    bot = MatrixBot(settings, handler=SlowHandler(), client=fake_client)  # type: ignore[arg-type]
    room = SimpleNamespace(room_id="!kitchen:example.com")
    ts = bot.started_at_ms + 1000

    task1 = asyncio.create_task(
        bot._on_message(
            room, _make_message_event(sender="@alice:example.com", body="first", timestamp=ts)
        )  # type: ignore[arg-type]
    )
    await first_started.wait()
    task2 = asyncio.create_task(
        bot._on_message(
            room, _make_message_event(sender="@alice:example.com", body="second", timestamp=ts + 1)
        )  # type: ignore[arg-type]
    )
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(task1, task2)

    assert timeline == ["start:first", "end:first", "start:second", "end:second"]

    # Ensure typing indicator is still cleared if handler raises unexpectedly
    fake_client.typing_calls.clear()
    crashing_bot = MatrixBot(
        settings,
        handler=FakeHandler(error=RuntimeError("boom")),
        client=fake_client,  # type: ignore[arg-type]
    )
    await crashing_bot._on_message(
        room,  # type: ignore[arg-type]
        _make_message_event(sender="@alice:example.com", body="oops", timestamp=ts + 2),
    )
    assert fake_client.typing_calls == [
        ("!kitchen:example.com", True),
        ("!kitchen:example.com", False),
    ]


async def test_run_forever_starts_and_stops_cleanly(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, access_token="syt_env")
    fake_client = FakeAsyncClient()
    bot = MatrixBot(settings, handler=FakeHandler(), client=fake_client)  # type: ignore[arg-type]
    stop_event = asyncio.Event()

    task = asyncio.create_task(bot.run_forever(stop_event=stop_event))
    await asyncio.wait_for(fake_client.sync_forever_started.wait(), timeout=2.0)
    stop_event.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert len(fake_client.restored) == 1
    assert len(fake_client.sync_calls) == 1
    assert [f for _, f in fake_client.callbacks] == [InviteMemberEvent, RoomMessageText]
    assert fake_client.sync_forever_calls == 1


async def test_run_forever_reconnects_after_sync_error(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path, access_token="syt_env")
    fake_client = FakeAsyncClient()
    fake_client.sync_forever_errors = [ConnectionError("temporary drop")]
    bot = MatrixBot(settings, handler=FakeHandler(), client=fake_client)  # type: ignore[arg-type]
    stop_event = asyncio.Event()

    async def stop_after_reconnect() -> None:
        while fake_client.sync_forever_calls < 2:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        stop_event.set()

    stopper = asyncio.create_task(stop_after_reconnect())
    await asyncio.wait_for(
        bot.run_forever(stop_event=stop_event, initial_backoff=0.01, max_backoff=0.02),
        timeout=2.0,
    )
    await stopper

    assert fake_client.sync_forever_calls == 2
