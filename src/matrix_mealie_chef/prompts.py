"""System prompts and message builders for recipe LLM calls."""

import json
from collections.abc import Sequence

from matrix_mealie_chef.models import Recipe

_SCHEMA_JSON = json.dumps(Recipe.llm_json_schema(), indent=2)

SYSTEM_PROMPT = f"""\
You are Matrix Mealie Chef, a practical and creative culinary assistant.
Your job is to turn user notes, leftover ingredients, or rough meal ideas into a complete,
well-proportioned, cookable recipe formatted strictly as JSON.

Rules:
1. Language: ALWAYS write all text fields (name, description, servings, times, ingredients,
   instructions, tags, categories) in the same language as the user's message, unless the user
   explicitly asks for a different language.
2. Units & proportions: Use metric units (g, ml, kg, l, tbsp/tsp or natural local equivalents)
   by default, unless the user writes in imperial units. Include sensible pantry basics
   (oil, salt, pepper, water) when needed so the recipe is genuinely cookable.
3. Ingredients: Split each ingredient into numeric `quantity` (or null for "to taste"),
   `unit` (or null for countable items like eggs), `food` (concise ingredient name), and
   optional `note` (prep detail such as "diced" or "to taste").
4. Instructions: Write clear, chronological, actionable steps. Only add a step `title` when
   grouping a distinct sub-part (e.g. "Sauce", "Assembly").
5. Times & servings: Provide realistic `servings`, `prep_time`, `cook_time`, and `total_time`
   in the recipe's language (e.g. "15 min", "4 servings").
6. Tags & categories: Add 2-5 concise, useful tags (e.g. "quick", "pasta", "vegetarian") and
   1-2 broad categories (e.g. "Dinner", "Breakfast") in the recipe's language.
7. Output format: Return ONLY a single valid JSON object matching the JSON Schema below.
   Do not wrap it in markdown code fences or add any explanation outside the JSON.

JSON Schema:
{_SCHEMA_JSON}
"""


def build_generate_messages(user_input: str) -> list[dict[str, str]]:
    """Build chat messages to generate a fresh recipe draft from free-form text."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_input.strip()},
    ]


REFINE_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + """
Refinement rules:
8. You are updating an EXISTING recipe draft based on the user's feedback.
9. Apply the requested changes accurately (e.g. swap ingredients, scale quantities and
   servings proportionally, adjust spiciness, update tags or times) while preserving all
   other details of the current recipe unless they conflict with the change.
10. Keep the recipe in the same language as the current draft unless the user explicitly asks
    to translate or switch languages.
11. Always return the COMPLETE updated recipe JSON (not a partial diff).
"""
)


def build_refine_messages(
    current: Recipe,
    instruction: str,
    history: Sequence[str] | None = None,
    *,
    max_history: int = 6,
) -> list[dict[str, str]]:
    """Build chat messages to refine an existing recipe draft."""
    sections = [
        "Current recipe (JSON):",
        current.model_dump_json(indent=2),
    ]

    if history and max_history > 0:
        cleaned = [item.strip() for item in history if item.strip()]
        trimmed = cleaned[-max_history:]
        if trimmed:
            history_lines = "\n".join(f"- {item}" for item in trimmed)
            sections.append(
                f"Earlier requests in this session (oldest to newest):\n{history_lines}"
            )

    sections.append(f"Requested change:\n{instruction.strip()}")

    return [
        {"role": "system", "content": REFINE_SYSTEM_PROMPT},
        {"role": "user", "content": "\n\n".join(sections)},
    ]
