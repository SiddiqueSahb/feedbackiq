"""
The LLM boundary - feedbackiq.engine.llm

Protects:
  * timeouts, rate limits and bad responses raise typed errors - never a string that
    looks like an answer
  * only rate limits are retried, honouring the provider's own "try again in Xs"
  * structured output is schema-validated, with recovery when a model writes JSON as
    content instead of a tool call (the real behaviour of the configured default model)
  * token usage is recorded when the provider reports it, and never invented

No network: every test injects a fake chat model.
"""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from feedbackiq.engine import LLMAdapter
from feedbackiq.engine.errors import (
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTimeoutError,
    ModelUnavailableError,
)


class Demo(BaseModel):
    name: str
    count: int


VALID = {"name": "battery", "count": 3}


def message(content: str, *, input_tokens: int | None = None, output_tokens: int | None = None):
    """A LangChain-style response: content, and usage metadata only if the provider sent it."""
    usage = None
    if input_tokens is not None:
        usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    return SimpleNamespace(content=content, usage_metadata=usage)


class FakeChatModel:
    """Records prompts and replays scripted responses; an Exception instance is raised."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []
        self.structured_schemas = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        response = self.responses.pop(0) if self.responses else message("ok")
        if isinstance(response, Exception):
            raise response
        return response

    def with_structured_output(self, schema):
        self.structured_schemas.append(schema)
        return self


def rate_limited(seconds: float = 1.5):
    return Exception(
        f"Error code: 429 - {{'error': {{'message': 'Rate limit reached', "
        f"'code': 'rate_limit_exceeded'}}}}. Please try again in {seconds}s."
    )


class ToolUseFailed(Exception):
    """Shaped like the Groq client error: the payload is on `.body`."""

    def __init__(self, failed_generation, code="tool_use_failed"):
        super().__init__("Error code: 400")
        self.body = {"error": {"code": code, "failed_generation": failed_generation}}


# ---------------------------------------------------------------- configuration


def test_no_model_and_no_api_key_is_reported_clearly():
    # conftest sets GROQ_API_KEY to an empty string.
    adapter = LLMAdapter()

    assert adapter.available is False
    with pytest.raises(ModelUnavailableError):
        adapter.complete("anything")


def test_injected_model_is_available_and_carries_the_configured_defaults():
    adapter = LLMAdapter(FakeChatModel([]), model_name="test-model", timeout=17, max_retries=4)

    assert adapter.available is True
    assert (adapter.model_name, adapter.timeout, adapter.max_retries) == ("test-model", 17, 4)


# ---------------------------------------------------------------- text completion


def test_complete_returns_the_models_text_and_sends_the_prompt():
    model = FakeChatModel([message("Key patterns: batteries fail early.")])
    adapter = LLMAdapter(model)

    answer = adapter.complete("Why are customers unhappy?")

    assert answer == "Key patterns: batteries fail early."
    assert model.prompts == ["Why are customers unhappy?"]


def test_timeout_is_raised_as_a_timeout_error():
    adapter = LLMAdapter(FakeChatModel([TimeoutError("request timed out")]), timeout=30)

    with pytest.raises(LLMTimeoutError) as raised:
        adapter.complete("hello")

    assert "30" in str(raised.value)


def test_an_unexpected_provider_error_is_raised_not_returned():
    adapter = LLMAdapter(FakeChatModel([ValueError("malformed request")]))

    with pytest.raises(LLMError):
        adapter.complete("hello")


# ---------------------------------------------------------------- retries


def test_rate_limits_are_retried_using_the_providers_own_wait():
    waits = []
    model = FakeChatModel([rate_limited(1.5), rate_limited(1.5), message("done")])
    adapter = LLMAdapter(model, max_retries=2, sleep=waits.append)

    assert adapter.complete("hello") == "done"
    assert waits == [2.0, 2.0]          # 1.5s asked for, plus 0.5s margin
    assert adapter.usage.retries == 2
    assert adapter.usage.llm_calls == 1  # one successful call


def test_rate_limit_falls_back_to_the_configured_wait_when_none_is_given():
    waits = []
    model = FakeChatModel([Exception("Error code: 429 rate_limit_exceeded"), message("done")])
    adapter = LLMAdapter(model, max_retries=1, retry_base_wait=3.0, sleep=waits.append)

    adapter.complete("hello")

    assert waits == [3.0]


def test_exhausted_retries_raise_a_rate_limit_error():
    waits = []
    model = FakeChatModel([rate_limited(), rate_limited(), rate_limited()])
    adapter = LLMAdapter(model, max_retries=1, sleep=waits.append)

    with pytest.raises(LLMRateLimitError):
        adapter.complete("hello")

    assert len(model.prompts) == 2  # the first attempt plus one retry
    assert len(waits) == 1


def test_other_errors_are_not_retried():
    waits = []
    model = FakeChatModel([ValueError("bad request"), message("never reached")])
    adapter = LLMAdapter(model, max_retries=3, sleep=waits.append)

    with pytest.raises(LLMError):
        adapter.complete("hello")

    assert len(model.prompts) == 1
    assert waits == []


# ---------------------------------------------------------------- usage accounting


def test_token_usage_is_recorded_when_the_provider_reports_it():
    model = FakeChatModel([
        message("one", input_tokens=120, output_tokens=30),
        message("two", input_tokens=80, output_tokens=20),
    ])
    adapter = LLMAdapter(model)

    adapter.complete("first")
    adapter.complete("second")

    assert adapter.usage.llm_calls == 2
    assert adapter.usage.input_tokens == 200
    assert adapter.usage.output_tokens == 50
    assert adapter.usage.total_tokens == 250


def test_calls_are_counted_even_when_the_provider_reports_no_tokens():
    adapter = LLMAdapter(FakeChatModel([message("no metadata")]))

    adapter.complete("hello")

    assert adapter.usage.llm_calls == 1
    assert adapter.usage.total_tokens == 0  # not guessed


def test_usage_can_be_reset():
    adapter = LLMAdapter(FakeChatModel([message("x", input_tokens=5, output_tokens=1)]))
    adapter.complete("hello")

    adapter.reset_usage()

    assert adapter.usage == adapter.usage.__class__()


# ---------------------------------------------------------------- structured output


def test_structured_output_returns_a_validated_object():
    model = FakeChatModel([Demo(**VALID)])
    adapter = LLMAdapter(model)

    result = adapter.structured("give me json", Demo)

    assert isinstance(result, Demo)
    assert (result.name, result.count) == ("battery", 3)
    assert model.structured_schemas == [Demo]
    assert adapter.usage.llm_calls == 1


def test_a_dict_response_is_validated_against_the_schema():
    adapter = LLMAdapter(FakeChatModel([VALID]))

    assert adapter.structured("give me json", Demo) == Demo(**VALID)


def test_json_written_as_content_instead_of_a_tool_call_is_recovered():
    import json

    model = FakeChatModel([ToolUseFailed(json.dumps(VALID))])
    adapter = LLMAdapter(model)

    result = adapter.structured("give me json", Demo)

    assert result == Demo(**VALID)
    assert adapter.usage.llm_calls == 1


def test_recovered_json_must_still_match_the_schema():
    import json

    model = FakeChatModel([ToolUseFailed(json.dumps({"name": "battery"}))])  # count missing
    adapter = LLMAdapter(model)

    with pytest.raises(LLMResponseError):
        adapter.structured("give me json", Demo)


def test_an_unrecoverable_structured_failure_raises():
    adapter = LLMAdapter(FakeChatModel([RuntimeError("connection reset")]))

    with pytest.raises(LLMResponseError):
        adapter.structured("give me json", Demo)


def test_a_structured_timeout_is_raised_as_a_timeout_error():
    adapter = LLMAdapter(FakeChatModel([TimeoutError("timed out")]))

    with pytest.raises(LLMTimeoutError):
        adapter.structured("give me json", Demo)


def test_structured_rate_limits_are_retried_too():
    import json

    waits = []
    model = FakeChatModel([rate_limited(), Demo(**VALID)])
    adapter = LLMAdapter(model, max_retries=1, sleep=waits.append)

    assert adapter.structured("give me json", Demo) == Demo(**VALID)
    assert waits == [2.0]
    assert json  # keep the import honest for linters
