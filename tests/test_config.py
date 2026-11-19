import json
from pathlib import Path

import pytest
import structlog
from pydantic import ValidationError

from matrix_mealie_chef.config import Settings
from matrix_mealie_chef.logging import configure_logging

ENV_KEYS = (
    "MATRIX_HOMESERVER",
    "MATRIX_USER_ID",
    "MATRIX_PASSWORD",
    "MATRIX_ACCESS_TOKEN",
    "MATRIX_DEVICE_ID",
    "ALLOWED_USER_IDS",
    "MEALIE_URL",
    "MEALIE_API_TOKEN",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_API_BASE",
    "DATA_DIR",
    "DRAFT_TTL_SECONDS",
    "LOG_LEVEL",
    "LOG_JSON",
)


@pytest.fixture
def base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://matrix.example.com/")
    monkeypatch.setenv("MATRIX_USER_ID", "@mealie-bot:example.com")
    monkeypatch.setenv("MATRIX_ACCESS_TOKEN", "syt_secret")
    monkeypatch.setenv("ALLOWED_USER_IDS", "@alice:example.com, @bob:example.com")
    monkeypatch.setenv("MEALIE_URL", "http://mealie:9000/")
    monkeypatch.setenv("MEALIE_API_TOKEN", "mealie_secret")


def test_settings_from_env(base_env: None) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.matrix_homeserver == "https://matrix.example.com"
    assert settings.matrix_user_id == "@mealie-bot:example.com"
    assert settings.matrix_password is None
    assert settings.matrix_access_token is not None
    assert settings.matrix_access_token.get_secret_value() == "syt_secret"
    assert settings.matrix_device_id == "MEALIE_BOT"
    assert settings.allowed_user_ids == ["@alice:example.com", "@bob:example.com"]
    assert settings.mealie_url == "http://mealie:9000"
    assert settings.mealie_api_token.get_secret_value() == "mealie_secret"
    assert settings.llm_model == "gemini/gemini-2.5-flash"
    assert settings.llm_api_key is None
    assert settings.llm_api_base is None
    assert settings.data_dir == Path("./data")
    assert settings.draft_ttl_seconds == 7200
    assert settings.log_level == "INFO"
    assert settings.log_json is False


def test_empty_optional_vars_become_none(base_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MATRIX_PASSWORD", "   ")
    monkeypatch.setenv("LLM_API_BASE", "")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.matrix_password is None
    assert settings.llm_api_base is None
    assert settings.log_level == "DEBUG"


def test_requires_password_or_access_token(base_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MATRIX_ACCESS_TOKEN")
    monkeypatch.setenv("MATRIX_PASSWORD", "")

    with pytest.raises(ValidationError, match="MATRIX_PASSWORD or MATRIX_ACCESS_TOKEN"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_password_only_auth_is_valid(base_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MATRIX_ACCESS_TOKEN")
    monkeypatch.setenv("MATRIX_PASSWORD", "hunter2")

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.matrix_access_token is None
    assert settings.matrix_password is not None
    assert settings.matrix_password.get_secret_value() == "hunter2"


@pytest.mark.parametrize(
    ("env_var", "bad_value"),
    [
        ("MATRIX_HOMESERVER", "matrix.example.com"),
        ("MEALIE_URL", "ftp://mealie:9000"),
        ("MATRIX_USER_ID", "not-a-matrix-id"),
        ("ALLOWED_USER_IDS", "  ,  "),
        ("ALLOWED_USER_IDS", "alice:example.com"),
        ("MEALIE_API_TOKEN", "   "),
        ("DRAFT_TTL_SECONDS", "0"),
        ("LOG_LEVEL", "VERBOSE"),
    ],
)
def test_invalid_values_raise_validation_error(
    base_env: None,
    monkeypatch: pytest.MonkeyPatch,
    env_var: str,
    bad_value: str,
) -> None:
    monkeypatch.setenv(env_var, bad_value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_configure_logging_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", json_logs=True)
    structlog.get_logger("test.logger").info("bot_started", version="0.1.0")

    captured = capsys.readouterr().out.strip()
    payload = json.loads(captured)
    assert payload["event"] == "bot_started"
    assert payload["version"] == "0.1.0"
    assert payload["level"] == "info"
    assert payload["logger"] == "test.logger"
    assert "timestamp" in payload
