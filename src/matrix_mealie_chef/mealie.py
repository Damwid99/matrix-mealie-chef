"""Async REST client for the Mealie API."""

from dataclasses import dataclass
from typing import Any, Self
from uuid import uuid4

import httpx
import structlog
from cachetools import TTLCache
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from matrix_mealie_chef.models import Ingredient, Recipe

logger = structlog.get_logger(__name__)


class MealieError(Exception):
    """Base exception for Mealie client errors."""


class MealieAuthError(MealieError):
    """Raised when Mealie rejects the API token (401/403)."""


class MealieRequestError(MealieError):
    """Raised when a Mealie API request fails."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _RetryableStatusError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"HTTP {status_code}: {detail}")
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class MealieOrganizer:
    """A Mealie tag or category record."""

    id: str
    name: str
    slug: str

    def to_payload(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "slug": self.slug}


@dataclass(frozen=True, slots=True)
class MealieEntity:
    """A Mealie unit or food record."""

    id: str
    name: str

    def to_payload(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name}


@dataclass(frozen=True, slots=True)
class CreatedRecipe:
    """Reference to a newly created recipe in Mealie."""

    slug: str
    name: str
    url: str


def _lookup_keys(text: str) -> set[str]:
    cleaned = text.strip().casefold()
    if not cleaned:
        return set()
    no_dot = cleaned.rstrip(".")
    return {cleaned, no_dot} if no_dot else {cleaned}


def _extract_entity_keys(item: dict[str, Any]) -> set[str]:
    """Collect all case-insensitive match keys (name, plurals, abbreviations, aliases)."""
    keys: set[str] = set()
    for field in ("name", "pluralName", "abbreviation", "pluralAbbreviation"):
        val = item.get(field)
        if isinstance(val, str):
            keys.update(_lookup_keys(val))

    aliases = item.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            if isinstance(alias, str):
                keys.update(_lookup_keys(alias))
            elif isinstance(alias, dict):
                alias_name = alias.get("name")
                if isinstance(alias_name, str):
                    keys.update(_lookup_keys(alias_name))
    return keys


def _parse_entity(item: Any) -> MealieEntity | None:
    if (
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("name"), str)
    ):
        return MealieEntity(id=item["id"], name=item["name"])
    return None


class MealieClient:
    """Async client for creating structured recipes in Mealie."""

    def __init__(
        self,
        base_url: str,
        api_token: str,
        *,
        timeout: float = 15.0,
        cache_ttl: float = 300.0,
        retry_attempts: int = 3,
        retry_base_wait: float = 0.5,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._retry_attempts = retry_attempts
        self._retry_base_wait = retry_base_wait
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        self._organizer_cache: TTLCache[str, dict[str, MealieOrganizer]] = TTLCache(
            maxsize=4, ttl=cache_ttl
        )
        self._units_cache: TTLCache[str, dict[str, MealieEntity]] = TTLCache(
            maxsize=1, ttl=cache_ttl
        )
        self._food_cache: TTLCache[str, MealieEntity] = TTLCache(maxsize=2048, ttl=cache_ttl)
        self._group_slug_cache: TTLCache[str, str] = TTLCache(maxsize=1, ttl=cache_ttl)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client if owned by this instance."""
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._retry_attempts),
                wait=wait_exponential(
                    multiplier=self._retry_base_wait,
                    min=self._retry_base_wait,
                    max=self._retry_base_wait * 8,
                ),
                retry=retry_if_exception_type(
                    (httpx.TimeoutException, httpx.NetworkError, _RetryableStatusError)
                ),
                reraise=True,
            ):
                with attempt:
                    response = await self._client.request(method, path, json=json, params=params)
                    if response.status_code in (401, 403):
                        raise MealieAuthError(
                            f"Mealie authentication failed (HTTP {response.status_code})"
                        )
                    if response.status_code == 429 or response.status_code >= 500:
                        raise _RetryableStatusError(response.status_code, response.text[:200])
                    if response.status_code >= 400:
                        raise MealieRequestError(
                            f"Mealie API error {response.status_code} on {method} {path}: "
                            f"{response.text[:200]}",
                            status_code=response.status_code,
                        )
                    return response.json() if response.content else None
        except MealieError:
            raise
        except _RetryableStatusError as exc:
            raise MealieRequestError(str(exc), status_code=exc.status_code) from exc
        except httpx.HTTPError as exc:
            raise MealieRequestError(f"Mealie network error on {method} {path}: {exc}") from exc

    async def get_group_slug(self) -> str:
        """Fetch and cache the authenticated user's Mealie group slug."""
        cached = self._group_slug_cache.get("slug")
        if cached is not None:
            return cached
        data = await self._request("GET", "/api/groups/self")
        slug = data.get("slug") if isinstance(data, dict) else None
        resolved = slug if isinstance(slug, str) and slug else "home"
        self._group_slug_cache["slug"] = resolved
        return resolved

    async def _get_or_create_organizers(self, kind: str, names: list[str]) -> list[MealieOrganizer]:
        if not names:
            return []

        endpoint = f"/api/organizers/{kind}"
        index = self._organizer_cache.get(kind)
        if index is None:
            data = await self._request("GET", endpoint, params={"perPage": 500})
            items = data.get("items", []) if isinstance(data, dict) else []
            index = {}
            for item in items:
                if isinstance(item, dict) and {"id", "name", "slug"} <= item.keys():
                    org = MealieOrganizer(
                        id=str(item["id"]),
                        name=str(item["name"]),
                        slug=str(item["slug"]),
                    )
                    index[org.name.casefold()] = org
            self._organizer_cache[kind] = index

        resolved: list[MealieOrganizer] = []
        for name in names:
            key = name.casefold()
            existing = index.get(key)
            if existing is None:
                created = await self._request("POST", endpoint, json={"name": name})
                if not isinstance(created, dict) or not ({"id", "name", "slug"} <= created.keys()):
                    raise MealieRequestError(f"Unexpected response creating {kind}: {created!r}")
                existing = MealieOrganizer(
                    id=str(created["id"]),
                    name=str(created["name"]),
                    slug=str(created["slug"]),
                )
                index[key] = existing
            resolved.append(existing)
        return resolved

    async def _search_entity(self, endpoint: str, name: str) -> MealieEntity | None:
        data = await self._request("GET", endpoint, params={"search": name, "perPage": 50})
        items = data.get("items", []) if isinstance(data, dict) else []
        wanted = _lookup_keys(name)
        for item in items:
            if not isinstance(item, dict):
                continue
            entity = _parse_entity(item)
            if entity is not None and (wanted & _extract_entity_keys(item)):
                return entity
        return None

    async def _get_or_create_unit(self, name: str) -> MealieEntity:
        cleaned = name.strip()
        wanted = _lookup_keys(cleaned)

        index = self._units_cache.get("units")
        if index is None:
            data = await self._request("GET", "/api/units", params={"perPage": 1000})
            items = data.get("items", []) if isinstance(data, dict) else []
            index = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                entity = _parse_entity(item)
                if entity is None:
                    continue
                for key in _extract_entity_keys(item):
                    index.setdefault(key, entity)
            self._units_cache["units"] = index

        for key in wanted:
            existing = index.get(key)
            if existing is not None:
                return existing

        try:
            created = await self._request("POST", "/api/units", json={"name": cleaned})
        except MealieRequestError as exc:
            if exc.status_code in (400, 409):
                found = await self._search_entity("/api/units", cleaned)
                if found is not None:
                    for key in wanted | _lookup_keys(found.name):
                        index[key] = found
                    return found
            raise

        entity = _parse_entity(created)
        if entity is None:
            raise MealieRequestError(f"Unexpected response creating unit: {created!r}")
        for key in wanted | _extract_entity_keys(created):
            index[key] = entity
        return entity

    async def _get_or_create_food(self, name: str) -> MealieEntity:
        cleaned = name.strip()
        cache_key = cleaned.casefold()
        cached = self._food_cache.get(cache_key)
        if cached is not None:
            return cached

        found = await self._search_entity("/api/foods", cleaned)
        if found is not None:
            self._food_cache[cache_key] = found
            self._food_cache[found.name.casefold()] = found
            return found

        try:
            created = await self._request("POST", "/api/foods", json={"name": cleaned})
        except MealieRequestError as exc:
            if exc.status_code in (400, 409):
                found = await self._search_entity("/api/foods", cleaned)
                if found is not None:
                    self._food_cache[cache_key] = found
                    self._food_cache[found.name.casefold()] = found
                    return found
            raise

        entity = _parse_entity(created)
        if entity is None:
            raise MealieRequestError(f"Unexpected response creating food: {created!r}")
        self._food_cache[cache_key] = entity
        self._food_cache[entity.name.casefold()] = entity
        return entity

    async def _build_ingredients_payload(
        self,
        ingredients: list[Ingredient],
    ) -> list[dict[str, Any]]:
        payload: list[dict[str, Any]] = []
        for ing in ingredients:
            unit = await self._get_or_create_unit(ing.unit) if ing.unit else None
            food = await self._get_or_create_food(ing.food)
            payload.append(
                {
                    "referenceId": str(uuid4()),
                    "quantity": ing.quantity,
                    "unit": unit.to_payload() if unit is not None else None,
                    "food": food.to_payload(),
                    "note": ing.note or "",
                    "display": ing.to_display_text(),
                    "disableAmount": ing.quantity is None,
                }
            )
        return payload

    def _build_recipe_patch(
        self,
        recipe: Recipe,
        ingredients: list[dict[str, Any]],
        tags: list[MealieOrganizer],
        categories: list[MealieOrganizer],
    ) -> dict[str, Any]:
        instructions = [
            {
                "id": str(uuid4()),
                "title": step.title or "",
                "text": step.text,
                "ingredientReferences": [],
            }
            for step in recipe.instructions
        ]

        payload: dict[str, Any] = {
            "description": recipe.description,
            "recipeIngredient": ingredients,
            "recipeInstructions": instructions,
            "tags": [tag.to_payload() for tag in tags],
            "recipeCategory": [cat.to_payload() for cat in categories],
        }
        if recipe.servings is not None:
            payload["recipeYield"] = recipe.servings
        if recipe.prep_time is not None:
            payload["prepTime"] = recipe.prep_time
        if recipe.cook_time is not None:
            payload["performTime"] = recipe.cook_time
        if recipe.total_time is not None:
            payload["totalTime"] = recipe.total_time
        return payload

    async def create_recipe(self, recipe: Recipe) -> CreatedRecipe:
        """Create a structured recipe in Mealie and return its slug and direct URL."""
        ingredients = await self._build_ingredients_payload(recipe.ingredients)
        tags = await self._get_or_create_organizers("tags", recipe.tags)
        categories = await self._get_or_create_organizers("categories", recipe.categories)

        created = await self._request("POST", "/api/recipes", json={"name": recipe.name})
        if isinstance(created, str) and created:
            slug = created
        elif isinstance(created, dict) and isinstance(created.get("slug"), str):
            slug = created["slug"]
        else:
            raise MealieRequestError(f"Unexpected create recipe response: {created!r}")

        patch_payload = self._build_recipe_patch(recipe, ingredients, tags, categories)
        await self._request("PATCH", f"/api/recipes/{slug}", json=patch_payload)

        group_slug = await self.get_group_slug()
        url = f"{self._base_url}/g/{group_slug}/r/{slug}"
        logger.info("mealie_recipe_created", slug=slug, name=recipe.name, url=url)
        return CreatedRecipe(slug=slug, name=recipe.name, url=url)
