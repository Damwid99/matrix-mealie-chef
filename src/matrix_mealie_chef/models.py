"""Domain models for recipes."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _normalize_labels(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            result.append(item)
            continue
        cleaned = item.strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


class Ingredient(BaseModel):
    """A single structured recipe ingredient."""

    model_config = ConfigDict(str_strip_whitespace=True)

    quantity: float | None = Field(
        default=None,
        gt=0,
        description="Numeric amount, or null if unspecified (e.g. to taste).",
    )
    unit: str | None = Field(
        default=None,
        description="Measurement unit (e.g. g, ml, tbsp), or null for countable items.",
    )
    food: str = Field(
        min_length=1,
        description="Ingredient name (e.g. olive oil, eggs, garlic).",
    )
    note: str | None = Field(
        default=None,
        description="Optional preparation note (e.g. finely chopped, to taste).",
    )

    @field_validator("quantity", "unit", "note", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        return _blank_to_none(value)

    def to_display_text(self) -> str:
        """Render the ingredient as a clean human-readable line."""
        parts: list[str] = []
        if self.quantity is not None:
            qty = str(int(self.quantity)) if self.quantity.is_integer() else f"{self.quantity:g}"
            parts.append(qty)
        if self.unit:
            parts.append(self.unit)
        parts.append(self.food)
        text = " ".join(parts)
        if self.note:
            text = f"{text} ({self.note})"
        return text


class Step(BaseModel):
    """A single recipe preparation instruction."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(
        default=None,
        description="Optional short section heading for this step.",
    )
    text: str = Field(
        min_length=1,
        description="Clear instruction text for this step.",
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_str(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"text": value}
        return value

    @field_validator("title", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        return _blank_to_none(value)


class Recipe(BaseModel):
    """Structured recipe produced by the LLM and saved to Mealie."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, description="Recipe title.")
    description: str = Field(
        default="",
        description="Short, appetizing 1-2 sentence summary of the dish.",
    )
    servings: str | None = Field(
        default=None,
        description="Yield or number of servings (e.g. 4 servings, 12 muffins).",
    )
    prep_time: str | None = Field(
        default=None,
        description="Preparation time (e.g. 15 min).",
    )
    cook_time: str | None = Field(
        default=None,
        description="Cooking or baking time (e.g. 30 min).",
    )
    total_time: str | None = Field(
        default=None,
        description="Total time from start to finish (e.g. 45 min).",
    )
    ingredients: list[Ingredient] = Field(
        min_length=1,
        description="Ordered list of recipe ingredients.",
    )
    instructions: list[Step] = Field(
        min_length=1,
        description="Ordered list of step-by-step cooking instructions.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Concise descriptive tags (e.g. quick, vegetarian, pasta).",
    )
    categories: list[str] = Field(
        default_factory=list,
        description="Optional broader meal categories (e.g. Dinner, Breakfast).",
    )

    @field_validator("servings", "prep_time", "cook_time", "total_time", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        return _blank_to_none(value)

    @field_validator("tags", "categories", mode="before")
    @classmethod
    def _clean_labels(cls, value: Any) -> Any:
        return _normalize_labels(value)

    @classmethod
    def llm_json_schema(cls) -> dict[str, Any]:
        """Return the JSON Schema used for structured LLM output."""
        return cls.model_json_schema()
