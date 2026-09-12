"""
The one place FeedbackIQ talks to a language model provider.

Everything else in the engine takes an `LLMAdapter` and calls `complete()` or
`structured()`. That keeps Groq-specific details - the client class, its error
payloads, its rate-limit wording - inside this module, so swapping or adding a
provider later is one file's work.

The adapter is responsible for:
  * timeout        passed to the client, and surfaced as LLMTimeoutError
  * retries        only for rate limits, honouring the provider's own "try again in Xs"
  * structured     schema-validated output, with recovery when a model writes JSON as
                   text instead of a tool call (the real behaviour of
                   openai/gpt-oss-20b, which is the configured default model)
  * usage          token counts when the provider reports them
  * errors         raised as typed exceptions, never returned as if they were answers
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
from feedbackiq.engine.errors import (
    EngineError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    ModelUnavailableError,
)
from feedbackiq.engine.types import UsageStats

log = get_logger("engine.llm")

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _looks_like_rate_limit(exc: Exception) -> bool:
    message = str(exc).lower()
    return "rate_limit" in message or "429" in message


def _looks_like_timeout(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    message = str(exc).lower()
    return "timeout" in message or "timed out" in message


def retry_after_seconds(exc: Exception, default: float) -> float:
    """
    How long the provider asked us to wait.

    Groq puts it in the message ("Please try again in 19m15.168s" / "in 1.5s"). We add
    half a second of margin. Falls back to `default` when there is nothing to read.
    """
    match = re.search(r"try again in ([\d.]+)s", str(exc))
    if match:
        try:
            return float(match.group(1)) + 0.5
        except ValueError:
            pass

    return default


def recover_structured_output(exc: Exception, schema: type[SchemaT]) -> SchemaT | None:
    """
    Rescue a valid object from a `tool_use_failed` error.

    Some models write the JSON as ordinary content instead of a tool call. Groq rejects
    that as an error but still hands back what the model produced, in
    `failed_generation`. If it validates against the schema, it is a perfectly good
    answer, so we use it rather than failing the request.

    Returns None when there is nothing recoverable - the caller then raises.
    """
    body = getattr(exc, "body", None)

    if isinstance(body, dict):
        error = body.get("error") or {}
        if error.get("code") != "tool_use_failed":
            return None
        raw = error.get("failed_generation")
    else:
        # Older clients expose the payload only in the message text.
        match = re.search(r"'failed_generation':\s*'(.*)'\}\}\s*$", str(exc), re.S)
        raw = match.group(1).encode().decode("unicode_escape") if match else None

    if not raw:
        return None

    # The model sometimes wraps the JSON in prose or a ```json fence.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    candidate = fenced.group(1) if fenced else raw
    if not fenced:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start == -1 or end == -1:
            return None
        candidate = candidate[start:end + 1]

    try:
        return schema.model_validate_json(candidate)
    except (ValidationError, ValueError):
        log.warning("Recovered payload from a failed tool call did not validate.")
        return None


class LLMAdapter:
    """
    A small wrapper around a LangChain chat model.

    Pass `chat_model` to inject anything with `.invoke()` (a fake in tests, a different
    provider later). Leave it out and the adapter builds the configured Groq client on
    first use, raising ModelUnavailableError if no API key is set.
    """

    def __init__(
        self,
        chat_model: Any | None = None,
        *,
        model_name: str | None = None,
        timeout: int | None = None,
        max_retries: int | None = None,
        retry_base_wait: float | None = None,
        temperature: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._chat_model = chat_model
        self.model_name = model_name or settings.GROQ_MODEL
        self.timeout = timeout if timeout is not None else settings.LLM_TIMEOUT
        self.max_retries = max_retries if max_retries is not None else settings.MAX_LIVE_RETRIES
        self.retry_base_wait = (
            retry_base_wait if retry_base_wait is not None else settings.RETRY_BASE_WAIT
        )
        self.temperature = temperature if temperature is not None else settings.LLM_TEMPERATURE
        self._sleep = sleep
        self._usage = UsageStats()

    # ---------------------------------------------------------------- model

    @property
    def available(self) -> bool:
        """True when a call could be made at all (injected model, or a configured key)."""
        return self._chat_model is not None or bool(settings.GROQ_API_KEY)

    def _model(self) -> Any:
        if self._chat_model is not None:
            return self._chat_model

        if not settings.GROQ_API_KEY:
            raise ModelUnavailableError(
                "No LLM API key configured. Set GROQ_API_KEY to enable generation."
            )

        from langchain_groq import ChatGroq

        self._chat_model = ChatGroq(
            api_key=settings.GROQ_API_KEY,
            model=self.model_name,
            temperature=self.temperature,
            timeout=self.timeout,
        )
        return self._chat_model

    # ---------------------------------------------------------------- usage

    @property
    def usage(self) -> UsageStats:
        """Cumulative usage since the adapter was created (or last reset)."""
        return self._usage

    def reset_usage(self) -> None:
        self._usage = UsageStats()

    def _record(self, response: Any, retries: int) -> None:
        """
        Add one call to the running totals.

        Token counts come from the provider's own reporting when present
        (`usage_metadata` on a LangChain message). Structured-output calls often return
        a parsed object with no metadata attached - then the call is counted but the
        token fields stay at zero rather than being guessed.
        """
        metadata = getattr(response, "usage_metadata", None) or {}
        self._usage = self._usage.plus(
            UsageStats(
                llm_calls=1,
                input_tokens=int(metadata.get("input_tokens", 0) or 0),
                output_tokens=int(metadata.get("output_tokens", 0) or 0),
                retries=retries,
            )
        )

    # ---------------------------------------------------------------- calls

    def _invoke(self, target: Any, payload: Any) -> tuple[Any, int]:
        """
        Invoke with the retry policy. Returns (response, retries_used).

        Only rate limits are retried: a timeout or a bad request will not fix itself,
        and the caller is waiting.
        """
        attempt = 0
        while True:
            try:
                return target.invoke(payload), attempt
            except Exception as exc:
                if _looks_like_rate_limit(exc):
                    if attempt >= self.max_retries:
                        raise LLMRateLimitError(
                            f"Rate limited by the LLM provider after {attempt} retries."
                        ) from exc
                    wait = retry_after_seconds(exc, self.retry_base_wait)
                    log.warning(
                        "LLM rate limited; retrying in %.1fs (%d/%d)",
                        wait, attempt + 1, self.max_retries,
                    )
                    self._sleep(wait)
                    attempt += 1
                    continue
                raise

    def complete(self, prompt: str) -> str:
        """Plain text completion. Raises an LLMError subclass on failure."""
        try:
            response, retries = self._invoke(self._model(), prompt)
        except EngineError:
            # Already one of ours (ModelUnavailableError, LLMRateLimitError, ...):
            # re-wrapping it would hide why the call could not be made.
            raise
        except Exception as exc:
            if _looks_like_timeout(exc):
                raise LLMTimeoutError(f"LLM call timed out after {self.timeout}s") from exc
            raise LLMError(f"LLM call failed: {type(exc).__name__}") from exc

        self._record(response, retries)

        return getattr(response, "content", str(response))

    def structured(self, prompt: str, schema: type[SchemaT]) -> SchemaT:
        """
        Completion validated against a Pydantic schema.

        Recovers the JSON when the model writes it as content instead of a tool call;
        raises LLMResponseError when nothing valid can be obtained.
        """
        model = self._model()
        try:
            structured_model = model.with_structured_output(schema)
        except AttributeError as exc:  # an injected fake without the helper
            raise LLMError("The configured chat model does not support structured output.") from exc

        try:
            response, retries = self._invoke(structured_model, prompt)
        except EngineError:
            raise
        except Exception as exc:
            recovered = recover_structured_output(exc, schema)
            if recovered is not None:
                log.warning(
                    "Model did not emit a tool call; recovered and validated the JSON "
                    "from the error payload instead."
                )
                self._record(None, 0)
                return recovered

            if _looks_like_timeout(exc):
                raise LLMTimeoutError(f"LLM call timed out after {self.timeout}s") from exc
            raise LLMResponseError(
                f"LLM did not return a valid {schema.__name__}: {type(exc).__name__}"
            ) from exc

        self._record(response, retries)

        if isinstance(response, schema):
            return response

        # Some models/versions return the raw message even under structured output.
        try:
            return schema.model_validate(response)
        except (ValidationError, ValueError) as exc:
            raise LLMResponseError(
                f"LLM response did not match {schema.__name__}."
            ) from exc
