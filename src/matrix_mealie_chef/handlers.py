"""Transport-agnostic conversation and command handler."""

from collections.abc import Sequence
from typing import Protocol

import structlog

from matrix_mealie_chef.drafts import DraftStore, RecipeDraft
from matrix_mealie_chef.formatting import (
    FormattedMessage,
    format_help,
    format_markdown,
    format_recipe_preview,
    format_saved_recipe,
)
from matrix_mealie_chef.llm import LLMAuthError, LLMError, LLMParseError, LLMRateLimitError
from matrix_mealie_chef.mealie import CreatedRecipe, MealieAuthError, MealieError
from matrix_mealie_chef.models import Recipe

logger = structlog.get_logger(__name__)


class RecipeLLM(Protocol):
    """Protocol for recipe generation and refinement backends."""

    async def generate(self, user_input: str) -> Recipe: ...

    async def refine(
        self,
        current: Recipe,
        instruction: str,
        history: Sequence[str] | None = None,
    ) -> Recipe: ...


class RecipeStore(Protocol):
    """Protocol for persisting approved recipes."""

    async def create_recipe(self, recipe: Recipe) -> CreatedRecipe: ...


class ConversationHandler:
    """Routes incoming user messages and commands to LLM, draft store, and Mealie."""

    def __init__(
        self,
        llm: RecipeLLM,
        mealie: RecipeStore,
        drafts: DraftStore,
    ) -> None:
        self._llm = llm
        self._mealie = mealie
        self._drafts = drafts

    async def handle_message(
        self,
        room_id: str,
        user_id: str,
        text: str,
    ) -> FormattedMessage | None:
        """Process one incoming user message and return the reply (or None to ignore)."""
        cleaned = text.strip()
        if not cleaned:
            return None

        if cleaned.startswith("!"):
            parts = cleaned.split(maxsplit=1)
            command = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""
            if len(command) > 1 and command[1:].isalpha():
                return await self._handle_command(room_id, user_id, command, arg)

        draft = self._drafts.get(room_id, user_id)
        if draft is None:
            return await self._generate_draft(room_id, user_id, cleaned)
        return await self._refine_draft(room_id, user_id, draft, cleaned)

    async def _handle_command(
        self,
        room_id: str,
        user_id: str,
        command: str,
        arg: str,
    ) -> FormattedMessage:
        if command == "!help":
            return format_help()
        if command == "!show":
            draft = self._drafts.get(room_id, user_id)
            if draft is None:
                return format_markdown(
                    "You do not have an active recipe draft. "
                    "Send me ingredients or a meal idea to start one!"
                )
            return format_recipe_preview(draft.recipe)
        if command in ("!discard", "!cancel"):
            if self._drafts.delete(room_id, user_id):
                return format_markdown(
                    "Draft discarded. Send me a new recipe idea whenever you are ready!"
                )
            return format_markdown("There is no active draft to discard.")
        if command == "!save":
            return await self._save_draft(room_id, user_id)
        if command == "!new":
            if not arg:
                return format_markdown(
                    "Usage: `!new <ingredients or recipe idea>` "
                    "(for example: `!new creamy mushroom risotto`)."
                )
            return await self._generate_draft(room_id, user_id, arg)
        return format_markdown(
            f"Unknown command `{command}`. Send `!help` to see available commands."
        )

    async def _generate_draft(
        self,
        room_id: str,
        user_id: str,
        prompt: str,
    ) -> FormattedMessage:
        try:
            recipe = await self._llm.generate(prompt)
        except LLMError as exc:
            return self._format_llm_error(exc, action="generate", room_id=room_id, user_id=user_id)

        self._drafts.set(room_id, user_id, recipe, prompt)
        return format_recipe_preview(recipe)

    async def _refine_draft(
        self,
        room_id: str,
        user_id: str,
        draft: RecipeDraft,
        instruction: str,
    ) -> FormattedMessage:
        try:
            updated = await self._llm.refine(draft.recipe, instruction, history=draft.history)
        except LLMError as exc:
            return self._format_llm_error(exc, action="refine", room_id=room_id, user_id=user_id)

        self._drafts.update(room_id, user_id, updated, instruction)
        return format_recipe_preview(updated)

    async def _save_draft(self, room_id: str, user_id: str) -> FormattedMessage:
        draft = self._drafts.get(room_id, user_id)
        if draft is None:
            return format_markdown(
                "Nothing to save — you do not have an active draft yet. "
                "Send me ingredients or a meal idea first!"
            )

        try:
            created = await self._mealie.create_recipe(draft.recipe)
        except MealieAuthError as exc:
            logger.error(
                "mealie_save_auth_failed",
                room_id=room_id,
                user_id=user_id,
                error=str(exc),
            )
            return format_markdown(
                "Could not save to Mealie: authentication failed. "
                "Please check `MEALIE_API_TOKEN`. Your draft is still kept — "
                "try `!save` again once fixed."
            )
        except MealieError as exc:
            logger.error(
                "mealie_save_failed",
                room_id=room_id,
                user_id=user_id,
                error=str(exc),
            )
            return format_markdown(
                "Could not save to Mealie right now. "
                "Your draft is still safe — please try `!save` again in a moment."
            )

        self._drafts.delete(room_id, user_id)
        return format_saved_recipe(created)

    def _format_llm_error(
        self,
        exc: LLMError,
        *,
        action: str,
        room_id: str,
        user_id: str,
    ) -> FormattedMessage:
        if isinstance(exc, LLMAuthError):
            logger.error(
                "llm_auth_error",
                action=action,
                room_id=room_id,
                user_id=user_id,
                error=str(exc),
            )
            return format_markdown(
                "The AI provider rejected the API key. Please check your LLM configuration."
            )
        if isinstance(exc, LLMRateLimitError):
            logger.warning(
                "llm_rate_limit",
                action=action,
                room_id=room_id,
                user_id=user_id,
                error=str(exc),
            )
            return format_markdown(
                "The AI provider rate limit was reached. Please wait a moment and try again."
            )
        if isinstance(exc, LLMParseError):
            logger.warning(
                "llm_parse_error",
                action=action,
                room_id=room_id,
                user_id=user_id,
                error=str(exc),
            )
            return format_markdown(
                "I had trouble structuring that recipe. Try rephrasing or giving a bit more detail."
            )
        logger.error(
            "llm_error",
            action=action,
            room_id=room_id,
            user_id=user_id,
            error=str(exc),
        )
        return format_markdown(
            "Something went wrong while talking to the AI model. Please try again in a moment."
        )
