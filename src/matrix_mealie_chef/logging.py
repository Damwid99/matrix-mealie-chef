"""Structured logging configuration."""

import logging
import sys

import structlog


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    """Configure structlog and stdlib logging."""
    log_level: int = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    final_processors: list[structlog.types.Processor] = [
        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
    ]
    if json_logs:
        final_processors.extend(
            [
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ]
        )
    else:
        final_processors.append(structlog.dev.ConsoleRenderer())

    structlog.reset_defaults()
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=final_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)
