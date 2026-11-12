"""Central logging configuration. Library code logs; it never prints."""

from __future__ import annotations

import logging

LOGGER_NAME = "decision_memory"
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Configure the package logger once; later calls only adjust the level."""
    logger = logging.getLogger(LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        logger.addHandler(handler)
        logger.propagate = False
    logger.setLevel(level)
    return logger
