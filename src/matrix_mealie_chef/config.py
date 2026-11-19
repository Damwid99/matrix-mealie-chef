"""Application configuration."""

from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Application settings loaded from environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Matrix
    matrix_homeserver: str
    matrix_user_id: str
    matrix_password: SecretStr | None = None
    matrix_access_token: SecretStr | None = None
    matrix_device_id: str = "MEALIE_BOT"
    allowed_user_ids: Annotated[list[str], NoDecode]

    # Mealie
    mealie_url: str
    mealie_api_token: SecretStr

    # LLM (LiteLLM)
    llm_model: str = "gemini/gemini-2.5-flash"
    llm_api_key: SecretStr | None = None
    llm_api_base: str | None = None

    # Runtime
    data_dir: Path = Path("./data")
    draft_ttl_seconds: int = Field(default=7200, gt=0)
    log_level: LogLevel = "INFO"
    log_json: bool = False

    @field_validator(
        "matrix_password",
        "matrix_access_token",
        "llm_api_key",
        "llm_api_base",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("matrix_homeserver", "mealie_url")
    @classmethod
    def _validate_http_url(cls, value: str) -> str:
        url = value.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            msg = f"URL must start with http:// or https://, got {value!r}"
            raise ValueError(msg)
        return url

    @field_validator("matrix_user_id")
    @classmethod
    def _validate_matrix_user_id(cls, value: str) -> str:
        user_id = value.strip()
        if not user_id.startswith("@") or ":" not in user_id:
            msg = f"Invalid Matrix user ID: {value!r} (expected @user:homeserver)"
            raise ValueError(msg)
        return user_id

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def _parse_allowed_user_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("allowed_user_ids")
    @classmethod
    def _validate_allowed_user_ids(cls, value: list[str]) -> list[str]:
        if not value:
            msg = "ALLOWED_USER_IDS must contain at least one Matrix user ID"
            raise ValueError(msg)
        for user_id in value:
            if not user_id.startswith("@") or ":" not in user_id:
                msg = f"Invalid Matrix user ID in ALLOWED_USER_IDS: {user_id!r}"
                raise ValueError(msg)
        return value

    @field_validator("mealie_api_token")
    @classmethod
    def _validate_mealie_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            msg = "MEALIE_API_TOKEN must not be empty"
            raise ValueError(msg)
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @model_validator(mode="after")
    def _validate_matrix_auth(self) -> Self:
        has_password = self.matrix_password is not None and bool(
            self.matrix_password.get_secret_value().strip()
        )
        has_token = self.matrix_access_token is not None and bool(
            self.matrix_access_token.get_secret_value().strip()
        )
        if not has_password and not has_token:
            msg = "Either MATRIX_PASSWORD or MATRIX_ACCESS_TOKEN must be set"
            raise ValueError(msg)
        return self


def load_settings() -> Settings:
    """Load settings from environment variables and `.env`."""
    return Settings()  # type: ignore[call-arg]
