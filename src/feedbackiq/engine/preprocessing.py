"""
What the engine does to text before a model sees it - and, just as importantly,
what it deliberately does not do.

BACKGROUND

The fine-tuned DistilBERT model was trained on the corpus's `cleaned_text` column,
produced by `clean_text()` (notebook 1 and `scripts/preprocess.py`): strip URLs and
@mentions, keep hashtag words, strip HTML, convert emoji to words via the `emoji`
package, drop non-ASCII, collapse whitespace, lower-case. The serving path has always
passed the caller's text straight to the tokenizer instead. Milestone 1 flagged that
mismatch; Milestone 3 measured it.

MEASUREMENT (1,200 labelled reviews, 400 per class, seed 42, fine-tuned model)

    input                                  accuracy   macro F1   predictions differing
                                                                 from training-style input
    raw text (what the API sends)           0.8217     0.8214     7 / 1200
    corpus cleaned_text (training-style)    0.8175     0.8170     -
    clean_text() without the emoji step     0.8183     0.8178     1 / 1200

DECISION: keep serving the caller's text, normalised only as below.

Reasons:
  * The mismatch is immaterial: 7 of 1,200 predictions differ (0.6%), and raw text
    scores marginally *higher*, not lower. There is no accuracy case for changing it.
  * The tokenizer is uncased and handles punctuation, URLs and mentions itself, so
    most of `clean_text()` is redundant for this model. It mattered for the TF-IDF
    classical models, which do their own cleaning in `nlp/classical_models.py`.
  * Reproducing training exactly would need the `emoji` package: emoji appear in only
    3,222 of 642,692 corpus rows (0.50%), and the standard library cannot substitute
    (`unicodedata` gives "thumbs down sign" where `emoji` gives "thumbs down", and
    multi-codepoint sequences such as flags have no single name at all). Adding a
    dependency to move 7 predictions in 1,200 is not a good trade.
  * Keeping the serving path unchanged also keeps this milestone structural: the
    deterministic prediction snapshot stays byte-identical.

The dissertation benchmark is untouched: it evaluates `cleaned_text`, as published.
The production benchmark added in this milestone measures what the engine actually
serves, and carries a metric floor.
"""

from __future__ import annotations

import re

# Control characters carry no meaning and break some tokenizers and CSV exports.
# This is the same set the API schemas strip, applied here so the engine is safe to
# use without the API in front of it.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalise_text(text: str) -> str:
    """
    The only text change the engine makes: drop control characters and collapse
    whitespace runs to single spaces.

    Both are invisible to a WordPiece tokenizer, which splits on whitespace anyway,
    so this does not alter model output - it just means one record with a stray tab or
    a null byte cannot poison a batch or a later CSV export. Casing, punctuation,
    URLs, mentions and emoji are left exactly as the customer wrote them.
    """
    cleaned = _CONTROL_CHARACTERS.sub("", str(text))

    return _WHITESPACE_RUN.sub(" ", cleaned).strip()
