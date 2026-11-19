"""In-memory TTL store for active conversational recipe drafts."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from cachetools import TTLCache

DraftKey = tuple[str, str]
MessageRole = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """A single turn message in a conversational recipe draft."""

    role: MessageRole
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class DraftSession:
    """An active unsaved conversational draft session."""

    messages: tuple[ChatMessage, ...] = field(default_factory=tuple)

    @property
    def last_reply(self) -> str | None:
        """Return the most recent assistant reply, if any."""
        for message in reversed(self.messages):
            if message.role == "assistant":
                return message.content
        return None

    def to_llm_messages(self) -> list[dict[str, str]]:
        """Serialize messages into LiteLLM-compatible role/content dicts."""
        return [message.to_dict() for message in self.messages]


class DraftStore:
    """Per-(room, user) TTL store for unsaved conversational recipe drafts."""

    def __init__(
        self,
        ttl_seconds: float = 7200.0,
        *,
        maxsize: int = 1000,
        max_turns: int = 10,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_messages = max(0, max_turns * 2)
        self._cache: TTLCache[DraftKey, DraftSession] = TTLCache(
            maxsize=maxsize,
            ttl=ttl_seconds,
            timer=timer,
        )

    def get(self, room_id: str, user_id: str) -> DraftSession | None:
        """Return the active draft session for (room, user), or None if absent/expired."""
        return self._cache.get((room_id, user_id))

    def has(self, room_id: str, user_id: str) -> bool:
        """Return True if an active draft session exists for (room, user)."""
        return (room_id, user_id) in self._cache

    def start(
        self,
        room_id: str,
        user_id: str,
        user_text: str,
        assistant_reply: str,
    ) -> DraftSession:
        """Start a brand-new draft session, replacing any previous session."""
        messages = self._trim(
            (
                ChatMessage(role="user", content=user_text.strip()),
                ChatMessage(role="assistant", content=assistant_reply.strip()),
            )
        )
        session = DraftSession(messages=messages)
        self._cache[(room_id, user_id)] = session
        return session

    def append_turn(
        self,
        room_id: str,
        user_id: str,
        user_text: str,
        assistant_reply: str,
    ) -> DraftSession:
        """Append a user/assistant exchange to the draft session and refresh TTL."""
        existing = self._cache.get((room_id, user_id))
        prior = existing.messages if existing is not None else ()
        messages = self._trim(
            (
                *prior,
                ChatMessage(role="user", content=user_text.strip()),
                ChatMessage(role="assistant", content=assistant_reply.strip()),
            )
        )
        session = DraftSession(messages=messages)
        self._cache[(room_id, user_id)] = session
        return session

    def delete(self, room_id: str, user_id: str) -> bool:
        """Delete the draft session for (room, user). Returns True if one existed."""
        return self._cache.pop((room_id, user_id), None) is not None

    def _trim(self, messages: tuple[ChatMessage, ...]) -> tuple[ChatMessage, ...]:
        if self._max_messages > 0 and len(messages) > self._max_messages:
            return messages[-self._max_messages :]
        return messages
