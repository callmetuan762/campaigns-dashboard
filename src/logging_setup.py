"""structlog configuration with JSON output and PII/secret redaction.

INFRA-05: Structured logging captures API call outcomes, report delivery status,
and errors WITHOUT logging PII (email, phone) or raw ad data (ad copy, creative
bodies) or secrets (tokens, API keys, passwords).

Design:
- Default to JSON renderer (Docker stdout friendly; easy to ship to a log aggregator).
- The _redact_processor runs BEFORE serialization and substitutes the literal
  string "***REDACTED***" for any value whose key is in the deny list.
- stdlib logging (used by aiogram, apscheduler, aiosqlite) is bridged through
  structlog's ProcessorFormatter so third-party log lines also pass through
  the redaction processor.
"""
from __future__ import annotations

import logging
import sys

import structlog

# Lowercase keys whose VALUES are always replaced with ***REDACTED*** regardless
# of where they appear in the structured event dict.
_REDACT_KEYS: frozenset[str] = frozenset(
    {
        # Secrets
        "token",
        "telegram_bot_token",
        "anthropic_api_key",
        "meta_access_token",
        "meta_app_secret",
        "google_credentials",
        "ga4_service_account_json",
        "access_token",
        "secret",
        "password",
        "api_key",
        "authorization",
        # PII
        "email",
        "phone",
        # Raw upstream payloads / ad creative
        "raw_response",
        "response_body",
        "ad_creative_body",
        "ad_copy",
        "message_text",
        "text",
    }
)


def _redact_processor(_logger, _method_name, event_dict: dict) -> dict:
    """Replace sensitive values with ***REDACTED*** in-place."""
    for key in list(event_dict.keys()):
        if key.lower() in _REDACT_KEYS:
            event_dict[key] = "***REDACTED***"
    return event_dict


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Initialize structlog and bridge stdlib logging.

    Args:
        level: One of "DEBUG", "INFO", "WARNING", "ERROR".
        fmt: "json" for production, "console" for local development.
    """
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        _redact_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if fmt == "json":
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    log_level = getattr(logging, level.upper(), logging.INFO)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib logging (aiogram, apscheduler, aiosqlite, etc.) into the
    # same processor pipeline so their messages also pass through _redact_processor.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replace any existing handlers (Docker re-runs, test sessions) with ours.
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(log_level)

    # Tame the chattiest third-party loggers in production.
    logging.getLogger("aiogram").setLevel(logging.INFO)
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
