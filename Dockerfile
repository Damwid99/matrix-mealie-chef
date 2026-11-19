# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install third-party dependencies first for better Docker layer caching
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-install-project

# Copy source and install the package into /app/.venv
COPY src ./src
RUN uv sync --locked --no-editable


FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    DATA_DIR="/app/data" \
    LOG_JSON="true" \
    PYTHONUNBUFFERED="1"

RUN groupadd --system --gid 1000 app \
    && useradd --system --uid 1000 --gid app --home /app --shell /sbin/nologin app \
    && mkdir -p /app/data \
    && chown -R app:app /app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

USER app

VOLUME ["/app/data"]

ENTRYPOINT ["mealie-bot"]