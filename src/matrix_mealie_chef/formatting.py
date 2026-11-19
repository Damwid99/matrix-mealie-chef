"""Markdown and HTML message formatting for Matrix replies."""

from dataclasses import dataclass

import markdown

from matrix_mealie_chef.mealie import CreatedRecipe
from matrix_mealie_chef.models import Recipe

_FOOTER = (
    "---\n"
    "*Reply with any tweaks to refine this draft, `!save` to save to Mealie, "
    "`!discard` to cancel, or `!new <idea>` to start over.*"
)

_HELP_TEXT = """\
### Matrix Mealie Chef

Send me ingredients, a rough dinner idea, or messy recipe notes and I will draft a structured recipe for you.

Once a draft is open, reply naturally to refine it (e.g. *"double the servings"*, *"make it vegan"*, *"swap butter for olive oil"*).

**Commands**

- `!save` — save the current draft to Mealie
- `!show` — display the current draft again
- `!discard` (or `!cancel`) — throw away the current draft
- `!new <idea>` — discard any active draft and generate a fresh recipe
- `!help` — show this message
"""


@dataclass(frozen=True, slots=True)
class FormattedMessage:
    """Message payload with plain-text Markdown and Matrix HTML."""

    body: str
    formatted_body: str


def markdown_to_html(text: str) -> str:
    """Render Markdown to clean HTML suitable for Matrix `org.matrix.custom.html`."""
    return markdown.markdown(text, extensions=["sane_lists"])


def format_markdown(text: str) -> FormattedMessage:
    """Wrap a Markdown string as a FormattedMessage."""
    return FormattedMessage(body=text, formatted_body=markdown_to_html(text))


def format_recipe_preview(recipe: Recipe, *, include_footer: bool = True) -> FormattedMessage:
    """Format a Recipe draft as a readable Markdown + HTML preview."""
    blocks: list[str] = [f"### {recipe.name}"]

    if recipe.description:
        blocks.append(f"*{recipe.description}*")

    meta: list[str] = []
    if recipe.servings:
        meta.append(f"**Servings:** {recipe.servings}")
    if recipe.prep_time:
        meta.append(f"**Prep:** {recipe.prep_time}")
    if recipe.cook_time:
        meta.append(f"**Cook:** {recipe.cook_time}")
    if recipe.total_time:
        meta.append(f"**Total:** {recipe.total_time}")
    if meta:
        blocks.append(" | ".join(meta))

    ingredient_lines = "\n".join(f"- {ing.to_display_text()}" for ing in recipe.ingredients)
    blocks.append(f"**Ingredients**\n\n{ingredient_lines}")

    step_lines = []
    for index, step in enumerate(recipe.instructions, start=1):
        if step.title:
            step_lines.append(f"{index}. **{step.title}:** {step.text}")
        else:
            step_lines.append(f"{index}. {step.text}")
    blocks.append("**Instructions**\n\n" + "\n".join(step_lines))

    if recipe.tags:
        tags_str = ", ".join(f"`{tag}`" for tag in recipe.tags)
        blocks.append(f"**Tags:** {tags_str}")

    if recipe.categories:
        categories_str = ", ".join(recipe.categories)
        blocks.append(f"**Categories:** {categories_str}")

    if include_footer:
        blocks.append(_FOOTER)

    return format_markdown("\n\n".join(blocks))


def format_saved_recipe(created: CreatedRecipe) -> FormattedMessage:
    """Format confirmation reply after saving a recipe to Mealie."""
    md = f"Saved **{created.name}** to Mealie!\n\n[{created.url}]({created.url})"
    return format_markdown(md)


def format_help() -> FormattedMessage:
    """Format the `!help` usage guide."""
    return format_markdown(_HELP_TEXT.strip())
