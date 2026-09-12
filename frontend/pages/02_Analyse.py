"""
Review Analysis — run a single review through the full FeedbackIQ pipeline:
sentiment, complaint category, similar past reviews and an LLM business
summary.

Tabbed rather than stacked — one scroll made it hard to tell which output came
from which stage.
"""


import pandas as pd
import plotly.express as px
import streamlit as st

import theme
from utils import api_get, api_post, page_header, sidebar_status, SENTIMENT_COLORS

st.set_page_config(
    page_title="Analyse | FeedbackIQ",
    page_icon="🧠",
    layout="wide",
)

theme.inject()
sidebar_status()

page_header(
    "Review Analysis",
    "Run a single review through the whole pipeline — sentiment, complaint "
    "category, similar historical reviews and an LLM business summary — and see "
    "what each stage contributes.",
    rq=["RQ1", "RQ3"],
)

EXAMPLE = (
    "The battery stopped charging after one week and customer support never "
    "replied to my emails."
)

# Model list comes from the backend so it can't drift out of sync with the API.
model_options = api_get("/api/sentiment/models")
if not isinstance(model_options, list) or not model_options:
    model_options = [{"id": "distilbert", "name": "DistilBERT (fine-tuned)", "tier": ""}]

model_labels = {m["id"]: f"{m['name']} — {m['tier']}".strip(" —") for m in model_options}
model_choices = list(model_labels.keys())

# Input
col_text, col_opts = st.columns([3, 1])

with col_text:
    review_text = st.text_area(
        "Customer review",
        value=st.session_state.get("analyse_text", ""),
        height=160,
        placeholder=f"e.g. {EXAMPLE}",
    )

with col_opts:
    platform = st.selectbox("Platform (optional)", ["", "amazon", "yelp", "twitter_airline"])

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
    st.caption(
        "Five models are available. The fine-tuned DistilBERT is the production "
        "path; the others are the study's baselines."
    )

run_col, example_col = st.columns([1, 4])
with run_col:
    analyse_clicked = st.button("Analyse review", type="primary", use_container_width=True)
with example_col:
    if st.button("Use an example review"):
        st.session_state["analyse_text"] = EXAMPLE
        st.rerun()

st.divider()

# Empty state
if not analyse_clicked:
    with st.container(border=True):
        st.markdown("**What this will show**")
        c1, c2, c3, c4 = st.columns(4)
        for col, (stage, detail) in zip(
            (c1, c2, c3, c4),
            [("Sentiment", "predicted label with per-class confidence"),
             ("Category", "assigned complaint category, or Unclassified"),
             ("Business analysis", "summary, severity, priority, department"),
             ("Similar reviews", "nearest matches from the indexed corpus")],
        ):
            with col:
                st.markdown(f"**{stage}**")
                st.caption(detail)

# Results
if analyse_clicked:

    if len(review_text.strip()) < 3:
        st.warning("Please enter a review of at least 3 characters.")

    else:
        with st.spinner("Running the full analysis pipeline — this can take a few seconds..."):
            result = api_post(
                "/api/sentiment/analyse",
                {"text": review_text, "platform": platform or None, "model": model_id},
                timeout=90,
            )

        if result:
            sentiment = result.get("sentiment", {})
            analysis = result.get("analysis", {})

            tab_sent, tab_cat, tab_llm, tab_similar = st.tabs(
                ["Sentiment", "Complaint category", "Business analysis", "Similar reviews"]
            )

            # Sentiment
            with tab_sent:
                left, right = st.columns([1, 2])

                with left, st.container(border=True):
                    st.metric(
                        "Predicted sentiment",
                        sentiment.get("label", "unknown").capitalize(),
                        f"{sentiment.get('confidence', 0):.0%} confidence",
                    )
                    st.caption(f"Model: {model_labels.get(model_id, model_id)}")

                with right:
                    scores = sentiment.get("scores", {})
                    if scores:
                        df_scores = pd.DataFrame(
                            {"Sentiment": list(scores.keys()), "Score": list(scores.values())}
                        )
                        fig = px.bar(
                            df_scores, x="Sentiment", y="Score",
                            color="Sentiment",
                            color_discrete_map=SENTIMENT_COLORS,
                            range_y=[0, 1],
                        )
                        fig.update_layout(
                            height=260, showlegend=False,
                            plot_bgcolor="white",
                            margin=dict(l=10, r=10, t=10, b=10),
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.caption("This model does not expose per-class scores.")

            # Category
            with tab_cat:
                primary = result.get("category", "N/A")
                if str(primary).lower().startswith("unclassified"):
                    st.warning(f"**{primary}**", icon="⚠️")
                    st.caption(
                        "The categoriser records a weak match as unclassified rather than "
                        "forcing it into the nearest bucket. 27.6% of held-out reviews land "
                        "here — an unflattering number the design deliberately allows to show."
                    )
                else:
                    st.success(f"**{primary}**", icon="✅")

                categories = result.get("categories", [])
                if categories:
                    st.caption("All candidate categories, scored:")
                    st.dataframe(pd.DataFrame(categories),
                                 use_container_width=True, hide_index=True)

            # LLM analysis
            with tab_llm:
                if analysis.get("summary"):
                    facts = st.columns(3)
                    for col, key, label in zip(
                        facts, ("severity", "priority", "department"),
                        ("Severity", "Priority", "Department"),
                    ):
                        with col, st.container(border=True):
                            st.metric(label, analysis.get(key, "N/A"))

                    with st.container(border=True):
                        st.markdown(f"**Summary**\n\n{analysis.get('summary', '')}")
                        if analysis.get("business_insight"):
                            st.markdown(
                                f"**Business insight**\n\n{analysis['business_insight']}")
                        if analysis.get("keywords"):
                            st.markdown("**Keywords:** " + ", ".join(analysis["keywords"]))

                    if analysis.get("executive_summary"):
                        with st.container(border=True):
                            st.markdown(
                                f"**Executive summary**\n\n{analysis['executive_summary']}")
                else:
                    st.warning(
                        "The LLM business analysis is unavailable — check that GROQ_API_KEY "
                        "is configured on the backend, and that the daily token quota "
                        "has not been exhausted."
                    )

            # Similar reviews
            with tab_similar:
                similar = result.get("similar_reviews", [])
                if similar:
                    st.caption(
                        "Nearest matches by embedding similarity — the same index the "
                        "assistant retrieves from."
                    )
                    st.dataframe(pd.DataFrame(similar),
                                 use_container_width=True, hide_index=True)
                else:
                    st.caption("No similar reviews were found in the index.")

# Model comparison
with st.expander("Compare all five sentiment models on this review"):
    st.caption(
        "The disagreements are the interesting part. Across the corpus the ranking "
        "reverses by platform — pretrained RoBERTa leads on tweets and loses badly "
        "on long-form prose."
    )

    if st.button("Run comparison"):
        if len(review_text.strip()) < 3:
            st.warning("Please enter a review above first.")
        else:
            with st.spinner("Scoring with all five models..."):
                comparison = api_post("/api/sentiment/compare", {"text": review_text})

            if comparison:
                rows = [
                    {
                        "Model": name,
                        "Label": r.get("label", "error"),
                        "Confidence": r.get("confidence", 0),
                    }
                    for name, r in comparison.items()
                ]
                df_cmp = pd.DataFrame(rows)
                st.dataframe(df_cmp, use_container_width=True, hide_index=True)

                labels = set(df_cmp["Label"]) - {"error"}
                if len(labels) > 1:
                    st.info(
                        f"The five models disagree on this review ({', '.join(sorted(labels))}). "
                        "Disagreement usually means the review carries mixed vocabulary — "
                        "the case that separates lexicon scoring from contextual models."
                    )
