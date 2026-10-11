"""Logs go to stderr so stdout stays free for MCP stdio."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str) -> logging.Logger:
    logger = logging.getLogger("hylde")
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("hylde")
