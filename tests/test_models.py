import pytest
from pydantic import ValidationError

from matrix_mealie_chef.models import Ingredient, Recipe, Step


def test_valid_recipe_normalization() -> None:
    recipe = Recipe.model_validate(
        {
            "name": "  Garlic Pasta  ",
            "description": "Quick weeknight pasta.",
            "servings": "2 servings",
            "prep_time": "10 min",
            "cook_time": "15 min",
            "total_time": "   ",
            "ingredients": [
                {"quantity": 200, "unit": "g", "food": "spaghetti", "note": ""},
                {"quantity": "", "unit": " ", "food": "salt", "note": "to taste"},
            ],
            "instructions": [
                {"title": "Boil", "text": "Cook pasta in salted water."},
                "Toss with garlic oil and serve.",
            ],
            "tags": [" pasta ", "Quick", "pasta", "", "  "],
            "categories": ["Dinner", "dinner"],
        }
    )

    assert recipe.name == "Garlic Pasta"
    assert recipe.total_time is None
    assert recipe.ingredients[0].note is None
    assert recipe.ingredients[1].quantity is None
    assert recipe.ingredients[1].unit is None
    assert recipe.instructions[0] == Step(title="Boil", text="Cook pasta in salted water.")
    assert recipe.instructions[1] == Step(title=None, text="Toss with garlic oil and serve.")
    assert recipe.tags == ["pasta", "Quick"]
    assert recipe.categories == ["Dinner"]


@pytest.mark.parametrize(
    ("ingredient", "expected"),
    [
        (Ingredient(quantity=200.0, unit="g", food="flour", note="sifted"), "200 g flour (sifted)"),
        (Ingredient(quantity=0.5, unit="tsp", food="pepper"), "0.5 tsp pepper"),
        (Ingredient(quantity=2.0, food="eggs"), "2 eggs"),
        (Ingredient(food="salt", note="to taste"), "salt (to taste)"),
    ],
)
def test_ingredient_to_display_text(ingredient: Ingredient, expected: str) -> None:
    assert ingredient.to_display_text() == expected


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "   "},
        {"ingredients": []},
        {"instructions": []},
        {"ingredients": [{"quantity": 0, "food": "salt"}]},
        {"ingredients": [{"quantity": -1, "food": "salt"}]},
        {"ingredients": [{"food": "   "}]},
        {"instructions": [{"text": "   "}]},
    ],
)
def test_invalid_recipe_raises_validation_error(overrides: dict[str, object]) -> None:
    payload: dict[str, object] = {
        "name": "Omelette",
        "ingredients": [{"quantity": 2, "food": "eggs"}],
        "instructions": [{"text": "Whisk and fry."}],
    }
    payload.update(overrides)

    with pytest.raises(ValidationError):
        Recipe.model_validate(payload)


def test_llm_json_schema_contains_core_fields() -> None:
    schema = Recipe.llm_json_schema()

    assert schema["title"] == "Recipe"
    assert "name" in schema["properties"]
    assert "ingredients" in schema["properties"]
    assert "instructions" in schema["properties"]
    assert "$defs" in schema
    assert "Ingredient" in schema["$defs"]
    assert "Step" in schema["$defs"]
