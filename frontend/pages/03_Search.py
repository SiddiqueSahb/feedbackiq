"""
Semantic Search — find reviews similar in meaning to a natural language query,
using the FAISS index rather than exact keyword matching.
"""

import os
import sys

import pandas as pd
import streamlit as st

sys.path.append(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

import theme
from utils import api_post, page_header, sidebar_status

st.set_page_config(
    page_title="Search | FeedbackIQ",
    page_icon="🔎",
    layout="wide",
)

theme.inject()
sidebar_status()

page_header(
    "Semantic Search",
    "Search by **meaning, not keywords**. \"Phone won't turn on\" also matches "
    "\"device is dead\" — the query and every review are compared as embeddings, "
    "so wording doesn't have to agree.",
    rq="RQ4",
)

EXAMPLES = [
    "battery draining too fast",
    "rude staff at the counter",
    "package arrived damaged",
    "flight delayed with no explanation",
]

# Query
# Filters sit beside the query, not the sidebar — easy to leave one on by accident there.
query = st.text_input(
    "Search query",
    value=st.session_state.get("search_query", ""),
    placeholder="e.g. battery draining too fast",
)

f1, f2, f3, f4 = st.columns([1, 1, 1, 1])
with f1:
    platform_filter = st.selectbox("Platform", ["Any", "amazon", "yelp", "twitter_airline"])
with f2:
    sentiment_filter = st.selectbox("Sentiment", ["Any", "positive", "negative", "neutral"])
with f3:
    min_rating = st.slider("Minimum rating", 1.0, 5.0, 1.0, 0.5)
with f4:
    top_k = st.slider("Results", 5, 50, 10)

search_clicked = st.button("Search", type="primary")

active = [f for f in (
    None if platform_filter == "Any" else f"platform = {platform_filter}",
    None if sentiment_filter == "Any" else f"sentiment = {sentiment_filter}",
    None if min_rating == 1.0 else f"rating ≥ {min_rating}",
) if f]
if active:
    st.caption("Filters active: " + " · ".join(active))

st.divider()

# Empty state
if not search_clicked and not query.strip():
    st.caption("Try one of these:")
    cols = st.columns(len(EXAMPLES))
    for col, example in zip(cols, EXAMPLES):
        with col:
            if st.button(example, use_container_width=True, key=f"ex_{example[:14]}"):
                st.session_state["search_query"] = example
                st.rerun()

# Results
if search_clicked:

    if len(query.strip()) < 2:
        st.warning("Please enter a search query of at least 2 characters.")

    else:
        with st.spinner("Searching 642,692 reviews..."):
            results = api_post(
                "/api/search",
                {
                    "query": query,
                    "top_k": top_k,
                    "platform_filter": None if platform_filter == "Any" else platform_filter,
                    "sentiment_filter": None if sentiment_filter == "Any" else sentiment_filter,
                    "min_rating": min_rating,
                },
            )

        if results:
            df = pd.DataFrame(results)
            scores = df.get("similarity_score")

            head = st.columns(3)
            with head[0], st.container(border=True):
                st.metric("Matches", len(df))
            with head[1], st.container(border=True):
                st.metric("Best similarity", f"{scores.max():.3f}" if scores is not None else "—")
            with head[2], st.container(border=True):
                st.metric("Median similarity", f"{scores.median():.3f}" if scores is not None else "—")

            tab_cards, tab_table = st.tabs(["Reviews", "Table"])

            with tab_cards:
                for _, row in df.iterrows():
                    with st.container(border=True):
                        meta = [
                            f"**{row.get('platform', 'unknown')}**",
                            f"★ {row.get('rating', 'N/A')}",
                            str(row.get("sentiment_label", "unknown")),
                        ]
                        score = row.get("similarity_score")
                        if score is not None:
                            meta.append(f"similarity {float(score):.3f}")
                        st.caption(" · ".join(meta))
                        st.write(row.get("text", ""))

            with tab_table:
                renamed = df.rename(columns={
                    "text": "Review",
                    "platform": "Platform",
                    "rating": "Rating",
                    "sentiment_label": "Sentiment",
                    "similarity_score": "Similarity",
                })
                cols = [c for c in ("Review", "Platform", "Rating", "Sentiment", "Similarity")
                        if c in renamed.columns]
                st.dataframe(renamed[cols], use_container_width=True, hide_index=True)
                st.download_button(
                    "Download results as CSV",
                    renamed[cols].to_csv(index=False).encode("utf-8"),
                    file_name="feedbackiq_search_results.csv",
                    mime="text/csv",
                )

        elif results is not None:
            st.info(
                "No reviews matched. If filters are active, try clearing them — "
                "semantic search finds near matches, but a filter is a hard cut."
            )

st.divider()
st.caption(
    "all-MiniLM-L6-v2 embeddings over a FAISS index of the full corpus. "
    "Measured at 21.7 ms mean retrieval time with precision@3 of 0.90, "
    "5.9× faster than a TF-IDF baseline at the same precision."
)
