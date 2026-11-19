"""In-memory TTL store for active recipe drafts."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from cachetools import TTLCache

from matrix_mealie_chef.models import Recipe

DraftKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class RecipeDraft:
    """An active unsaved recipe draft and its user request history."""

    recipe: Recipe
    history: tuple[str, ...] = field(default_factory=tuple)


class DraftStore:
    """Per-(room, user) TTL store for unsaved recipe drafts."""

    def __init__(
        self,
        ttl_seconds: float = 7200.0,
        *,
        maxsize: int = 1000,
        max_history: int = 20,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_history = max_history
        self._cache: TTLCache[DraftKey, RecipeDraft] = TTLCache(
            maxsize=maxsize,
            ttl=ttl_seconds,
            timer=timer,
        )

    def get(self, room_id: str, user_id: str) -> RecipeDraft | None:
        """Return the active draft for (room, user), or None if absent/expired."""
        return self._cache.get((room_id, user_id))

    def has(self, room_id: str, user_id: str) -> bool:
        """Return True if an active draft exists for (room, user)."""
        return (room_id, user_id) in self._cache

    def set(self, room_id: str, user_id: str, recipe: Recipe, prompt: str) -> RecipeDraft:
        """Store a brand-new draft, replacing any previous draft for (room, user)."""
        cleaned = prompt.strip()
        history = (cleaned,) if cleaned else ()
        draft = RecipeDraft(recipe=recipe, history=history)
        self._cache[(room_id, user_id)] = draft
        return draft

    def update(self, room_id: str, user_id: str, recipe: Recipe, instruction: str) -> RecipeDraft:
        """Replace the recipe on an existing draft, append instruction, and refresh TTL."""
        existing = self._cache.get((room_id, user_id))
        prior = existing.history if existing is not None else ()
        cleaned = instruction.strip()
        new_history = (*prior, cleaned) if cleaned else prior
        if self._max_history > 0 and len(new_history) > self._max_history:
            new_history = new_history[-self._max_history :]
        draft = RecipeDraft(recipe=recipe, history=new_history)
        self._cache[(room_id, user_id)] = draft
        return draft

    def delete(self, room_id: str, user_id: str) -> bool:
        """Delete the draft for (room, user). Returns True if a draft existed."""
        return self._cache.pop((room_id, user_id), None) is not None
