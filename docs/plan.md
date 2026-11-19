# Matrix Mealie Chef – Implementation Plan

Working rules: one step = one branch (`feat/<name>`) = one or more small commits (Conventional Commits). Each step ends with `ruff check`, `ruff format --check`, `mypy`, `pytest` green, then push and merge.

## Target architecture

```
src/matrix_mealie_chef/
  main.py            # entrypoint: run()
  config.py          # pydantic-settings Settings
  logging.py         # structlog setup
  models.py          # Recipe, Ingredient, Step (pydantic)
  llm.py             # LiteLLM client: generate / refine -> Recipe
  prompts.py         # system prompts, language handling
  mealie.py          # Mealie REST client (httpx + tenacity)
  drafts.py          # per-room/user draft store (TTLCache)
  formatting.py      # Recipe -> Markdown/HTML preview
  matrix_bot.py      # nio client, sync loop, auth, event handlers
  handlers.py        # conversation logic / commands (transport-agnostic)
tests/               # mirrors modules; respx for HTTP, fake LLM/Matrix
```

Flow: Matrix message → `handlers` → (`llm` → `Recipe`) → `drafts` → `formatting` → reply. `!save` → `mealie` → reply with link.

---

## Phase 0 – Foundation

### Step 0.1 – Repo hygiene  (`chore/repo-setup`)
- Make first commit of what exists (`pyproject.toml`, `uv.lock`, `.gitignore`, `.env.example`, `.python-version`).
- Add `data/.gitkeep`, short `README.md` stub, remove stray `plan.md` line from `.gitignore` if unintended.
- Verify package skeleton `src/matrix_mealie_chef/__init__.py` + `main.py` with `run()` (matches `[project.scripts]`) and `tests/`.
- Commit: `chore: initial project scaffold`

### Step 0.2 – Settings & logging  (`feat/config`)
- `Settings` (pydantic-settings) mapping every var in `.env.example`; `ALLOWED_USER_IDS` parsed to a list; validator: password OR access token required.
- structlog config honoring `LOG_LEVEL` / `LOG_JSON`.
- Tests: env parsing, validation errors.
- Commit: `feat(config): add typed settings and structured logging`

### Step 0.3 – CI  (`chore/ci`)
- GitHub Actions: `uv sync`, ruff, mypy, pytest.
- Commit: `ci: add lint, type-check and test workflow`

## Phase 1 – Domain & Mealie

### Step 1.1 – Recipe model  (`feat/recipe-model`)
- Pydantic `Recipe`: name, description, servings, prep/cook/total time, `ingredients[]` (quantity, unit, food, note), `instructions[]`, `tags[]`, optional `categories[]`.
- Validation (non-empty name/ingredients/steps), JSON schema export for LLM structured output.
- Commit: `feat(models): add recipe domain models`

### Step 1.2 – Mealie client  (`feat/mealie-client`)
- Async `MealieClient` (httpx, Bearer token, tenacity retries on 5xx/timeouts).
- Flow with the Mealie API: `POST /api/recipes` (create by name → slug) → `GET /api/recipes/{slug}` → modify fields → `PUT/PATCH /api/recipes/{slug}`.
- Tags: look up/create via `/api/organizers/tags`; same for categories. Cache lookups (cachetools).
- Map ingredients (use `note`/`display` text first; structured units/foods optional later). Time fields, `recipeYield`, instructions → `recipeInstructions`.
- Return recipe URL: `{MEALIE_URL}/g/<group>/r/<slug>`.
- Tests with respx (success, 401, 5xx retry, tag creation).
- Commit: `feat(mealie): add API client for creating recipes`

### Step 1.3 – Mealie smoke test  (`chore/mealie-smoke`)
- Manual script/pytest marker against your real Mealie to verify the payload; fix mismatches.
- Commit: `fix(mealie): align payload with live instance`

## Phase 2 – LLM

### Step 2.1 – Prompts & generation  (`feat/llm-generate`)
- `llm.py` using `litellm.acompletion`, JSON-schema/`response_format` output, fallback to JSON-in-text parsing; validate into `Recipe` with retry on validation error (feed error back).
- Prompt rules: answer in user's language, sane proportions, metric units, concise tags.
- Tests with mocked `litellm`.
- Commit: `feat(llm): generate structured recipes from free text`

### Step 2.2 – Refinement  (`feat/llm-refine`)
- `refine(current: Recipe, instruction: str, history)` → full updated `Recipe`.
- Keep language consistent; limit history size.
- Commit: `feat(llm): refine existing draft from natural-language feedback`

## Phase 3 – Conversation core (no Matrix yet)

### Step 3.1 – Drafts & formatting  (`feat/drafts-formatting`)
- `DraftStore` keyed by (room, user) with TTLCache (`DRAFT_TTL_SECONDS`).
- `formatting.py`: recipe → Markdown plain body + HTML (`markdown` lib) with title, times, servings, ingredients, numbered steps, tags, and footer hint of commands.
- Commit: `feat: add draft store and recipe preview formatting`

### Step 3.2 – Handlers  (`feat/handlers`)
- Commands: `!help`, `!save`, `!discard`/`!cancel`, `!show`, `!new <text>`.
- Non-command text: no draft → generate; draft exists → refine.
- Errors (LLM/Mealie) become friendly messages; draft preserved on save failure.
- Tests with fake LLM + fake Mealie.
- Commit: `feat(handlers): implement draft/refine/save conversation logic`

## Phase 4 – Matrix integration

### Step 4.1 – Client & auth  (`feat/matrix-client`)
- `AsyncClient` login via password (cache `access_token`/`device_id` in `DATA_DIR/session.json`) or token.
- Initial sync skipped for old messages (set `since` / ignore events before start time).
- Commit: `feat(matrix): connect, authenticate and persist session`

### Step 4.2 – Message handling  (`feat/matrix-messages)`
- `RoomMessageText` callback: ignore self, enforce `ALLOWED_USER_IDS`, pass to handlers, reply with formatted body + HTML.
- Auto-join invites (only from allowed users), typing indicator while LLM works, process messages per room sequentially (lock).
- Commit: `feat(matrix): wire message events to handlers`

### Step 4.3 – Run loop  (`feat/main-loop)`
- `main.run()`: setup logging, asyncio loop, `sync_forever`, graceful SIGINT/SIGTERM shutdown, reconnect/backoff.
- Commit: `feat: add entrypoint and graceful shutdown`

### Step 4.4 – E2EE decision  (`feat/e2ee` – optional)
- Start unencrypted rooms only (document it). Later: `matrix-nio[e2e]` + libolm, store in `DATA_DIR`, device verification.
- Commit: `feat(matrix): optional end-to-end encryption support`

## Phase 5 – Polish & release

### Step 5.1 – Docker  (`feat/docker`)
- Multi-stage `Dockerfile` (uv), non-root user, `data` volume, `docker-compose.yml` with the bot (+ example Mealie service).
- Commit: `build: add Dockerfile and compose example`

### Step 5.2 – Docs  (`docs/readme`)
- README: concept, quick start, creating bot account, Mealie token, LLM provider examples, commands, troubleshooting.
- Commit: `docs: write README and setup guide`

### Step 5.3 – Hardening  (`feat/hardening`)
- Rate-limit per user, max input length, LLM timeout/cost guard, duplicate-save protection, handle recipe name conflicts in Mealie.
- Optional: import by URL, image-to-recipe, `!list`/`!tags`.
- Commit: `feat: add rate limiting and input guards`

### Step 5.4 – Release  (`chore/release-0.1.0`)
- End-to-end test on real Matrix + Mealie + each LLM type (cloud, local Ollama), tag `v0.1.0`.
- Commit: `chore: release v0.1.0`

---

## Definition of done (MVP = through Step 4.3)
Send ingredients in Matrix → get preview → refine in plain text → `!save` → get working Mealie link; unauthorized users ignored; nothing saved without `!save`.
</Content>
</invoke>
