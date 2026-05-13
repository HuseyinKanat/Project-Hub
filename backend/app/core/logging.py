"""Structured logging utilities."""

import logging
import sys
from functools import lru_cache


def get_logger(name: str) -> logging.Logger:
    """Return a logger with structured formatting."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


@lru_cache
def get_settings_log_level() -> int:
    from app.core.config import get_settings

    level = get_settings().log_level.upper()
    return getattr(logging, level, logging.INFO)
