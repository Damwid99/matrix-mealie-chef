import os

import pytest

from matrix_mealie_chef.llm import RecipeLLMClient

pytestmark = pytest.mark.skipif(
    os.environ.get("LLM_LIVE_TEST") != "1",
    reason="Set LLM_LIVE_TEST=1 to run live LLM smoke tests",
)


async def test_generate_and_refine_against_live_llm() -> None:
    model = os.environ.get("LLM_MODEL", "gemini/gemini-2.5-flash").strip()
    api_key = os.environ.get("LLM_API_KEY", "").strip() or None
    api_base = os.environ.get("LLM_API_BASE", "").strip() or None

    client = RecipeLLMClient(model, api_key=api_key, api_base=api_base)

    draft = await client.generate("I have eggs, spinach, feta cheese, and cherry tomatoes")
    print(f"\nGenerated: {draft.name} ({draft.servings}) — {len(draft.ingredients)} ingredients")
    assert draft.name
    assert draft.ingredients
    assert draft.instructions

    refined = await client.refine(
        draft,
        "Make it for 4 servings and add a spicy kick",
        history=["I have eggs, spinach, feta cheese, and cherry tomatoes"],
    )
    print(f"Refined:   {refined.name} ({refined.servings}) — tags: {refined.tags}")
    assert refined.name
    assert refined.ingredients
    assert refined.instructions
