"""
CSV validation - feedbackiq.ingestion.csv_reader

The rule these tests protect: **malformed input never becomes valid data, and one bad row
never costs a customer the whole upload.** A row is either accepted, or rejected with the
line number the customer can find in their own file.

Pure unit tests: bytes in, dataclasses out. No database, no engine, no fixtures.
"""

import pytest

from feedbackiq.ingestion.csv_reader import (
    MAX_TEXT_CHARS,
    CsvFormatError,
    parse_csv,
)

VALID = b"""text,external_id,created_at,rating,platform
The app crashes on launch,R-1,2026-02-01,1,app store
Waited forty minutes,R-2,2026-02-02T09:30:00,4,yelp
"""


def texts(result):
    return [row.text for row in result.rows]


def errors_for(result, field):
    return [error for error in result.errors if error.field == field]


# ---------------------------------------------------------------- the happy path


def test_a_valid_csv_is_accepted_in_full():
    result = parse_csv(VALID)

    assert result.received == 2
    assert result.accepted == 2
    assert result.rejected == 0
    assert texts(result) == ["The app crashes on launch", "Waited forty minutes"]


def test_optional_fields_are_normalised():
    [first, second] = parse_csv(VALID).rows

    assert first.external_id == "R-1"
    assert first.rating == 1.0
    assert first.platform == "app store"
    # Date-only values are read as UTC rather than as the server's local time, because the
    # column they land in is timestamptz.
    assert first.created_at.isoformat() == "2026-02-01T00:00:00+00:00"
    assert second.created_at.hour == 9


def test_only_the_text_column_is_required():
    result = parse_csv(b"text\nJust one column of comments\n")

    assert result.accepted == 1
    row = result.rows[0]
    assert (row.external_id, row.created_at, row.rating, row.platform) == (None, None, None, None)


@pytest.mark.parametrize("header", [b"feedback", b"comment", b"review", b"body", b"TEXT", b" Text "])
def test_common_names_for_the_text_column_are_accepted(header):
    """What people actually put in a spreadsheet header."""
    result = parse_csv(header + b"\nThe delivery never arrived\n")

    assert texts(result) == ["The delivery never arrived"]


def test_row_numbers_match_the_customers_file():
    """Row 1 is the header, so the first data row is row 2 - what their spreadsheet shows."""
    result = parse_csv(b"text\nfirst\n\nthird\n")

    assert [row.row_number for row in result.rows] == [2, 4]


def test_a_blank_trailing_line_is_not_an_error():
    result = parse_csv(b"text\nsomething\n\n\n")

    assert result.accepted == 1
    assert result.errors == []


# ---------------------------------------------------------------- whole-file failures


@pytest.mark.parametrize(
    "raw, expected",
    [
        (b"", "empty"),
        (b"   \n  ", "empty"),
        (b"name,rating\nAcme,5\n", "No feedback text column"),
    ],
    ids=["empty-file", "whitespace-only", "no-text-column"],
)
def test_an_unusable_file_is_refused_outright(raw, expected):
    with pytest.raises(CsvFormatError) as error:
        parse_csv(raw)

    assert expected in str(error.value)


def test_the_missing_column_message_names_what_was_found():
    """An actionable error: what is missing and what the file actually had."""
    with pytest.raises(CsvFormatError) as error:
        parse_csv(b"name,rating\nAcme,5\n")

    message = str(error.value)
    assert "'text'" in message
    assert "name" in message and "rating" in message


def test_too_many_rows_is_refused_rather_than_truncated():
    """Silently importing the first N rows would lose data without telling anyone."""
    raw = b"text\n" + b"a complaint\n" * 12

    with pytest.raises(CsvFormatError) as error:
        parse_csv(raw, max_rows=10)

    assert "at most" in str(error.value)


# ---------------------------------------------------------------- row-level rejection


def test_empty_text_is_rejected_with_its_row_number():
    result = parse_csv(b"text,external_id\nreal feedback,R-1\n   ,R-2\n")

    assert texts(result) == ["real feedback"]
    [error] = result.errors
    assert (error.row, error.field) == (3, "text")
    assert "empty" in error.message


def test_text_of_only_control_characters_is_empty_after_cleaning():
    result = parse_csv(b"text\n\x00\x07\x1f\n")

    assert result.accepted == 0
    assert errors_for(result, "text")


def test_oversized_text_is_rejected():
    raw = b"text\n" + (b"x" * (MAX_TEXT_CHARS + 1)) + b"\n"

    result = parse_csv(raw)

    assert result.accepted == 0
    [error] = result.errors
    assert error.field == "text"
    assert "longer than" in error.message


@pytest.mark.parametrize(
    "value", ["not-a-date", "2026-13-45", "yesterday", "01/02/26 maybe"]
)
def test_an_invalid_date_is_rejected(value):
    result = parse_csv(f"text,created_at\nsomething,{value}\n".encode())

    assert result.accepted == 0
    [error] = result.errors
    assert error.field == "created_at"
    assert value in error.message


@pytest.mark.parametrize("value", ["2026-02-01", "2026-02-01 10:30:00", "01/02/2026", "2026/02/01"])
def test_common_date_formats_are_accepted(value):
    result = parse_csv(f"text,created_at\nsomething,{value}\n".encode())

    assert result.accepted == 1, result.errors[0].message if result.errors else ""
    assert result.rows[0].created_at is not None


@pytest.mark.parametrize(
    "value, reason",
    [("nine", "not a number"), ("0", "outside"), ("6", "outside"), ("-3", "outside")],
)
def test_an_invalid_rating_is_rejected(value, reason):
    result = parse_csv(f"text,rating\nsomething,{value}\n".encode())

    assert result.accepted == 0
    [error] = result.errors
    assert error.field == "rating"
    assert reason in error.message


@pytest.mark.parametrize("value", ["1", "5", "3.5"])
def test_a_valid_rating_is_kept(value):
    result = parse_csv(f"text,rating\nsomething,{value}\n".encode())

    assert result.rows[0].rating == float(value)


def test_a_row_with_more_values_than_columns_is_rejected():
    """Malformed rather than silently truncated: the extra value might be the real text."""
    result = parse_csv(b"text,rating\nsomething,3,unexpected extra\n")

    assert result.accepted == 0
    [error] = result.errors
    assert error.field == "row"


def test_one_bad_row_does_not_stop_the_others():
    result = parse_csv(
        b"text,rating\ngood one,5\n,3\nanother good one,4\nbad rating,99\nthird good one,1\n"
    )

    assert texts(result) == ["good one", "another good one", "third good one"]
    assert len(result.errors) == 2
    assert result.received == 5


# ---------------------------------------------------------------- duplicate identity


def test_a_repeated_external_id_within_one_file_is_kept_once():
    result = parse_csv(b"text,external_id\nfirst,R-1\nsecond,R-1\nthird,R-2\n")

    assert texts(result) == ["first", "third"]
    assert result.duplicates_in_file == 1
    # Counted as rejected, but not as a row *error* - it is not malformed, just repeated.
    assert result.errors == []
    assert result.rejected == 1


def test_rows_without_an_external_id_are_never_duplicates_of_each_other():
    """Most uploads have no identifier column; identical-looking rows may be genuine."""
    result = parse_csv(b"text\nsame words\nsame words\n")

    assert result.accepted == 2
    assert result.duplicates_in_file == 0


def test_an_oversized_external_id_is_rejected():
    result = parse_csv(("text,external_id\nsomething," + "x" * 300 + "\n").encode())

    assert result.accepted == 0
    assert errors_for(result, "external_id")


# ---------------------------------------------------------------- encodings


def test_a_utf8_bom_from_excel_does_not_break_the_header():
    result = parse_csv("﻿text\ncafé service was poor\n".encode("utf-8"))

    assert texts(result) == ["café service was poor"]


def test_non_utf8_bytes_degrade_rather_than_losing_the_file():
    """Latin-1 as a last resort: one mojibake row beats refusing the whole upload."""
    result = parse_csv("text\ncaf\xe9 was cold\n".encode("latin-1"))

    assert result.accepted == 1
