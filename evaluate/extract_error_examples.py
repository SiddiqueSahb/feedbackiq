"""
Extracts the qualitative error-analysis examples reported in Chapter 4, §4.5.4.

Joins the cached per-review predictions from the diagnostic sample back to the
source review text and isolates the three error patterns discussed:

  Pattern 1  true neutral, RoBERTa polar, DistilBERT correct  -> label mismatch
  Pattern 2  true negative, VADER positive                    -> lexicon failure
  Pattern 3  all five models wrong                            -> labelling ceiling

Usage:  python evaluate/extract_error_examples.py
"""
import pandas as pd

PRED = "data/results/sentiment_diagnostics/predictions.csv"
CORPUS = "data/processed/reviews_unified.parquet"

V = "pred::VADER"
NB = "pred::Naive Bayes"
LR = "pred::Logistic Regression"
RB = "pred::RoBERTa (pre-trained)"
DB = "pred::DistilBERT (fine-tuned)"
ALL = [V, NB, LR, RB, DB]


def load():
    pred = pd.read_csv(PRED)
    corpus = pd.read_parquet(
        CORPUS, columns=["review_id", "text", "rating", "platform"])
    pred["review_id"] = pred["review_id"].astype(str)
    corpus["review_id"] = corpus["review_id"].astype(str)
    return pred.merge(corpus, on="review_id", how="left", suffixes=("", "_c"))


def show(df, title, n=6, fields=()):
    print(f"\n{'=' * 72}\n{title}  (n = {len(df)})\n{'=' * 72}")
    for _, r in df.head(n).iterrows():
        meta = f"[{r.platform} | rating {r.rating} | true={r.true_label}]"
        extra = "  ".join(f"{k}={r[k]}" for k in fields)
        print(f"{meta} {extra}")
        print("   ", " ".join(str(r.text).split())[:200], "\n")


def main():
    m = load()
    m = m[m.text.notna()]
    print(f"merged rows: {len(m)}")

    p1 = m[(m.true_label == "neutral") & (m[RB] != "neutral") & (m[DB] == "neutral")]
    show(p1, "Pattern 1 - mixed evaluative content in three-star reviews",
         fields=(RB, V))

    p2 = m[(m.true_label == "negative") & (m[V] == "positive")]
    show(p2, "Pattern 2 - positive vocabulary in negative reviews",
         fields=("vader_compound",))

    p3 = m[m.apply(lambda r: all(r[k] != r.true_label for k in ALL), axis=1)]
    show(p3, "Pattern 3 - all five models misclassify")

    print(f"\nSummary: pattern 1 = {len(p1)}, pattern 2 = {len(p2)}, "
          f"pattern 3 = {len(p3)} of {len(m)} sampled reviews "
          f"({len(p3) / len(m):.1%} for pattern 3)")


if __name__ == "__main__":
    main()
