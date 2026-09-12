"""
Shared helpers for calling the FastAPI backend from Streamlit pages.

Base URL + GET/POST helpers with error handling, so pages don't repeat the
same requests.get(...)/try/except boilerplate.
"""

import re

import requests
import streamlit as st

from app_settings import settings

API = settings.API_URL

# Every /api/* route except /api/health requires this header (see
# feedbackiq/api/deps.py). Configured, never hardcoded.
API_KEY = settings.API_KEY
HEADERS = {"x-api-key": API_KEY}

# First request after a cold start can take ~a minute (FAISS index + models
# load lazily, not at startup). 90s covers that; the old 15s timeout failed
# against a freshly started but healthy backend.
GET_TIMEOUT = settings.API_GET_TIMEOUT

# No model loading behind health, so it should be fast — kept short since
# the sidebar calls it on every page render.
HEALTH_TIMEOUT = settings.API_HEALTH_TIMEOUT

# Used everywhere sentiment is shown, so colors stay consistent across pages.
SENTIMENT_COLORS = {
    "positive": "#2ECC71",
    "negative": "#E74C3C",
    "neutral": "#F39C12",
}


@st.cache_data(show_spinner=False, ttl=60)
def api_get(
    endpoint: str,
    params: dict | None = None,
    timeout: int | None = None,
) -> dict | list:
    """GET from the backend. Cached a minute — dashboard data doesn't change
    second to second. Returns {} on failure."""

    try:
        response = requests.get(
            f"{API}{endpoint}",
            params=params,
            headers=HEADERS,
            timeout=timeout or GET_TIMEOUT,
        )

        if response.ok:
            return response.json()

        if response.status_code in (401, 403):
            st.error(
                "The backend rejected this request's API key. Set API_KEY to the "
                "same value the backend is using (`.env` locally, or the "
                "environment/secret on the deployed frontend)."
            )
            return {}

        st.error(f"API error ({response.status_code}): {response.text[:200]}")

    except requests.exceptions.Timeout:
        st.error(
            "The backend didn't respond in time. If it was just started it may "
            "still be loading the search index — wait a moment and retry."
        )

    except requests.exceptions.ConnectionError:
        st.error(
            "Can't reach the FeedbackIQ API. Make sure the backend is running "
            f"at {API}."
        )

    except Exception as exc:
        st.error(f"Unexpected error calling the API: {exc}")

    return {}


def api_post(endpoint: str, payload: dict, timeout: int = 60) -> dict | list | None:
    """POST to the backend. Not cached — these are user-triggered actions, not
    passive reads. Returns None on failure, distinct from an empty result."""

    try:
        response = requests.post(
            f"{API}{endpoint}",
            json=payload,
            headers=HEADERS,
            timeout=timeout,
        )

        if response.ok:
            return response.json()

        try:
            detail = response.json().get("detail", response.text)
        except Exception:
            detail = response.text

        if response.status_code in (401, 403):
            st.error(
                "The backend rejected this request's API key. Set API_KEY to the "
                "same value the backend is using."
            )
            return None

        st.error(f"Request failed ({response.status_code}): {detail}")

    except requests.exceptions.ConnectionError:
        st.error(
            "Can't reach the FeedbackIQ API. Make sure the backend is running "
            f"at {API}."
        )

    except requests.exceptions.Timeout:
        st.error("The request took too long and timed out. Please try again.")

    except Exception as exc:
        st.error(f"Unexpected error calling the API: {exc}")

    return None


# Which research question each page demonstrates — centralised so it's stated once.
RESEARCH_QUESTIONS = {
    "RQ1": "How does sentiment classification performance differ between lexicon, "
           "classical, pretrained transformer and fine-tuned transformer approaches?",
    "RQ2": "How effectively does retrieval-augmented generation improve complaint "
           "exploration compared with a standalone large language model?",
    "RQ3": "Can BERTopic produce a coherent cross-domain complaint taxonomy from "
           "heterogeneous customer reviews?",
    "RQ4": "To what extent can retrieval-augmented answers be traced to, and "
           "verified against, the source reviews used to produce them?",
}


def page_header(title: str, subtitle: str, rq: str | list[str] | None = None) -> None:
    """One header treatment for every page: title, one-line explanation, and
    the research question it speaks to. Centralised so pages don't drift into
    different header styles."""
    # Styled header, not st.title, to match the landing page (see theme.py).
    # Subtitle is markdown from callers — convert emphasis by hand since raw
    # HTML blocks skip markdown processing.
    subtitle = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", subtitle)
    subtitle = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", subtitle)
    subtitle = re.sub(r"`(.+?)`", r"<code>\1</code>", subtitle)

    st.markdown(
        f'''<div class="fiq-hero">
              <h1>{title}</h1>
              <p>{subtitle}</p>
            </div>''',
        unsafe_allow_html=True,
    )

    keys = [rq] if isinstance(rq, str) else (rq or [])
    if keys:
        labels = " · ".join(keys)
        with st.expander(f"Research question: {labels}", expanded=False):
            for key in keys:
                st.markdown(f"**{key}** — {RESEARCH_QUESTIONS.get(key, '')}")

    st.divider()


def render_sources(sources: list[dict], label: str | None = None) -> None:
    """Show the reviews an answer was built from — the visible half of RQ4
    (traceability). Shared here since it's rendered in two places (history +
    live turn).
    """
    if not sources:
        return

    n = len(sources)
    with st.expander(label or f"{n} source review{'s' if n != 1 else ''} — the evidence for this answer"):
        for i, source in enumerate(sources, start=1):
            score = source.get("similarity_score")
            bits = [
                f"**{source.get('platform', 'unknown')}**",
                f"★ {source.get('rating', 'N/A')}",
                str(source.get("sentiment_label", "unknown")),
            ]
            if score is not None:
                bits.append(f"similarity {score:.2f}")
            if source.get("review_id"):
                bits.append(f"`{source['review_id']}`")

            st.markdown(f"{i}. " + " · ".join(bits))
            st.markdown(f"> {source.get('text', '')}")
            if i < n:
                st.divider()


def sidebar_status() -> None:
    """Sidebar "is the backend working" widget. Checks /api/health, which
    also verifies data/index/LLM key are present — not just that the process
    is up."""

    with st.sidebar:

        st.markdown("### FeedbackIQ")

        health = api_get("/api/health", timeout=HEALTH_TIMEOUT)

        if not health:
            st.error("Backend unreachable")
            st.caption(f"Expected at {API}")
            st.divider()
            return

        checks = {
            "Review data": health.get("data_loaded", False),
            "Search index": health.get("index_ready", False),
            "LLM (Groq)": health.get("llm_ready", False),
        }

        # Genuine status colour, not decorative — keeps st.success/st.error
        # meaningful when they do appear.
        if all(checks.values()):
            st.success("All systems ready", icon="✅")
        else:
            st.warning("Some components unavailable", icon="⚠️")

        for label, ok in checks.items():
            st.caption(f"{'✅' if ok else '⚠️'} {label}")

        st.divider()
