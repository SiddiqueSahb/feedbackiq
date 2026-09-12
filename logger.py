"""
Central logging configuration for FeedbackAnalytics.
Import this in any module: from logger import get_logger
"""

import logging
import os
from logging.handlers import RotatingFileHandler




LOG_DIR = "logs"

os.makedirs(LOG_DIR, exist_ok=True)

# e.g. logs/feedbackanalytics.log
LOG_FILE = os.path.join(LOG_DIR, "feedbackanalytics.log")

# from .env; defaults to WARNING
LOG_LEVEL = os.getenv("LOG_LEVEL", "WARNING").upper()

_fmt = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

def get_logger(name: str) -> logging.Logger:
    """Create and return a configured logger."""
    print("Inside get_logger()")
    logger = logging.getLogger(name)

    # already configured
    if logger.handlers:
        return logger

    logger.setLevel(LOG_LEVEL)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(_fmt)
    logger.addHandler(console_handler)

    # Save logs to a rotating file (5 MB, keep 3 backups)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8"
    )
    file_handler.setFormatter(_fmt)
    logger.addHandler(file_handler)

    # Prevent duplicate log messages from parent loggers
    logger.propagate = False

    return logger

if __name__ == "__main__":
    print(f"LOG_LEVEL = {LOG_LEVEL}")
    logger = get_logger(__name__)
    logger.warning("Warning message")