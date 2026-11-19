"""Model-agnostic async LLM client powered by LiteLLM."""

import json  # noqa: I001
import re
from typing import Any

import litellm
import structlog
from pydantic import ValidationError

from matrix_mealie_chef.models import Recipe
from matrix_mealie_chef.prompts import build_generate_messages

import litellm.exceptions

logger = structlog.get_logger(__name__)

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


class LLMError(Exception):
    """Base exception for LLM client failures."""


class LLMAuthError(LLMError):
    """Raised when the LLM provider rejects credentials."""


class LLMRateLimitError(LLMError):
    """Raised when the LLM provider rate limit or quota is hit."""


class LLMParseError(LLMError):
    """Raised when the model fails to produce valid Recipe JSON."""


def _extract_json_text(raw: str) -> str:
    """Extract a JSON object from raw model output (handling code fences or extra text)."""
    text = raw.strip()
    if not text:
        raise ValueError("Empty response from LLM")

    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()

    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]

    return text


def _parse_recipe(raw: str) -> Recipe:
    json_text = _extract_json_text(raw)
    data = json.loads(json_text)
    return Recipe.model_validate(data)


class RecipeLLMClient:
    """Generates and refines structured Recipe objects using LiteLLM."""

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: float = 60.0,
        max_validation_retries: int = 2,
        temperature: float = 0.7,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._api_base = api_base
        self._timeout = timeout
        self._max_validation_retries = max_validation_retries
        self._temperature = temperature
        self._supports_response_format = True

    async def _call_model(
        self,
        messages: list[dict[str, str]],
        *,
        use_response_format: bool,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "timeout": self._timeout,
            "temperature": self._temperature,
        }
        if self._api_key is not None:
            kwargs["api_key"] = self._api_key
        if self._api_base is not None:
            kwargs["api_base"] = self._api_base
        if use_response_format:
            kwargs["response_format"] = Recipe

        try:
            response = await litellm.acompletion(**kwargs)
        except litellm.litellm.exceptions.AuthenticationError as exc:
            raise LLMAuthError(f"LLM authentication failed: {exc}") from exc
        except litellm.litellm.exceptions.RateLimitError as exc:
            raise LLMRateLimitError(f"LLM rate limit exceeded: {exc}") from exc
        except (
            litellm.litellm.exceptions.UnsupportedParamsError,
            litellm.litellm.exceptions.BadRequestError,
        ) as exc:
            if use_response_format:
                logger.warning(
                    "llm_response_format_unsupported_fallback",
                    model=self._model,
                    error=str(exc),
                )
                self._supports_response_format = False
                return await self._call_model(messages, use_response_format=False)
            raise LLMError(f"LLM request failed: {exc}") from exc
        except Exception as exc:
            raise LLMError(f"LLM call failed: {exc}") from exc

        choices = getattr(response, "choices", None)
        if not choices:
            raise LLMParseError("LLM returned no choices")

        content = getattr(choices[0].message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise LLMParseError("LLM returned an empty message")
        return content

    async def _complete_recipe(self, messages: list[dict[str, str]]) -> Recipe:
        conversation = list(messages)
        last_error: Exception | None = None

        for attempt in range(1, self._max_validation_retries + 2):
            raw_content = await self._call_model(
                conversation,
                use_response_format=self._supports_response_format,
            )
            try:
                recipe = _parse_recipe(raw_content)
                logger.info("llm_recipe_generated", model=self._model, name=recipe.name)
                return recipe
            except (ValueError, json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                logger.warning(
                    "llm_recipe_validation_failed",
                    attempt=attempt,
                    model=self._model,
                    error=str(exc),
                )
                conversation.append({"role": "assistant", "content": raw_content})
                conversation.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was not valid Recipe JSON. "
                            f"Fix these errors and return ONLY valid JSON:\n{exc}"
                        ),
                    }
                )

        raise LLMParseError(
            f"Failed to produce a valid recipe after {self._max_validation_retries + 1} attempts: "
            f"{last_error}"
        )

    async def generate(self, user_input: str) -> Recipe:
        """Generate a new structured recipe from free-form user text."""
        messages = build_generate_messages(user_input)
        return await self._complete_recipe(messages)
