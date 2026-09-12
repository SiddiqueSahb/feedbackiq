"""
Logging setup for FeedbackIQ.

Logs go to **stdout**, which is what a container expects: the platform collects the
stream, so nothing has to manage files or rotation inside the image. Before
Milestone 2 every logger also wrote to `logs/feedbackanalytics.log`, relative to
whatever directory the process happened to start in; that file is no longer used
unless `LOG_FILE` is set.

Only the standard library is used. Levels, logger names and message format are
unchanged, so existing log lines still read the same.

Usage:
    from feedbackiq.core.logging import get_logger
    log = get_logger("api.main")
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from feedbackiq.core.config import settings

MESSAGE_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def configure_logging(force: bool = False) -> None:
    """
    Attach one stdout handler to the root logger, at the configured level.

    Handlers live on the root logger rather than on each named logger, so every
    module's messages are formatted the same way and nothing is duplicated. Safe to
    call repeatedly: it only does the work once unless `force=True`.
    """
    global _configured

    if _configured and not force:
        return

    root = logging.getLogger()
    formatter = logging.Formatter(fmt=MESSAGE_FORMAT, datefmt=DATE_FORMAT)

    # Drop handlers this function added before, so force=True (and repeated calls in
    # tests) can't stack duplicates.
    for handler in [h for h in root.handlers if getattr(h, "_feedbackiq", False)]:
        root.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler._feedbackiq = True  # type: ignore[attr-defined]
    root.addHandler(stream_handler)

    # Opt-in file logging, off by default. Useful on a plain VM; pointless in a
    # container, where stdout is already collected.
    if settings.LOG_FILE:
        file_handler = RotatingFileHandler(
            settings.LOG_FILE,
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler._feedbackiq = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)

    root.setLevel(settings.LOG_LEVEL.upper())
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return the named logger, configuring logging on first use."""
    configure_logging()

    return logging.getLogger(name)
