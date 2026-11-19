import json
from types import SimpleNamespace
from typing import Any

import litellm
import pytest

from matrix_mealie_chef import llm as llm_module
from matrix_mealie_chef.llm import (
    LLMAuthError,
    LLMParseError,
    LLMRateLimitError,
    RecipeLLMClient,
)
from matrix_mealie_chef.models import Recipe


def _fake_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _valid_recipe_json(name: str = "Garlic Pasta") -> str:
    return json.dumps(
        {
            "name": name,
            "description": "Simple garlicky spaghetti.",
            "servings": "2 servings",
            "prep_time": "5 min",
            "cook_time": "12 min",
            "total_time": "17 min",
            "ingredients": [
                {"quantity": 200, "unit": "g", "food": "spaghetti", "note": None},
                {"quantity": 3, "unit": None, "food": "garlic cloves", "note": "sliced"},
            ],
            "instructions": [
                {"title": None, "text": "Boil spaghetti in salted water."},
                {"title": None, "text": "Toss with gently sautéed garlic and olive oil."},
            ],
            "tags": ["pasta", "quick"],
            "categories": ["Dinner"],
        }
    )


async def test_chat_sends_plain_markdown_request_without_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> SimpleNamespace:
        captured_kwargs.update(kwargs)
        return _fake_response("### Garlic Pasta\n\nCook pasta with garlic oil.")

    monkeypatch.setattr(llm_module.litellm, "acompletion", fake_acompletion)

    client = RecipeLLMClient(
        "gemini/gemini-2.5-flash",
        api_key="test-key",
        api_base="http://custom.local",
    )
    reply = await client.chat("I have spaghetti and garlic")

    assert reply == "### Garlic Pasta\n\nCook pasta with garlic oil."
    assert captured_kwargs["model"] == "gemini/gemini-2.5-flash"
    assert captured_kwargs["api_key"] == "test-key"
    assert captured_kwargs["api_base"] == "http://custom.local"
    assert captured_kwargs["drop_params"] is True
    assert captured_kwargs["reasoning_effort"] == "low"
    assert "temperature" not in captured_kwargs
    assert "response_format" not in captured_kwargs
    assert captured_kwargs["messages"][0]["role"] == "system"
    assert captured_kwargs["messages"][1] == {
        "role": "user",
        "content": "I have spaghetti and garlic",
    }


async def test_chat_includes_trimmed_history_and_omits_reasoning_for_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> SimpleNamespace:
        captured_kwargs.update(kwargs)
        return _fake_response("### Spicy Garlic Pasta")

    monkeypatch.setattr(llm_module.litellm, "acompletion", fake_acompletion)

    client = RecipeLLMClient("ollama/llama3.1", max_history_messages=2)
    history = [
        {"role": "user", "content": "old user msg"},
        {"role": "assistant", "content": "old assistant msg"},
        {"role": "user", "content": "garlic pasta"},
        {"role": "assistant", "content": "### Garlic Pasta"},
    ]
    reply = await client.chat("make it spicier", history=history)

    assert reply == "### Spicy Garlic Pasta"
    assert "reasoning_effort" not in captured_kwargs
    assert captured_kwargs["messages"][1:] == [
        {"role": "user", "content": "garlic pasta"},
        {"role": "assistant", "content": "### Garlic Pasta"},
        {"role": "user", "content": "make it spicier"},
    ]


async def test_extract_recipe_uses_response_format_and_parses_recipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_kwargs: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> SimpleNamespace:
        captured_kwargs.update(kwargs)
        return _fake_response(_valid_recipe_json("Spicy Garlic Pasta"))

    monkeypatch.setattr(llm_module.litellm, "acompletion", fake_acompletion)

    client = RecipeLLMClient("gemini/gemini-2.5-flash", api_key="test-key")
    history = [
        {"role": "user", "content": "garlic pasta"},
        {"role": "assistant", "content": "### Spicy Garlic Pasta\n\nIngredients: ..."},
    ]
    recipe = await client.extract_recipe(history)

    assert recipe.name == "Spicy Garlic Pasta"
    assert captured_kwargs["response_format"] is Recipe
    assert captured_kwargs["messages"][0]["role"] == "system"
    assert "JSON Schema:" in captured_kwargs["messages"][0]["content"]
    assert captured_kwargs["messages"][1:3] == history
    assert captured_kwargs["messages"][3]["role"] == "user"


async def test_extract_recipe_handles_markdown_fenced_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fenced = f"Sure! Here is the JSON:\n```json\n{_valid_recipe_json('Tomato Soup')}\n```"

    async def fake_acompletion(**_: Any) -> SimpleNamespace:
        return _fake_response(fenced)

    monkeypatch.setattr(llm_module.litellm, "acompletion", fake_acompletion)

    client = RecipeLLMClient("ollama/llama3.1")
    recipe = await client.extract_recipe([{"role": "assistant", "content": "### Tomato Soup"}])

    assert recipe.name == "Tomato Soup"


async def test_extract_recipe_falls_back_when_response_format_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_acompletion(**kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        if "response_format" in kwargs:
            raise litellm.UnsupportedParamsError(
                message="response_format not supported",
                llm_provider="ollama",
                model="ollama/llama3.1",
            )
        return _fake_response(_valid_recipe_json("Omelette"))

    monkeypatch.setattr(llm_module.litellm, "acompletion", fake_acompletion)

    client = RecipeLLMClient("ollama/llama3.1")
    recipe = await client.extract_recipe([{"role": "assistant", "content": "### Omelette"}])

    assert recipe.name == "Omelette"
    assert len(calls) == 2
    assert "response_format" in calls[0]
    assert "response_format" not in calls[1]


async def test_extract_recipe_retries_on_validation_error_with_feedback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[dict[str, str]]] = []
    responses = [
        _fake_response(json.dumps({"name": "Broken", "ingredients": [], "instructions": []})),
        _fake_response(_valid_recipe_json("Fixed Dish")),
    ]

    async def fake_acompletion(**kwargs: Any) -> SimpleNamespace:
        calls.append(list(kwargs["messages"]))
        return responses.pop(0)

    monkeypatch.setattr(llm_module.litellm, "acompletion", fake_acompletion)

    client = RecipeLLMClient("gemini/gemini-2.5-flash")
    recipe = await client.extract_recipe([{"role": "assistant", "content": "### Fixed Dish"}])

    assert recipe.name == "Fixed Dish"
    assert len(calls) == 2
    assert calls[1][-2]["role"] == "assistant"
    assert "not valid Recipe JSON" in calls[1][-1]["content"]


async def test_extract_recipe_raises_parse_error_after_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    async def fake_acompletion(**_: Any) -> SimpleNamespace:
        nonlocal call_count
        call_count += 1
        return _fake_response("definitely not json")

    monkeypatch.setattr(llm_module.litellm, "acompletion", fake_acompletion)

    client = RecipeLLMClient("gemini/gemini-2.5-flash", max_validation_retries=2)
    with pytest.raises(LLMParseError):
        await client.extract_recipe([{"role": "assistant", "content": "just chatting"}])

    assert call_count == 3


async def test_chat_maps_auth_and_rate_limit_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def raise_auth(**_: Any) -> SimpleNamespace:
        raise litellm.AuthenticationError(
            message="invalid key",
            llm_provider="gemini",
            model="gemini/gemini-2.5-flash",
        )

    monkeypatch.setattr(llm_module.litellm, "acompletion", raise_auth)
    client = RecipeLLMClient("gemini/gemini-2.5-flash")
    with pytest.raises(LLMAuthError):
        await client.chat("pasta")

    async def raise_rate_limit(**_: Any) -> SimpleNamespace:
        raise litellm.RateLimitError(
            message="quota exceeded",
            llm_provider="gemini",
            model="gemini/gemini-2.5-flash",
        )

    monkeypatch.setattr(llm_module.litellm, "acompletion", raise_rate_limit)
    with pytest.raises(LLMRateLimitError):
        await client.chat("pasta")
