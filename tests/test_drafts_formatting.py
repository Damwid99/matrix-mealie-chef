from matrix_mealie_chef.drafts import DraftStore
from matrix_mealie_chef.formatting import (
    format_help,
    format_recipe_preview,
    format_saved_recipe,
)
from matrix_mealie_chef.mealie import CreatedRecipe
from matrix_mealie_chef.models import Ingredient, Recipe, Step


def _sample_recipe(name: str = "Garlic Pasta") -> Recipe:
    return Recipe(
        name=name,
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
        tags=["pasta", "quick"],
        categories=["Dinner"],
    )


def test_draft_store_isolates_by_room_and_user() -> None:
    store = DraftStore(ttl_seconds=60)
    r1 = _sample_recipe("Pasta")
    r2 = _sample_recipe("Soup")

    store.set("!room1:ex", "@alice:ex", r1, "pasta idea")
    store.set("!room1:ex", "@bob:ex", r2, "soup idea")

    alice_draft = store.get("!room1:ex", "@alice:ex")
    bob_draft = store.get("!room1:ex", "@bob:ex")
    assert alice_draft is not None
    assert bob_draft is not None
    assert alice_draft.recipe.name == "Pasta"
    assert bob_draft.recipe.name == "Soup"
    assert store.get("!room2:ex", "@alice:ex") is None


def test_draft_store_update_appends_and_caps_history() -> None:
    store = DraftStore(ttl_seconds=60, max_history=2)
    store.set("!r:ex", "@u:ex", _sample_recipe("V1"), "first")
    store.update("!r:ex", "@u:ex", _sample_recipe("V2"), "second")
    draft = store.update("!r:ex", "@u:ex", _sample_recipe("V3"), "third")

    assert draft.recipe.name == "V3"
    assert draft.history == ("second", "third")
    assert store.delete("!r:ex", "@u:ex") is True
    assert store.delete("!r:ex", "@u:ex") is False


def test_draft_store_ttl_expiration_and_refresh() -> None:
    now = 100.0

    def fake_clock() -> float:
        return now

    store = DraftStore(ttl_seconds=10.0, timer=fake_clock)
    store.set("!r:ex", "@u:ex", _sample_recipe("V1"), "initial")

    now = 108.0
    store.update("!r:ex", "@u:ex", _sample_recipe("V2"), "tweak")

    now = 115.0  # 7s after update -> still alive
    assert store.has("!r:ex", "@u:ex") is True

    now = 119.0  # 11s after update -> expired
    assert store.get("!r:ex", "@u:ex") is None


def test_format_recipe_preview_renders_markdown_and_html() -> None:
    msg = format_recipe_preview(_sample_recipe())

    assert "### Garlic Pasta" in msg.body
    assert "- 200 g spaghetti" in msg.body
    assert "1. **Boil:** Cook spaghetti in salted water." in msg.body
    assert "2. Toss with garlic oil." in msg.body
    assert "!save" in msg.body

    assert "<h3>Garlic Pasta</h3>" in msg.formatted_body
    assert "<ul>" in msg.formatted_body
    assert "<li>200 g spaghetti</li>" in msg.formatted_body
    assert "<ol>" in msg.formatted_body
    assert "<code>pasta</code>" in msg.formatted_body


def test_format_recipe_preview_omits_optional_sections_and_footer() -> None:
    minimal = Recipe(
        name="Toast",
        ingredients=[Ingredient(quantity=2, food="bread slices")],
        instructions=[Step(text="Toast until golden.")],
    )
    msg = format_recipe_preview(minimal, include_footer=False)

    assert "**Servings:**" not in msg.body
    assert "**Tags:**" not in msg.body
    assert "**Categories:**" not in msg.body
    assert "!save" not in msg.body


def test_format_saved_recipe_and_help() -> None:
    created = CreatedRecipe(
        slug="garlic-pasta",
        name="Garlic Pasta",
        url="http://mealie.test/g/home/r/garlic-pasta",
    )
    saved = format_saved_recipe(created)
    assert "Garlic Pasta" in saved.body
    assert 'href="http://mealie.test/g/home/r/garlic-pasta"' in saved.formatted_body

    help_msg = format_help()
    assert "!save" in help_msg.body
    assert "<ul>" in help_msg.formatted_body
