"""
Session tokens - feedbackiq.auth.session_tokens

The properties that make a stolen database useless as a set of cookies: **tokens are
unguessable and unique, what is stored is a one-way hash of them, and malformed values are
recognised without asking the database.**
"""

import re

import pytest

from feedbackiq.auth.session_tokens import (
    hash_session_token,
    is_well_formed,
    new_session_token,
)


def test_a_token_is_43_url_safe_characters():
    token = new_session_token()

    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    assert is_well_formed(token)


def test_tokens_do_not_repeat():
    assert len({new_session_token() for _ in range(1000)}) == 1000


def test_the_stored_hash_is_sha256_hex_and_is_not_the_token():
    token = new_session_token()
    stored = hash_session_token(token)

    assert re.fullmatch(r"[0-9a-f]{64}", stored)
    assert token not in stored


def test_the_same_token_always_hashes_the_same_way():
    """Deterministic, unlike a password hash: it is how a request finds its session row."""
    token = new_session_token()

    assert hash_session_token(token) == hash_session_token(token)
    assert hash_session_token(token) != hash_session_token(new_session_token())


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "short",
        "a" * 42,
        "a" * 44,
        "a" * 42 + "!",
        "a" * 42 + " ",
        "a" * 42 + "=",
        "Bearer " + "a" * 36,
        "../../" + "a" * 37,
    ],
)
def test_anything_not_shaped_like_our_token_is_malformed(value):
    assert is_well_formed(value) is False
