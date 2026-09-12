"""
RAG chat service — thin wrapper around rag/pipeline.py.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
)

from rag.pipeline import ask
from logger import get_logger

log = get_logger("service.rag")


def ask_question(question: str, chat_history: list[dict] | None = None) -> dict:
    """Answer a question about customer feedback using the RAG pipeline."""

    if not question.strip():
        raise ValueError("Question cannot be empty.")

    log.info("RAG chat | question='%s'", question[:60])

    return ask(question=question, chat_history=chat_history or [])
