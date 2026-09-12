"""
Dashboard — corpus-level view of sentiment, ratings, categories and trends.

KPI row + charts only — an earlier version repeated the same figures in prose three times.
"""


import pandas as pd
import plotly.express as px
import streamlit as st

import theme
from utils import api_get, page_header, sidebar_status, SENTIMENT_COLORS

st.set_page_config(
    page_title="Dashboard | FeedbackIQ",
    page_icon="📊",
    layout="wide",
)

theme.inject()
sidebar_status()

page_header(
    "Analytics Dashboard",
    "Sentiment, ratings, complaint categories and trends across the full corpus "
    "of 642,692 reviews.",
    rq=["RQ1", "RQ3"],
)

# Load
with st.spinner("Loading dashboard..."):
    stats = api_get("/api/analytics/summary")
    by_platform = api_get("/api/analytics/sentiment-by-platform")
    rating_distribution = api_get("/api/analytics/rating-distribution")
    keywords = api_get("/api/analytics/keywords")
    trends = api_get("/api/analytics/trends")
    platforms = api_get("/api/analytics/platforms")

if not stats:
    st.error(
        "Unable to load analytics. Check that the FastAPI backend is running and "
        "that data/processed/reviews_unified.parquet is present."
    )
    st.stop()

# Filters
st.sidebar.header("Filters")

if isinstance(platforms, list) and platforms:
    selected_platforms = st.sidebar.multiselect("Platform", platforms, default=platforms)
else:
    selected_platforms = []

selected_sentiment = st.sidebar.selectbox(
    "Sentiment", ["All", "positive", "negative", "neutral"]
)

st.sidebar.caption(
    "Filters apply to the sentiment and trend charts. Ratings and keywords are "
    "corpus-wide."
)

# KPIs
kpi = st.columns(5)
cards = [
    ("Total reviews", f"{stats.get('total_reviews', 0):,}"),
    ("Average rating", f"{stats.get('avg_rating', 0):.1f} / 5"),
    ("Positive", f"{stats.get('positive_pct', 0):.1f}%"),
    ("Negative", f"{stats.get('negative_pct', 0):.1f}%"),
    ("Neutral", f"{stats.get('neutral_pct', 0):.1f}%"),
]
for col, (label, value) in zip(kpi, cards):
    with col, st.container(border=True):
        st.metric(label, value)

st.caption(
    "Labels are derived from star ratings, so a four-star review containing a "
    "complaint counts as positive. This bounds every figure on this page."
)

st.divider()

# Charts
tab_sentiment, tab_ratings, tab_keywords, tab_trends = st.tabs(
    ["Sentiment", "Ratings", "Keywords", "Trends"]
)

CHART_LAYOUT = dict(height=420, plot_bgcolor="white",
                    margin=dict(l=10, r=10, t=40, b=10))

# Sentiment
with tab_sentiment:
    if by_platform:
        df_platform = pd.DataFrame(by_platform)

        if selected_platforms:
            df_platform = df_platform[df_platform["platform"].isin(selected_platforms)]
        if selected_sentiment != "All":
            df_platform = df_platform[df_platform["sentiment_label"] == selected_sentiment]

        if df_platform.empty:
            st.info("No data matches the current filters.")
        else:
            left, right = st.columns([1.4, 1])

            with left:
                fig = px.bar(
                    df_platform, x="platform", y="count", color="sentiment_label",
                    barmode="group", color_discrete_map=SENTIMENT_COLORS,
                    title="Sentiment by platform",
                    labels={"platform": "Platform", "count": "Reviews",
                            "sentiment_label": "Sentiment"},
                )
                fig.update_layout(legend_title="Sentiment", **CHART_LAYOUT)
                st.plotly_chart(fig, use_container_width=True)

            with right:
                summary = df_platform.groupby("sentiment_label")["count"].sum().reset_index()
                fig = px.pie(
                    summary, values="count", names="sentiment_label", hole=0.55,
                    color="sentiment_label", color_discrete_map=SENTIMENT_COLORS,
                    title="Overall sentiment",
                )
                fig.update_traces(textposition="inside", textinfo="percent+label")
                fig.update_layout(showlegend=False, **CHART_LAYOUT)
                st.plotly_chart(fig, use_container_width=True)

            st.caption(
                "The platform split is the study's central finding: model ranking "
                "reverses depending on which platform the text comes from."
            )
    else:
        st.info("Sentiment breakdown unavailable.")

# Ratings
with tab_ratings:
    if rating_distribution:
        df_rating = pd.DataFrame(rating_distribution)
        fig = px.bar(
            df_rating, x="rating", y="count", text="count",
            color="rating", color_continuous_scale="RdYlGn",
            labels={"rating": "Star rating", "count": "Reviews"},
            title="Rating distribution",
        )
        fig.update_traces(textposition="outside")
        fig.update_layout(showlegend=False, coloraxis_showscale=False, **CHART_LAYOUT)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Ratings are the source of the sentiment labels, which is why the shape "
            "here constrains what any classifier can learn."
        )
    else:
        st.info("Rating distribution unavailable.")

# Keywords
with tab_keywords:
    if keywords:
        df_keywords = pd.DataFrame(keywords).sort_values("count", ascending=True).tail(20)

        basis = df_keywords["basis"].iloc[0] if "basis" in df_keywords else None
        reviewed = int(df_keywords["n_reviews"].iloc[0]) if "n_reviews" in df_keywords else 0

        fig = px.bar(
            df_keywords, x="count", y="word", orientation="h", text="count",
            color="count", color_continuous_scale="Blues",
            labels={"count": "Frequency", "word": "Phrase"},
            title="Most frequent phrases",
        )
        fig.update_layout(
            height=560, plot_bgcolor="white", showlegend=False,
            coloraxis_showscale=False, yaxis=dict(categoryorder="total ascending"),
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)

        if basis == "sample":
            st.caption(
                f"Counts from a random sample of {reviewed:,} reviews, not the full corpus. "
                "Run `python scripts/precompute_keywords.py` for exact figures."
            )
        elif basis == "corpus" and reviewed:
            st.caption(f"Counts across all {reviewed:,} reviews.")
    else:
        st.info("Keyword analysis unavailable.")

# Trends
with tab_trends:
    if trends:
        df_trend = pd.DataFrame(trends)
        if selected_sentiment != "All":
            df_trend = df_trend[df_trend["sentiment_label"] == selected_sentiment]

        if df_trend.empty:
            st.info("Trend data is not available for this selection.")
        else:
            fig = px.line(
                df_trend, x="month", y="count", color="sentiment_label", markers=True,
                color_discrete_map=SENTIMENT_COLORS,
                labels={"month": "Month", "count": "Reviews",
                        "sentiment_label": "Sentiment"},
                title="Sentiment over time",
            )
            fig.update_layout(hovermode="x unified", legend_title="Sentiment", **CHART_LAYOUT)
            st.plotly_chart(fig, use_container_width=True)
            st.caption(
                "Review volume varies substantially by month and platform, so read "
                "the shape rather than the absolute counts."
            )
    else:
        st.info("Trend data unavailable for this dataset.")

st.divider()

# Where to go next
st.subheader("Go deeper")
nav = st.columns(3)
with nav[0]:
    st.page_link("pages/03_Search.py", label="Find specific reviews",
                 icon="🔎", use_container_width=True)
with nav[1]:
    st.page_link("pages/04_Chatbot.py", label="Ask about the patterns",
                 icon="💬", use_container_width=True)
with nav[2]:
    st.page_link("pages/06_Evaluation.py", label="See how it was evaluated",
                 icon="📈", use_container_width=True)
