"""Application entrypoint for Matrix Mealie Chef."""

import asyncio
import contextlib
import signal
import sys

import structlog
from pydantic import ValidationError

from matrix_mealie_chef.config import Settings, load_settings
from matrix_mealie_chef.drafts import DraftStore
from matrix_mealie_chef.handlers import ConversationHandler
from matrix_mealie_chef.llm import RecipeLLMClient
from matrix_mealie_chef.logging import configure_logging
from matrix_mealie_chef.matrix_bot import MatrixBot, MatrixBotError
from matrix_mealie_chef.mealie import MealieClient

logger = structlog.get_logger(__name__)


def _request_shutdown(sig: signal.Signals, stop_event: asyncio.Event) -> None:
    if not stop_event.is_set():
        logger.info("shutdown_signal_received", signal=sig.name)
        stop_event.set()


async def run_bot(settings: Settings, *, stop_event: asyncio.Event | None = None) -> None:
    """Wire all components and run the Matrix bot until stopped."""
    llm_client = RecipeLLMClient(
        model=settings.llm_model,
        api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else None,
        api_base=settings.llm_api_base,
    )
    drafts = DraftStore(ttl_seconds=settings.draft_ttl_seconds)

    async with MealieClient(
        base_url=settings.mealie_url,
        api_token=settings.mealie_api_token.get_secret_value(),
    ) as mealie_client:
        handler = ConversationHandler(llm=llm_client, mealie=mealie_client, drafts=drafts)
        bot = MatrixBot(settings=settings, handler=handler)

        shutdown_event = stop_event or asyncio.Event()
        loop = asyncio.get_running_loop()
        installed_signals: list[signal.Signals] = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, _request_shutdown, sig, shutdown_event)
                installed_signals.append(sig)

        logger.info(
            "bot_starting",
            matrix_user_id=settings.matrix_user_id,
            homeserver=settings.matrix_homeserver,
            mealie_url=settings.mealie_url,
            llm_model=settings.llm_model,
            allowed_users_count=len(settings.allowed_user_ids),
        )
        try:
            await bot.run_forever(stop_event=shutdown_event)
        finally:
            for sig in installed_signals:
                with contextlib.suppress(NotImplementedError):
                    loop.remove_signal_handler(sig)
            await bot.close()
            logger.info("bot_stopped")


def run() -> None:
    """CLI entrypoint (`mealie-bot`)."""
    try:
        settings = load_settings()
    except ValidationError as exc:
        print(f"Configuration error:\n{exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    configure_logging(level=settings.log_level, json_logs=settings.log_json)

    try:
        asyncio.run(run_bot(settings))
    except KeyboardInterrupt:
        logger.info("bot_interrupted")
    except MatrixBotError as exc:
        logger.error("bot_fatal_error", error=str(exc))
        raise SystemExit(1) from exc
