import json

import httpx
import pytest
import respx

from matrix_mealie_chef.mealie import (
    CreatedRecipe,
    MealieAuthError,
    MealieClient,
    MealieRequestError,
)
from matrix_mealie_chef.models import Ingredient, Recipe, Step

BASE_URL = "http://mealie.test"


def _sample_recipe() -> Recipe:
    return Recipe(
        name="Garlic Pasta",
        description="Quick weeknight pasta.",
        servings="2 servings",
        prep_time="5 min",
        cook_time="15 min",
        total_time="20 min",
        ingredients=[
            Ingredient(quantity=200, unit="g", food="spaghetti"),
            Ingredient(food="salt", note="to taste"),
        ],
        instructions=[
            Step(title="Boil", text="Cook spaghetti in salted water."),
            Step(text="Toss with garlic oil."),
        ],
        tags=["Pasta", "Quick"],
        categories=["Dinner"],
    )


async def test_create_recipe_success() -> None:
    with respx.mock(base_url=BASE_URL) as router:
        create_route = router.post("/api/recipes").mock(
            return_value=httpx.Response(201, json="garlic-pasta")
        )
        router.get("/api/organizers/tags").mock(
            return_value=httpx.Response(
                200,
                json={"items": [{"id": "t1", "name": "pasta", "slug": "pasta"}]},
            )
        )
        create_tag_route = router.post("/api/organizers/tags").mock(
            return_value=httpx.Response(201, json={"id": "t2", "name": "Quick", "slug": "quick"})
        )
        router.get("/api/organizers/categories").mock(
            return_value=httpx.Response(200, json={"items": []})
        )
        router.post("/api/organizers/categories").mock(
            return_value=httpx.Response(201, json={"id": "c1", "name": "Dinner", "slug": "dinner"})
        )
        patch_route = router.patch("/api/recipes/garlic-pasta").mock(
            return_value=httpx.Response(200, json={})
        )
        router.get("/api/groups/self").mock(return_value=httpx.Response(200, json={"slug": "home"}))

        async with MealieClient(BASE_URL, "secret-token", retry_base_wait=0.0) as client:
            result = await client.create_recipe(_sample_recipe())

    assert result == CreatedRecipe(
        slug="garlic-pasta",
        name="Garlic Pasta",
        url=f"{BASE_URL}/g/home/r/garlic-pasta",
    )
    assert create_route.calls[0].request.headers["Authorization"] == "Bearer secret-token"
    assert create_tag_route.call_count == 1

    patch_body = json.loads(patch_route.calls[0].request.content)
    assert patch_body["description"] == "Quick weeknight pasta."
    assert patch_body["recipeYield"] == "2 servings"
    assert patch_body["prepTime"] == "5 min"
    assert patch_body["performTime"] == "15 min"
    assert patch_body["totalTime"] == "20 min"
    assert [i["display"] for i in patch_body["recipeIngredient"]] == [
        "200 g spaghetti",
        "salt (to taste)",
    ]
    assert all(i["disableAmount"] is True for i in patch_body["recipeIngredient"])
    assert [(s["title"], s["text"]) for s in patch_body["recipeInstructions"]] == [
        ("Boil", "Cook spaghetti in salted water."),
        ("", "Toss with garlic oil."),
    ]
    assert [t["slug"] for t in patch_body["tags"]] == ["pasta", "quick"]
    assert [c["slug"] for c in patch_body["recipeCategory"]] == ["dinner"]


async def test_organizer_and_group_slug_are_cached() -> None:
    recipe = Recipe(
        name="Simple Eggs",
        ingredients=[Ingredient(quantity=2, food="eggs")],
        instructions=[Step(text="Scramble.")],
        tags=["Quick"],
    )

    with respx.mock(base_url=BASE_URL) as router:
        router.post("/api/recipes").mock(
            side_effect=[
                httpx.Response(201, json="simple-eggs"),
                httpx.Response(201, json="simple-eggs-1"),
            ]
        )
        tags_get = router.get("/api/organizers/tags").mock(
            return_value=httpx.Response(
                200,
                json={"items": [{"id": "t1", "name": "Quick", "slug": "quick"}]},
            )
        )
        router.patch("/api/recipes/simple-eggs").mock(return_value=httpx.Response(200, json={}))
        router.patch("/api/recipes/simple-eggs-1").mock(return_value=httpx.Response(200, json={}))
        group_get = router.get("/api/groups/self").mock(
            return_value=httpx.Response(200, json={"slug": "family"})
        )

        async with MealieClient(BASE_URL, "token", retry_base_wait=0.0) as client:
            first = await client.create_recipe(recipe)
            second = await client.create_recipe(recipe)

    assert first.url == f"{BASE_URL}/g/family/r/simple-eggs"
    assert second.url == f"{BASE_URL}/g/family/r/simple-eggs-1"
    assert tags_get.call_count == 1
    assert group_get.call_count == 1


async def test_auth_error_is_not_retried() -> None:
    with respx.mock(base_url=BASE_URL) as router:
        route = router.post("/api/recipes").mock(return_value=httpx.Response(401, text="denied"))

        async with MealieClient(BASE_URL, "bad-token", retry_base_wait=0.0) as client:
            with pytest.raises(MealieAuthError):
                await client.create_recipe(_sample_recipe())

    assert route.call_count == 1


async def test_server_error_retries_then_succeeds() -> None:
    recipe = Recipe(
        name="Toast",
        ingredients=[Ingredient(quantity=2, food="bread slices")],
        instructions=[Step(text="Toast until golden.")],
    )

    with respx.mock(base_url=BASE_URL) as router:
        create_route = router.post("/api/recipes").mock(
            side_effect=[
                httpx.Response(502, text="bad gateway"),
                httpx.Response(201, json="toast"),
            ]
        )
        router.patch("/api/recipes/toast").mock(return_value=httpx.Response(200, json={}))
        router.get("/api/groups/self").mock(return_value=httpx.Response(200, json={"slug": "home"}))

        async with MealieClient(BASE_URL, "token", retry_base_wait=0.0) as client:
            result = await client.create_recipe(recipe)

    assert create_route.call_count == 2
    assert result.slug == "toast"


async def test_server_error_exhausts_retries() -> None:
    with respx.mock(base_url=BASE_URL) as router:
        route = router.post("/api/recipes").mock(
            return_value=httpx.Response(503, text="unavailable")
        )

        async with MealieClient(BASE_URL, "token", retry_attempts=3, retry_base_wait=0.0) as client:
            with pytest.raises(MealieRequestError) as exc_info:
                await client.create_recipe(_sample_recipe())

    assert route.call_count == 3
    assert exc_info.value.status_code == 503
