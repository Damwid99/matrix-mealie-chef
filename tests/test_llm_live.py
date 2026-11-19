import os

import pytest

from matrix_mealie_chef.llm import RecipeLLMClient

pytestmark = pytest.mark.skipif(
    os.environ.get("LLM_LIVE_TEST") != "1",
    reason="Set LLM_LIVE_TEST=1 to run live LLM smoke tests",
)


async def test_chat_and_extract_against_live_llm() -> None:
    model = os.environ.get("LLM_MODEL", "gemini/gemini-2.5-flash").strip()
    api_key = os.environ.get("LLM_API_KEY", "").strip() or None
    api_base = os.environ.get("LLM_API_BASE", "").strip() or None

    client = RecipeLLMClient(model, api_key=api_key, api_base=api_base)

    prompt1 = "I have eggs, spinach, feta cheese, and cherry tomatoes"
    reply1 = await client.chat(prompt1)
    print(f"\nChat reply 1 ({len(reply1)} chars):\n{reply1[:200]}...")
    assert reply1

    history = [
        {"role": "user", "content": prompt1},
        {"role": "assistant", "content": reply1},
    ]
    prompt2 = "Make it for 4 servings and add a spicy kick"
    reply2 = await client.chat(prompt2, history=history)
    print(f"\nChat reply 2 ({len(reply2)} chars):\n{reply2[:200]}...")
    assert reply2

    history.extend(
        [
            {"role": "user", "content": prompt2},
            {"role": "assistant", "content": reply2},
        ]
    )
    recipe = await client.extract_recipe(history)
    print(f"\nExtracted: {recipe.name} ({recipe.servings}) — tags: {recipe.tags}")
    assert recipe.name
    assert recipe.ingredients
    assert recipe.instructions
