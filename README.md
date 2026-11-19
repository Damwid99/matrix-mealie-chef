# Matrix Mealie Chef

[![CI](https://github.com/Damwid99/matrix-mealie-chef/actions/workflows/ci.yml/badge.svg)](https://github.com/Damwid99/matrix-mealie-chef/actions/workflows/ci.yml)
[![Docker](https://github.com/Damwid99/matrix-mealie-chef/actions/workflows/docker.yml/badge.svg)](https://github.com/Damwid99/matrix-mealie-chef/actions/workflows/docker.yml)

A self-hosted conversational Matrix bot that turns spontaneous cooking ideas, leftover fridge ingredients, or messy notes into structured recipes saved directly to your [Mealie](https://mealie.io/) instance.

---

## Why Matrix Mealie Chef?

Self-hosted recipe managers like Mealie are great for organizing recipes, scaling servings, and generating shopping lists — but entering recipes by hand is tedious. AI chats are great for inventing dinner ideas from random ingredients, yet those recipes usually stay buried in chat history.

**Matrix Mealie Chef** bridges the gap: brainstorm and refine recipes naturally in Matrix, and save the finished version to Mealie with a single `!save` command.

## Features

* **Fast Conversational Drafting:** Brainstorm ideas and refine recipes in clean Markdown chat — structured JSON extraction runs **only** when you type `!save`.
* **Human-in-the-Loop:** Nothing is ever saved to Mealie without your explicit `!save` approval.
* **Structured Mealie Integration:** Automatically resolves or creates Mealie **units**, **foods**, **tags**, and **categories**, so serving scaling and shopping lists work out of the box.
* **Model-Agnostic (LiteLLM):** Works with free cloud tiers (Google Gemini), commercial models (OpenAI, Anthropic, OpenRouter), or private self-hosted models (Ollama).
* **Language-Aware:** Automatically replies and saves recipes in whatever language you write in.
* **Access-Controlled & Self-Hosted Native:** Only responds to and auto-joins invites from `ALLOWED_USER_IDS`; persists Matrix login sessions across restarts; publishes multi-arch (`amd64` + `arm64`) Docker images.

---

## How It Works

1. **Start with anything:** Invite the bot to an **unencrypted** Matrix room and send ingredients (*"chicken breast, spinach, cream, garlic"*), a meal idea, or messy recipe notes.
2. **Brainstorm & refine:** Chat naturally — ask for 3 ideas, pick one, double the servings, swap ingredients, or make it spicier.
3. **Save with one command:** Type `!save`. The bot extracts structured ingredients, steps, times, tags, and categories, saves the recipe to Mealie, and replies with a direct link.

### Commands

| Command | Description |
| :--- | :--- |
| `!save` | Extract the finalized draft into structured JSON and save it to Mealie |
| `!show` | Re-display the latest recipe draft reply |
| `!discard` / `!cancel` | Throw away the current draft session |
| `!new <idea>` | Discard any active draft and start fresh with a new prompt |
| `!help` | Show usage instructions and available commands |

---

## Quick Start

### Option 1: Docker Compose (Recommended)

Pre-built multi-arch images (`linux/amd64` and `linux/arm64`) are published to GitHub Container Registry.

1. Download `.env.example` and `docker-compose.yml` (or clone the repo):
   ```bash
   mkdir matrix-mealie-chef && cd matrix-mealie-chef
   curl -O https://raw.githubusercontent.com/Damwid99/matrix-mealie-chef/main/.env.example
   curl -O https://raw.githubusercontent.com/Damwid99/matrix-mealie-chef/main/docker-compose.yml
   cp .env.example .env
   mkdir -p data
   ```
2. Edit `.env` with your Matrix, Mealie, and LLM credentials (see [Configuration](#configuration)).
3. Start the bot:
   ```bash
   docker compose up -d
   docker compose logs -f
   ```

### Option 2: Local with `uv`

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/Damwid99/matrix-mealie-chef.git
cd matrix-mealie-chef
cp .env.example .env
# Edit .env with your credentials
uv sync
uv run mealie-bot
```

---

## Configuration

### 1. Matrix Bot Account

1. Create a dedicated Matrix account for the bot on your homeserver (e.g. `@mealie-bot:example.com`).
2. Set `MATRIX_HOMESERVER`, `MATRIX_USER_ID`, and **either**:
   * `MATRIX_PASSWORD` — on first start, the bot logs in and caches its token in `DATA_DIR/session.json` (`0600` permissions), or
   * `MATRIX_ACCESS_TOKEN` — an existing access token (e.g. from Element → *Settings → Help & About*).
3. Set `ALLOWED_USER_IDS` to a comma-separated list of Matrix user IDs allowed to invite and chat with the bot (e.g. `@you:example.com,@partner:example.com`).
4. Invite the bot to an **unencrypted** room — it will auto-join invites from allowed users.

> **Note on E2EE:** End-to-end encrypted rooms are not supported yet. Use an unencrypted room for chatting with the bot.

### 2. Mealie API Token

1. Log into your Mealie web UI.
2. Open your user profile → **Manage Your API Tokens** (`/user/profile/api-tokens`).
3. Create a long-lived token (e.g. `matrix-mealie-chef`) and copy it into `MEALIE_API_TOKEN`.
4. Set `MEALIE_URL` to your Mealie base URL (e.g. `https://mealie.example.com` or `http://mealie:9000` on an internal Docker network).

### 3. LLM Provider Examples (LiteLLM)

Set `LLM_MODEL` (and `LLM_API_KEY` / `LLM_API_BASE` as needed):

| Provider | `LLM_MODEL` | `LLM_API_KEY` | `LLM_API_BASE` |
| :--- | :--- | :--- | :--- |
| **Google Gemini (Free Tier)** | `gemini/gemini-3.5-flash-lite` or `gemini/gemini-2.5-flash` | Key from [Google AI Studio](https://aistudio.google.com/apikey) | *(empty)* |
| **OpenAI** | `openai/gpt-4o-mini` | OpenAI API key | *(empty)* |
| **Anthropic** | `anthropic/claude-3-5-haiku-latest` | Anthropic API key | *(empty)* |
| **Local Ollama** | `ollama/llama3.1` | *(empty)* | `http://ollama:11434` |

### Environment Variables Reference

| Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `MATRIX_HOMESERVER` | Yes | — | Matrix homeserver URL (`https://...`) |
| `MATRIX_USER_ID` | Yes | — | Bot Matrix user ID (`@bot:domain`) |
| `MATRIX_PASSWORD` | One of* | — | Bot password (token cached in `DATA_DIR/session.json`) |
| `MATRIX_ACCESS_TOKEN` | One of* | — | Existing Matrix access token (takes precedence over password) |
| `MATRIX_DEVICE_ID` | No | `MEALIE_BOT` | Device ID label for the bot session |
| `ALLOWED_USER_IDS` | Yes | — | Comma-separated Matrix IDs allowed to use the bot |
| `MEALIE_URL` | Yes | — | Base URL of your Mealie instance |
| `MEALIE_API_TOKEN` | Yes | — | Mealie long-lived Bearer API token |
| `LLM_MODEL` | No | `gemini/gemini-2.5-flash` | LiteLLM model identifier |
| `LLM_API_KEY` | Depends | — | API key for cloud LLM providers (not needed for local Ollama) |
| `LLM_API_BASE` | No | — | Custom endpoint URL (e.g. Ollama or OpenRouter) |
| `DATA_DIR` | No | `./data` | Directory for persisted Matrix session (`session.json`) |
| `DRAFT_TTL_SECONDS` | No | `7200` | Seconds before an idle unsaved draft expires (2 hours) |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` |
| `LOG_JSON` | No | `false` | Emit structured JSON logs (`true` in Docker by default) |

\* Provide at least one of `MATRIX_PASSWORD` or `MATRIX_ACCESS_TOKEN`.

---

## Development

```bash
uv sync --dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Opt-in live integration smoke tests (skipped during normal `pytest` and CI):

```bash
MEALIE_LIVE_TEST=1 uv run --env-file .env pytest tests/test_mealie_live.py -s
LLM_LIVE_TEST=1 uv run --env-file .env pytest tests/test_llm_live.py -s
```

---

## Troubleshooting

* **Bot joined the room, but doesn't respond to messages:**
  Check that the room is **unencrypted** and that your Matrix user ID is listed in `ALLOWED_USER_IDS`.
* **Replies feel slow on reasoning models (e.g. `gemini-3.8-flash`):**
  Reasoning models spend time thinking on every reply. Switch `LLM_MODEL` to a fast non-reasoning/lite model such as `gemini/gemini-3.5-flash-lite`, `openai/gpt-4o-mini`, or a local Ollama model.
* **Matrix login fails after rotating credentials:**
  Delete `data/session.json` and restart the bot so it creates a fresh session.
* **Mealie save fails with authentication error:**
  Verify `MEALIE_API_TOKEN` hasn't expired and `MEALIE_URL` is reachable from the bot container/host. Your draft stays preserved in chat so you can retry `!save` once fixed.

---

## License

MIT