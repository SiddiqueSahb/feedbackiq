"""
The engine's exceptions.

Before Milestone 3 a failing RAG call returned `"An error occurred: <exception>"` as
if it were an answer, with HTTP 200. The engine raises instead: callers decide what
the user sees, and monitoring can see that something broke.

Everything here derives from `EngineError`, so a caller can catch one type:

    EngineError
      ModelUnavailableError   a model or artefact is missing/unloadable
      RetrievalError          the injected retriever failed
      LLMError                the language model provider
        LLMTimeoutError       the call exceeded the configured timeout
        LLMRateLimitError     rate limited, and retries were exhausted
        LLMResponseError      responded, but the output could not be validated
"""

from __future__ import annotations


class EngineError(Exception):
    """Base class for every failure the analytics engine reports."""


class ModelUnavailableError(EngineError):
    """A required model or artefact could not be loaded."""


class RetrievalError(EngineError):
    """The injected retriever raised. The engine never silently returns no evidence."""


class LLMError(EngineError):
    """The language model provider failed."""


class LLMTimeoutError(LLMError):
    """The provider did not answer within the configured timeout."""


class LLMRateLimitError(LLMError):
    """The provider rate limited us and the retry budget is spent."""


class LLMResponseError(LLMError):
    """The provider answered, but the response could not be parsed or validated."""
