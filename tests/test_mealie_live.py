import os

import pytest

from matrix_mealie_chef.mealie import MealieClient
from matrix_mealie_chef.models import Ingredient, Recipe, Step

pytestmark = pytest.mark.skipif(
    os.environ.get("MEALIE_LIVE_TEST") != "1",
    reason="Set MEALIE_LIVE_TEST=1 to run live Mealie smoke tests",
)


async def test_create_recipe_against_live_mealie() -> None:
    mealie_url = os.environ.get("MEALIE_URL", "").strip()
    mealie_token = os.environ.get("MEALIE_API_TOKEN", "").strip()
    assert mealie_url, "MEALIE_URL must be set"
    assert mealie_token, "MEALIE_API_TOKEN must be set"

    recipe = Recipe(
        name="Smoke Test Garlic Pasta",
        description="Automated smoke test recipe from Matrix Mealie Chef.",
        servings="2 servings",
        prep_time="5 min",
        cook_time="15 min",
        total_time="20 min",
        ingredients=[
            Ingredient(quantity=200, unit="g", food="spaghetti"),
            Ingredient(quantity=3, food="garlic cloves", note="thinly sliced"),
            Ingredient(food="salt", note="to taste"),
        ],
        instructions=[
            Step(title="Pasta", text="Cook spaghetti in generously salted boiling water."),
            Step(title="Finish", text="Toss pasta with sautéed garlic and olive oil."),
        ],
        tags=["smoke-test", "pasta"],
        categories=["Dinner"],
    )

    async with MealieClient(mealie_url, mealie_token) as client:
        created = await client.create_recipe(recipe)

    print(f"\nCreated Mealie recipe: {created.url}")
    assert created.slug
    assert created.url.startswith(mealie_url.rstrip("/"))
