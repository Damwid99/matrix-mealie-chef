from pathlib import Path
from typing import Any

import pytest
from nio import LoginError, LoginResponse, SyncError, SyncResponse

from matrix_mealie_chef.config import Settings
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

    def restore_login(self, user_id: str, device_id: str, access_token: str) -> None:
        self.restored.append((user_id, device_id, access_token))

    async def login(self, password: str | None = None, device_name: str = "") -> Any:
        self.login_calls.append({"password": password, "device_name": device_name})
        return self.login_response

    async def sync(self, **kwargs: Any) -> Any:
        self.sync_calls.append(kwargs)
        return self.sync_responses.pop(0)

    async def close(self) -> None:
        self.closed = True


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
