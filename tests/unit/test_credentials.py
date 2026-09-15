"""
Credentials - feedbackiq.auth.credentials

The rules these tests protect: **a password is never stored or echoed as itself, a weak one
is refused with a message that says why, and every way verification can fail means "not
signed in" rather than a crash.**

Pure unit tests. Real argon2 hashing runs here (it is fast enough, and faking the hasher
would test nothing), but no database and no network.
"""

import pytest
from argon2 import PasswordHasher

from feedbackiq.auth.credentials import (
    MAX_EMAIL_CHARS,
    PASSWORD_MAX_CHARS,
    PASSWORD_MIN_CHARS,
    hash_password,
    needs_rehash,
    normalise_email,
    validate_email,
    validate_password,
    verify_against_dummy_hash,
    verify_password,
)
from feedbackiq.core.exceptions import CredentialError

GOOD_PASSWORD = "correct horse battery staple"


# ---------------------------------------------------------------- email


def test_an_email_is_trimmed_and_lower_cased():
    assert normalise_email("  Ana.Silva@Example.COM \n") == "ana.silva@example.com"


@pytest.mark.parametrize(
    "address",
    ["ana@example.com", "ANA@EXAMPLE.COM", "ana+feedback@mail.example.co.uk", " a@b.io "],
)
def test_a_valid_email_is_accepted_and_returned_normalised(address):
    assert validate_email(address) == normalise_email(address)


@pytest.mark.parametrize(
    "address",
    [
        "",
        "   ",
        "no-at-sign",
        "@example.com",
        "ana@",
        "ana@localhost",
        "ana@.com",
        "ana@example.",
        "ana silva@example.com",
        "ana@@example.com",
        "a" * MAX_EMAIL_CHARS + "@example.com",
    ],
)
def test_an_invalid_email_is_refused(address):
    with pytest.raises(CredentialError, match="valid email"):
        validate_email(address)


# ---------------------------------------------------------------- password policy


def test_a_password_at_the_minimum_length_is_accepted():
    validate_password("x" * PASSWORD_MIN_CHARS)


def test_a_short_password_is_refused_with_the_rule():
    with pytest.raises(CredentialError, match=f"at least {PASSWORD_MIN_CHARS}"):
        validate_password("x" * (PASSWORD_MIN_CHARS - 1))


def test_an_overlong_password_is_refused():
    with pytest.raises(CredentialError, match=f"at most {PASSWORD_MAX_CHARS}"):
        validate_password("x" * (PASSWORD_MAX_CHARS + 1))


def test_a_password_of_only_spaces_is_refused():
    with pytest.raises(CredentialError, match="only spaces"):
        validate_password(" " * PASSWORD_MIN_CHARS)


def test_a_password_equal_to_the_email_is_refused_whatever_its_case():
    with pytest.raises(CredentialError, match="email"):
        validate_password("Ana.Silva@Example.com", email="ana.silva@example.com")


def test_there_are_no_composition_rules():
    """Letters only, no digit or symbol: long is what counts (NIST SP 800-63B)."""
    validate_password("purple elephants dance quietly")


def test_a_policy_message_never_contains_the_password():
    secret = "tooshort"

    with pytest.raises(CredentialError) as caught:
        validate_password(secret)

    assert secret not in str(caught.value)


# ---------------------------------------------------------------- hashing


def test_a_hash_is_argon2id_and_not_the_password():
    stored = hash_password(GOOD_PASSWORD)

    assert stored.startswith("$argon2id$")
    assert GOOD_PASSWORD not in stored


def test_the_same_password_hashes_differently_each_time():
    """A per-hash random salt: identical passwords must not produce identical rows."""
    assert hash_password(GOOD_PASSWORD) != hash_password(GOOD_PASSWORD)


def test_the_right_password_verifies():
    assert verify_password(GOOD_PASSWORD, hash_password(GOOD_PASSWORD)) is True


def test_a_wrong_password_does_not_verify():
    assert verify_password("correct horse battery stapler", hash_password(GOOD_PASSWORD)) is False


@pytest.mark.parametrize("stored", ["", "not-a-hash", GOOD_PASSWORD, "$argon2id$v=19$broken"])
def test_a_corrupt_or_plaintext_hash_means_not_verified_rather_than_a_crash(stored):
    assert verify_password(GOOD_PASSWORD, stored) is False


def test_a_fresh_hash_does_not_need_rehashing():
    assert needs_rehash(hash_password(GOOD_PASSWORD)) is False


def test_a_hash_made_with_weaker_parameters_needs_rehashing():
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash(GOOD_PASSWORD)

    assert verify_password(GOOD_PASSWORD, weak) is True
    assert needs_rehash(weak) is True


def test_the_dummy_verification_runs_without_error():
    """Used when an email has no account. Its whole value is taking time; returning quietly
    is the contract, and timing itself is too noisy to assert in a unit test."""
    assert verify_against_dummy_hash(GOOD_PASSWORD) is None
