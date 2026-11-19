from collections.abc import Sequence

import pytest

from matrix_mealie_chef.drafts import DraftStore
from matrix_mealie_chef.handlers import ConversationHandler
from matrix_mealie_chef.llm import LLMAuthError, LLMError, LLMParseError, LLMRateLimitError
from matrix_mealie_chef.mealie import CreatedRecipe, MealieAuthError, MealieRequestError
from matrix_mealie_chef.models import Ingredient, Recipe, Step

ROOM = "!room:example.com"
USER = "@alice:example.com"


def _make_recipe(name: str) -> Recipe:
    return Recipe(
        name=name,
        description=f"Tasty {name.lower()}.",
        servings="2 servings",
        ingredients=[Ingredient(quantity=200, unit="g", food="pasta")],
        instructions=[Step(text="Cook and serve.")],
        tags=["quick"],
    )


class FakeLLM:
    def __init__(
        self,
        *,
        generate_result: Recipe | None = None,
        refine_result: Recipe | None = None,
        error: Exception | None = None,
    ) -> None:
        self.generate_result = generate_result or _make_recipe("Garlic Pasta")
        self.refine_result = refine_result or _make_recipe("Spicy Garlic Pasta")
        self.error = error
        self.generate_calls: list[str] = []
        self.refine_calls: list[tuple[Recipe, str, tuple[str, ...]]] = []

    async def generate(self, user_input: str) -> Recipe:
        self.generate_calls.append(user_input)
        if self.error is not None:
            raise self.error
        return self.generate_result

    async def refine(
        self,
        current: Recipe,
        instruction: str,
        history: Sequence[str] | None = None,
    ) -> Recipe:
        self.refine_calls.append((current, instruction, tuple(history or ())))
        if self.error is not None:
            raise self.error
        return self.refine_result


class FakeMealie:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.saved_recipes: list[Recipe] = []

    async def create_recipe(self, recipe: Recipe) -> CreatedRecipe:
        self.saved_recipes.append(recipe)
        if self.error is not None:
            raise self.error
        slug = recipe.name.lower().replace(" ", "-")
        return CreatedRecipe(
            slug=slug,
            name=recipe.name,
            url=f"http://mealie.test/g/home/r/{slug}",
        )


async def test_full_generate_refine_save_journey() -> None:
    llm = FakeLLM()
    mealie = FakeMealie()
    drafts = DraftStore()
    handler = ConversationHandler(llm, mealie, drafts)

    # 1. Generate initial draft
    reply1 = await handler.handle_message(ROOM, USER, "spaghetti and garlic")
    assert reply1 is not None
    assert "### Garlic Pasta" in reply1.body
    assert llm.generate_calls == ["spaghetti and garlic"]
    assert drafts.has(ROOM, USER)

    # 2. Refine existing draft
    reply2 = await handler.handle_message(ROOM, USER, "make it spicy")
    assert reply2 is not None
    assert "### Spicy Garlic Pasta" in reply2.body
    assert len(llm.refine_calls) == 1
    assert llm.refine_calls[0][0].name == "Garlic Pasta"
    assert llm.refine_calls[0][1] == "make it spicy"
    assert llm.refine_calls[0][2] == ("spaghetti and garlic",)

    # 3. Save draft to Mealie
    reply3 = await handler.handle_message(ROOM, USER, "!save")
    assert reply3 is not None
    assert "Spicy Garlic Pasta" in reply3.body
    assert "http://mealie.test/g/home/r/spicy-garlic-pasta" in reply3.body
    assert len(mealie.saved_recipes) == 1
    assert not drafts.has(ROOM, USER)


async def test_commands_help_show_discard_new_and_unknown() -> None:
    llm = FakeLLM(generate_result=_make_recipe("Tomato Soup"))
    mealie = FakeMealie()
    drafts = DraftStore()
    handler = ConversationHandler(llm, mealie, drafts)

    assert await handler.handle_message(ROOM, USER, "   ") is None

    help_reply = await handler.handle_message(ROOM, USER, "!HELP")
    assert help_reply is not None
    assert "!save" in help_reply.body

    show_empty = await handler.handle_message(ROOM, USER, "!show")
    assert show_empty is not None
    assert "do not have an active recipe draft" in show_empty.body

    discard_empty = await handler.handle_message(ROOM, USER, "!discard")
    assert discard_empty is not None
    assert "no active draft" in discard_empty.body

    save_empty = await handler.handle_message(ROOM, USER, "!save")
    assert save_empty is not None
    assert "Nothing to save" in save_empty.body

    new_empty = await handler.handle_message(ROOM, USER, "!new")
    assert new_empty is not None
    assert "Usage:" in new_empty.body

    # Start with !new, verify !show, guard against typo, then !cancel
    new_reply = await handler.handle_message(ROOM, USER, "!new tomato soup")
    assert new_reply is not None
    assert "### Tomato Soup" in new_reply.body

    show_reply = await handler.handle_message(ROOM, USER, "!show")
    assert show_reply is not None
    assert "### Tomato Soup" in show_reply.body

    unknown_reply = await handler.handle_message(ROOM, USER, "!svae")
    assert unknown_reply is not None
    assert "Unknown command `!svae`" in unknown_reply.body
    assert llm.refine_calls == []

    cancel_reply = await handler.handle_message(ROOM, USER, "!cancel")
    assert cancel_reply is not None
    assert "Draft discarded" in cancel_reply.body
    assert not drafts.has(ROOM, USER)


async def test_save_failure_preserves_draft() -> None:
    llm = FakeLLM()
    mealie = FakeMealie(error=MealieRequestError("server down", status_code=503))
    drafts = DraftStore()
    handler = ConversationHandler(llm, mealie, drafts)

    await handler.handle_message(ROOM, USER, "garlic pasta")
    reply = await handler.handle_message(ROOM, USER, "!save")

    assert reply is not None
    assert "Could not save to Mealie" in reply.body
    assert drafts.has(ROOM, USER)

    mealie.error = MealieAuthError("bad token")
    auth_reply = await handler.handle_message(ROOM, USER, "!save")
    assert auth_reply is not None
    assert "authentication failed" in auth_reply.body
    assert drafts.has(ROOM, USER)


@pytest.mark.parametrize(
    ("error", "expected_fragment"),
    [
        (LLMAuthError("bad key"), "rejected the API key"),
        (LLMRateLimitError("429"), "rate limit"),
        (LLMParseError("bad json"), "trouble structuring"),
        (LLMError("boom"), "Something went wrong"),
    ],
)
async def test_llm_errors_return_friendly_message_and_keep_draft(
    error: Exception,
    expected_fragment: str,
) -> None:
    llm = FakeLLM()
    mealie = FakeMealie()
    drafts = DraftStore()
    handler = ConversationHandler(llm, mealie, drafts)

    await handler.handle_message(ROOM, USER, "garlic pasta")
    llm.error = error
    reply = await handler.handle_message(ROOM, USER, "make it vegan")

    assert reply is not None
    assert expected_fragment in reply.body
    existing = drafts.get(ROOM, USER)
    assert existing is not None
    assert existing.recipe.name == "Garlic Pasta"
