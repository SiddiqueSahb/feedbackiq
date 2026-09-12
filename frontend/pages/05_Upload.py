"""
Batch Upload — score a batch of reviews from a CSV using a single sentiment
model.

Sample CSV (18 rows, real data, balanced across platforms/sentiment) is offered
so the page can be tried without assembling a file first.
"""

import os

import pandas as pd
import streamlit as st

import theme
from utils import api_get, api_post, page_header, sidebar_status

st.set_page_config(
    page_title="Upload | FeedbackIQ",
    page_icon="📤",
    layout="wide",
)

theme.inject()
sidebar_status()

MAX_ROWS = 200

# Resolved relative to this file, not cwd, so it works from either launch dir.
ASSETS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # frontend/
    "assets",
)

# Two samples: "processed" has corpus labels (checks model agreement); "raw" is
# uncleaned data/raw/ rows (checks the pipeline handles messy input).
SAMPLE_PROCESSED = os.path.join(ASSETS, "sample_reviews.csv")
SAMPLE_RAW = os.path.join(ASSETS, "sample_reviews_raw.csv")

page_header(
    "Batch Upload",
    f"Score sentiment for many reviews at once from a CSV. Capped at {MAX_ROWS} "
    "rows per upload to keep response times reasonable. This stage only — for the "
    "full pipeline on one review, use Review Analysis.",
    rq="RQ1",
)

# Sample files
with st.container(border=True):
    st.markdown("**Don't have a file to hand?**")
    st.caption(
        "Download either sample and upload it straight back. Both are 18 real reviews, "
        "balanced across the three platforms."
    )

    left, right = st.columns(2)

    with left:
        st.markdown("**Raw — unprocessed**")
        st.caption(
            "Lifted straight from `data/raw/` before `preprocess.py` ran. Contains "
            "`@mentions`, double spaces, embedded newlines, non-ASCII characters, and "
            "no rating at all on the Twitter rows. Use this to check the pipeline "
            "handles input it hasn't been cleaned for."
        )
        if os.path.exists(SAMPLE_RAW):
            with open(SAMPLE_RAW, "rb") as fh:
                st.download_button(
                    "Download raw sample",
                    fh.read(),
                    file_name="sample_reviews_raw.csv",
                    mime="text/csv",
                    type="primary",
                    use_container_width=True,
                )
        else:
            st.caption("Not found in `frontend/assets/`.")

    with right:
        st.markdown("**Processed — with labels**")
        st.caption(
            "Rows from the unified corpus, two per platform per sentiment class, "
            "carrying their corpus labels. Scoring this one reports how often the "
            "model agreed with the label."
        )
        if os.path.exists(SAMPLE_PROCESSED):
            with open(SAMPLE_PROCESSED, "rb") as fh:
                st.download_button(
                    "Download processed sample",
                    fh.read(),
                    file_name="sample_reviews.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
        else:
            st.caption("Not found in `frontend/assets/`.")

    tab_raw, tab_proc = st.tabs(["Preview raw", "Preview processed"])
    with tab_raw:
        if os.path.exists(SAMPLE_RAW):
            st.dataframe(pd.read_csv(SAMPLE_RAW),
                         use_container_width=True, hide_index=True)
            st.caption(
                "Note the Twitter rows have an empty `rating` — the raw source has no "
                "star rating, which is exactly the kind of gap a real upload contains."
            )
    with tab_proc:
        if os.path.exists(SAMPLE_PROCESSED):
            st.dataframe(pd.read_csv(SAMPLE_PROCESSED),
                         use_container_width=True, hide_index=True)

st.caption(
    "Your own file needs one column of review text. Any other columns are carried "
    "through untouched."
)

st.divider()

# Model
model_options = api_get("/api/sentiment/models")
if not isinstance(model_options, list) or not model_options:
    model_options = [{"id": "distilbert", "name": "DistilBERT (fine-tuned)", "tier": ""}]

model_labels = {m["id"]: f"{m['name']} — {m['tier']}".strip(" —") for m in model_options}
model_choices = list(model_labels.keys())

upload_col, model_col = st.columns([2, 1])

with upload_col:
    uploaded_file = st.file_uploader("Choose a CSV file", type=["csv"])

with model_col:
    default_index = (
        model_choices.index(st.session_state["last_model"])
        if st.session_state.get("last_model") in model_choices
        else 0
    )
    model_id = st.selectbox(
        "Sentiment model",
        options=model_choices,
        index=default_index,
        format_func=lambda x: model_labels[x],
    )
    st.session_state["last_model"] = model_id

# Scoring
if uploaded_file is not None:

    try:
        df = pd.read_csv(uploaded_file)
    except Exception as exc:
        st.error(f"Could not read that file as a CSV: {exc}")
        df = None

    if df is not None:

        if df.empty:
            st.warning("The uploaded file has no rows.")

        else:
            # Default to a column that looks like review text, so no selection needed.
            likely = [c for c in df.columns
                      if c.lower() in ("review_text", "text", "review", "comment", "body")]
            default_col = df.columns.get_loc(likely[0]) if likely else 0

            text_column = st.selectbox(
                "Which column contains the review text?",
                df.columns,
                index=int(default_col),
            )

            if len(df) > MAX_ROWS:
                st.warning(
                    f"File has {len(df):,} rows — only the first {MAX_ROWS} will be scored."
                )
                df = df.head(MAX_ROWS)

            if st.button("Score reviews", type="primary"):

                texts = df[text_column].dropna().astype(str).tolist()

                if not texts:
                    st.warning("No usable text found in that column.")

                else:
                    with st.spinner(f"Scoring {len(texts):,} reviews..."):
                        results = api_post(
                            "/api/sentiment/batch-predict",
                            {"texts": texts, "model": model_id},
                            timeout=120,
                        )

                    if results:
                        df_results = pd.DataFrame(results)
                        counts = df_results["label"].value_counts()

                        theme.kpi_grid([
                            ("Scored", f"{len(df_results):,}"),
                            ("Positive", str(int(counts.get("positive", 0)))),
                            ("Negative", str(int(counts.get("negative", 0)))),
                            ("Neutral", str(int(counts.get("neutral", 0)))),
                        ])

                        # Show agreement rate when the file has its own labels.
                        if "sentiment_label" in df.columns and len(df) == len(df_results):
                            truth = df["sentiment_label"].astype(str).str.lower().tolist()
                            pred = df_results["label"].astype(str).str.lower().tolist()
                            agree = sum(t == p for t, p in zip(truth, pred))
                            st.info(
                                f"The model agreed with the corpus label on "
                                f"**{agree} of {len(pred)}** rows "
                                f"({agree / len(pred):.0%}). Disagreements are usually "
                                "neutral-class cases — the class that separates these "
                                "models on the full corpus."
                            )

                        st.dataframe(df_results, use_container_width=True, hide_index=True)

                        st.download_button(
                            "Download results as CSV",
                            df_results.to_csv(index=False).encode("utf-8"),
                            file_name="feedbackiq_sentiment_results.csv",
                            mime="text/csv",
                        )
