"""
Reading and validating a customer's CSV.

    result = parse_csv(raw_bytes)
    result.rows      # ParsedFeedback, ready to store
    result.errors    # RowError, one per rejected row, with the row number
    result.duplicates_in_file

One required column, `text`. Everything else is optional, because the first thing a
customer will try is a spreadsheet with one column of comments:

    text                                   required, non-empty after cleaning
    external_id                            their own identifier for the row
    created_at                             when the customer wrote it (ISO 8601 or a few
                                           common formats)
    rating                                 numeric, 1-5
    platform                               free text, e.g. "app store", "support"

Design notes:

* **stdlib `csv`, not pandas.** Row-level error reporting is the whole point here, and
  `csv.DictReader` gives line-by-line control. pandas would coerce types silently and
  report failures per column, which is the opposite of what an import report needs.
* **Nothing is dropped quietly.** Every rejected row produces a `RowError` carrying the
  row number, the field and a message safe to show a customer.
* **No database, no HTTP, no engine.** This module works on bytes and returns dataclasses.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

# The column that must be present. Matched case-insensitively, and a few obvious
# alternatives are accepted because "feedback" and "comment" are what people actually
# write in a spreadsheet header.
TEXT_COLUMNS = ("text", "feedback", "comment", "review", "body")
EXTERNAL_ID_COLUMNS = ("external_id", "id", "review_id", "ticket_id")
CREATED_AT_COLUMNS = ("created_at", "date", "created", "submitted_at", "timestamp")
RATING_COLUMNS = ("rating", "score", "stars")
PLATFORM_COLUMNS = ("platform", "source", "channel")

# Limits. Generous enough for real feedback, small enough that one row cannot exhaust
# memory or blow past the model's context.
MAX_TEXT_CHARS = 20_000
MAX_EXTERNAL_ID_CHARS = 200
MAX_PLATFORM_CHARS = 100
MAX_ROWS = 50_000

MIN_RATING = 1.0
MAX_RATING = 5.0

# Control characters that have no business in feedback text. Same expression the API's
# schemas already use, so cleaning is consistent across entry points.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
)


@dataclass(frozen=True)
class ParsedFeedback:
    """One accepted row, normalised and ready for the database."""

    text: str
    # The line number in the uploaded file, so an import report points at something the
    # customer can actually find in their spreadsheet.
    row_number: int
    external_id: str | None = None
    created_at: datetime | None = None
    rating: float | None = None
    platform: str | None = None

    def as_record(self) -> dict:
        """The shape `db.persistence.save_feedback(items=...)` expects."""
        metadata: dict[str, object] = {"source_row": self.row_number}
        if self.platform:
            metadata["platform"] = self.platform

        return {
            "text": self.text,
            "external_id": self.external_id,
            "rating": self.rating,
            "feedback_at": self.created_at,
            "metadata": metadata,
        }


class RowError(Exception):
    """
    One rejected row. `row` is the line number a customer can find in their own file.

    An exception rather than a plain record, because the row validators are small
    functions and raising is how they abandon a row without each caller checking a return
    value. `parse_csv` catches it and collects it into the import report.
    """

    def __init__(self, row: int, field: str, message: str) -> None:
        super().__init__(f"row {row}: {field}: {message}")
        self.row = row
        self.field = field
        self.message = message

    def as_dict(self) -> dict:
        return {"row": self.row, "field": self.field, "message": self.message}


@dataclass
class ParseResult:
    rows: list[ParsedFeedback] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    # Rows skipped because an earlier row in the *same file* had the same external_id.
    duplicates_in_file: int = 0

    @property
    def received(self) -> int:
        """Data rows read, accepted or not."""
        return len(self.rows) + len(self.errors) + self.duplicates_in_file

    @property
    def accepted(self) -> int:
        return len(self.rows)

    @property
    def rejected(self) -> int:
        return len(self.errors) + self.duplicates_in_file


class CsvFormatError(ValueError):
    """The file as a whole cannot be read - not one bad row, but no usable CSV at all."""


def parse_csv(raw: bytes, *, max_rows: int = MAX_ROWS) -> ParseResult:
    """
    Validate a CSV and return accepted rows alongside per-row errors.

    Raises `CsvFormatError` only for whole-file problems: undecodable bytes, no header,
    a missing `text` column, or more than `max_rows` data rows. Everything else is a row
    error, because one malformed line must not cost a customer their whole upload.
    """
    text = _decode(raw)

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise CsvFormatError("The file is empty: expected a header row with a 'text' column.")

    columns = _map_columns(reader.fieldnames)
    if "text" not in columns:
        found = ", ".join(name for name in reader.fieldnames if name) or "none"
        raise CsvFormatError(
            "No feedback text column found. Add a column named 'text' "
            f"(also accepted: {', '.join(TEXT_COLUMNS[1:])}). Columns found: {found}."
        )

    result = ParseResult()
    seen_external_ids: set[str] = set()

    for raw_row in reader:
        # `reader.line_num` is the real line number in the customer's file, which is what an
        # import report has to quote. Counting iterations instead would drift: csv.DictReader
        # silently skips blank lines, and a quoted field may span several lines.
        position = reader.line_num

        if len(result.rows) + len(result.errors) + result.duplicates_in_file >= max_rows:
            raise CsvFormatError(
                f"Too many rows: this endpoint accepts at most {max_rows:,} per upload."
            )

        if _is_blank(raw_row):
            continue

        try:
            parsed = _parse_row(raw_row, columns, position)
        except RowError as error:  # noqa: PERF203 - one error per row is the point
            result.errors.append(error)
            continue

        if parsed.external_id is not None:
            if parsed.external_id in seen_external_ids:
                result.duplicates_in_file += 1
                continue
            seen_external_ids.add(parsed.external_id)

        result.rows.append(parsed)

    return result


# ---------------------------------------------------------------- internals


def _decode(raw: bytes) -> str:
    """
    Bytes to text, tolerating what spreadsheets actually produce.

    UTF-8 first, then UTF-8 with a BOM (Excel on Windows), then Latin-1 as a last resort
    so an unusual encoding degrades to mojibake in one row rather than losing the file.
    """
    if not raw.strip():
        raise CsvFormatError("The file is empty.")

    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    raise CsvFormatError("The file is not readable as text. Save it as UTF-8 CSV and retry.")


def _map_columns(fieldnames: list[str]) -> dict[str, str]:
    """Map our field names to the customer's actual header names."""
    lowered = {
        (name or "").strip().lower(): name
        for name in fieldnames
        if name is not None
    }

    mapping: dict[str, str] = {}
    for field_name, candidates in (
        ("text", TEXT_COLUMNS),
        ("external_id", EXTERNAL_ID_COLUMNS),
        ("created_at", CREATED_AT_COLUMNS),
        ("rating", RATING_COLUMNS),
        ("platform", PLATFORM_COLUMNS),
    ):
        for candidate in candidates:
            if candidate in lowered:
                mapping[field_name] = lowered[candidate]
                break

    return mapping


def _is_blank(row: dict) -> bool:
    """A trailing empty line in a spreadsheet export is not an error."""
    return all(not str(value or "").strip() for value in row.values())


def _parse_row(row: dict, columns: dict[str, str], position: int) -> ParsedFeedback:
    raw_text = row.get(columns["text"])

    # csv.DictReader puts extra unnamed fields under None; a row with more cells than the
    # header is malformed rather than silently truncated.
    if None in row and any(str(v or "").strip() for v in (row[None] or [])):
        raise RowError(position, "row", "More values than columns in the header.")

    text = _clean_text(str(raw_text or ""))
    if not text:
        raise RowError(position, "text", "Feedback text is empty.")
    if len(text) > MAX_TEXT_CHARS:
        raise RowError(
            position, "text", f"Feedback text is longer than {MAX_TEXT_CHARS:,} characters."
        )

    return ParsedFeedback(
        text=text,
        row_number=position,
        external_id=_parse_external_id(row, columns, position),
        created_at=_parse_created_at(row, columns, position),
        rating=_parse_rating(row, columns, position),
        platform=_parse_platform(row, columns, position),
    )


def _clean_text(value: str) -> str:
    return _CONTROL_CHARACTERS.sub("", value).strip()


def _cell(row: dict, columns: dict[str, str], field_name: str) -> str | None:
    if field_name not in columns:
        return None

    value = str(row.get(columns[field_name]) or "").strip()

    return value or None


def _parse_external_id(row: dict, columns: dict[str, str], position: int) -> str | None:
    value = _cell(row, columns, "external_id")
    if value is None:
        return None

    if len(value) > MAX_EXTERNAL_ID_CHARS:
        raise RowError(
            position, "external_id", f"Longer than {MAX_EXTERNAL_ID_CHARS} characters."
        )

    return value


def _parse_created_at(row: dict, columns: dict[str, str], position: int) -> datetime | None:
    value = _cell(row, columns, "created_at")
    if value is None:
        return None

    parsed = _parse_datetime(value)
    if parsed is None:
        raise RowError(
            position,
            "created_at",
            f"'{value}' is not a recognised date. Use YYYY-MM-DD or an ISO 8601 timestamp.",
        )

    return parsed


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed: datetime | date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = None  # type: ignore[assignment]

        for pattern in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(value, pattern)
                break
            except ValueError:
                continue

    if parsed is None:
        return None

    # Stored in a timestamptz column, so a date-only or naive value is read as UTC rather
    # than as the server's local time.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed


def _parse_rating(row: dict, columns: dict[str, str], position: int) -> float | None:
    value = _cell(row, columns, "rating")
    if value is None:
        return None

    try:
        rating = float(value)
    except ValueError:
        raise RowError(position, "rating", f"'{value}' is not a number.") from None

    if not MIN_RATING <= rating <= MAX_RATING:
        raise RowError(
            position, "rating", f"Rating {rating:g} is outside {MIN_RATING:g}-{MAX_RATING:g}."
        )

    return rating


def _parse_platform(row: dict, columns: dict[str, str], position: int) -> str | None:
    value = _cell(row, columns, "platform")
    if value is None:
        return None

    if len(value) > MAX_PLATFORM_CHARS:
        raise RowError(position, "platform", f"Longer than {MAX_PLATFORM_CHARS} characters.")

    return _clean_text(value) or None
