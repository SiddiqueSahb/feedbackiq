"""
Engine preprocessing - feedbackiq.engine.preprocessing

Protects the documented Milestone 3 decision: the engine passes the customer's text to
the model, changing only what is invisible to a tokenizer (control characters,
whitespace runs). It does NOT reproduce the training-time `clean_text()` - measured at
7 differing predictions in 1,200, with raw text scoring marginally higher. See the
module docstring and docs/production/milestone-03.md for the numbers.

If someone later makes the engine lower-case, strip URLs or demojize text, these tests
fail - which is the point: that is a behaviour change and needs its own measurement.
"""

import pytest

from feedbackiq.engine import normalise_text


def test_control_characters_are_removed():
    assert normalise_text("battery\x00 died\x07") == "battery died"


def test_whitespace_runs_collapse_and_edges_are_trimmed():
    assert normalise_text("  the   battery \t\n died  ") == "the battery died"


@pytest.mark.parametrize(
    "text",
    [
        "The Battery DIED",                      # casing is preserved
        "Battery died! Awful... 2/10?",          # punctuation is preserved
        "see https://example.com/returns",       # URLs are preserved
        "@acme your support ignored me",         # mentions are preserved
        "#broken after one week",                # hashtags are preserved
        "battery died 😕",                        # emoji are preserved
        "café crème",                            # non-ASCII is preserved
    ],
    ids=["casing", "punctuation", "url", "mention", "hashtag", "emoji", "non-ascii"],
)
def test_nothing_else_is_changed(text):
    assert normalise_text(text) == text


def test_non_string_input_is_tolerated():
    assert normalise_text(12345) == "12345"


def test_normalisation_is_idempotent():
    once = normalise_text("  battery\x00  died  ")
    assert normalise_text(once) == once
