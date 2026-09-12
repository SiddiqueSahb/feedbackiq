"""
FeedbackIQ — landing page.

Run:
    streamlit run frontend/app.py

Organised around the four research questions, not a feature list — each links
to where that finding can be seen running.
"""

import os
import sys

import streamlit as st

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import theme
from utils import api_get, sidebar_status

st.set_page_config(
    page_title="FeedbackIQ",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

theme.inject()
sidebar_status()

# Hero
theme.hero(
    "FeedbackIQ",
    "Which method should you actually use to analyse customer feedback — a lexicon, "
    "classical machine learning, a fine-tuned transformer, or retrieval-augmented "
    "generation? This system compares all four, stage by stage, across three very "
    "different kinds of feedback.",
    "MSc Data Science dissertation · University of Surrey · 642,692 reviews from "
    "Amazon products, Yelp businesses and Twitter Airline complaints",
)

# Corpus at a glance — live figures so the page never contradicts the running system
stats = api_get("/api/analytics/summary") or {}

total = stats.get("total_reviews")
avg_rating = stats.get("avg_rating")
negative = stats.get("negative_pct")

theme.kpi_grid([
    ("Reviews indexed", f"{total:,}" if total else "642,692"),
    ("Platforms", str(stats.get("platforms") or 3)),
    ("Average rating", f"{avg_rating:.1f} / 5" if avg_rating else "—"),
    ("Negative", f"{negative:.1f}%" if negative is not None else "—"),
    ("Complaint categories", "24"),
])

if not stats:
    st.caption(
        "Backend unreachable — showing corpus constants. Start the API to load live figures."
    )

# Research questions
theme.section(
    "Research Questions",
    "What the study asked, what the numbers showed, and where you can watch the "
    "system behave that way.",
)

RESEARCH_QUESTIONS = [
    {
        "id": "RQ1",
        "question": (
            "How does sentiment classification performance differ between lexicon-based, "
            "classical machine learning, pretrained transformer and fine-tuned transformer "
            "approaches on multi-domain customer feedback?"
        ),
        "finding": (
            "Fine-tuned DistilBERT led at <b>0.8375</b> macro F1 on clean held-out data — but "
            "the ranking <b>reverses by platform</b>. Pretrained RoBERTa scores 0.7853 on "
            "tweets and 0.5544 on Yelp prose, beating both classical models on the domain "
            "closest to its pretraining and losing badly elsewhere. Fine-tuning bought "
            "consistency — a 0.020 spread across platforms — rather than peak accuracy."
        ),
        "pages": [("Review Analysis", "pages/02_Analyse.py", "🧠"),
                  ("Evaluation", "pages/06_Evaluation.py", "📈")],
    },
    {
        "id": "RQ2",
        "question": (
            "How effectively does retrieval-augmented generation improve complaint "
            "exploration compared with a standalone large language model?"
        ),
        "finding": (
            "RAG reached <b>0.540</b> faithfulness with source attribution, while the "
            "ungrounded baseline scored <i>higher</i> on answer relevancy — 0.753 against "
            "0.610 — in under half the time. What retrieval buys is verifiability, not "
            "responsiveness."
        ),
        "pages": [("AI Assistant", "pages/04_Chatbot.py", "💬"),
                  ("Evaluation", "pages/06_Evaluation.py", "📈")],
    },
    {
        "id": "RQ3",
        "question": (
            "Can BERTopic produce a coherent cross-domain complaint taxonomy from "
            "heterogeneous customer reviews?"
        ),
        "finding": (
            "121 raw topics discovered per platform were consolidated into <b>24 categories</b> "
            "at 0.6334 coherence — but <b>27.6%</b> of held-out reviews stayed unclassified and "
            "one category absorbed 28.2% of the sample. Usable, but not without a consolidation "
            "stage and a data-driven category count."
        ),
        "pages": [("Dashboard", "pages/01_Dashboard.py", "📊"),
                  ("Review Analysis", "pages/02_Analyse.py", "🧠")],
    },
    {
        "id": "RQ4",
        "question": (
            "To what extent can retrieval-augmented answers be traced to, and verified "
            "against, the source reviews used to produce them?"
        ),
        "finding": (
            "Every answer returns the identifiers of the reviews that produced it, so "
            "traceability is an architectural property rather than a claim. The figures bound "
            "it honestly: at 0.540 faithfulness roughly half of statements were not fully "
            "supported, and at 0.416 context precision the retrieved evidence was often not "
            "the best available."
        ),
        "pages": [("AI Assistant", "pages/04_Chatbot.py", "💬"),
                  ("Semantic Search", "pages/03_Search.py", "🔎")],
    },
]

for rq in RESEARCH_QUESTIONS:
    st.markdown(
        f"""<div class="fiq-rq">
              <span class="tag">{rq['id']}</span>
              <div class="q">{rq['question']}</div>
              <p class="a">{rq['finding']}</p>
            </div>""",
        unsafe_allow_html=True,
    )
    links = st.columns([1, 1, 3])
    for col, (label, path, icon) in zip(links, rq["pages"]):
        with col:
            st.page_link(path, label=label, icon=icon, use_container_width=True)

# How it works
theme.section(
    "How It Works",
    "Three stages, because monitoring, diagnosis and investigation are different jobs "
    "with different failure modes — and different evaluation regimes.",
)

STAGES = [
    ("Stage 1", "Sentiment",
     "Fine-tuned DistilBERT classifies each review, with VADER, Naive Bayes, logistic "
     "regression and pretrained RoBERTa as comparison baselines.",
     "Monitoring — how bad is it?"),
    ("Stage 2", "Complaint discovery",
     "BERTopic (UMAP + HDBSCAN) discovers topics per platform, consolidated into 24 "
     "categories and assigned with DeBERTa-v3 zero-shot entailment.",
     "Diagnosis — what is wrong?"),
    ("Stage 3", "Grounded answers",
     "MiniLM embeddings in FAISS, a two-stage scope guard and a similarity threshold, "
     "then a language model answering only from retrieved reviews.",
     "Investigation — show me the evidence."),
]

cards = "".join(
    f"""<div class="fiq-card fiq-stage">
          <div class="n">{n}</div>
          <div class="t">{t}</div>
          <div class="d">{d}</div>
          <p class="p">{p}</p>
        </div>"""
    for n, t, d, p in STAGES
)
st.markdown(f'<div class="fiq-grid fiq-cards">{cards}</div>', unsafe_allow_html=True)

st.caption(
    "A macro F1, a coherence score and a faithfulness score are not comparable, so the "
    "study measures each stage on its own terms rather than collapsing them into one number."
)

# Explore
theme.section("Explore")

PAGES = [
    ("Dashboard", "pages/01_Dashboard.py", "📊"),
    ("Review Analysis", "pages/02_Analyse.py", "🧠"),
    ("Semantic Search", "pages/03_Search.py", "🔎"),
    ("AI Assistant", "pages/04_Chatbot.py", "💬"),
    ("Batch Upload", "pages/05_Upload.py", "📤"),
    ("Evaluation", "pages/06_Evaluation.py", "📈"),
]
row1, row2 = st.columns(3), st.columns(3)
for col, (label, path, icon) in zip(list(row1) + list(row2), PAGES):
    with col:
        st.page_link(path, label=label, icon=icon, use_container_width=True)

# Detail, folded away
with st.expander("Technology and method detail"):
    left, right = st.columns(2)

    with left:
        st.markdown("**Machine learning and NLP**")
        st.markdown(
            "- Fine-tuned DistilBERT · pretrained RoBERTa\n"
            "- Naive Bayes · logistic regression · TF-IDF\n"
            "- VADER lexicon baseline\n"
            "- BERTopic + UMAP + HDBSCAN\n"
            "- DeBERTa-v3 zero-shot classification"
        )
        st.markdown("**Retrieval and generation**")
        st.markdown(
            "- all-MiniLM-L6-v2 sentence embeddings\n"
            "- FAISS vector index over 642,692 reviews\n"
            "- LangChain · Llama 3.1 8B via Groq\n"
            "- RAGAS: faithfulness, answer relevancy, context precision/recall"
        )

    with right:
        st.markdown("**Engineering**")
        st.markdown(
            "- FastAPI backend · Streamlit frontend\n"
            "- Docker Compose, two images by resource profile\n"
            "- MLflow experiment tracking\n"
            "- API-key auth on every route except health"
        )
        st.markdown("**Evaluation discipline**")
        st.markdown(
            "- One shared held-out set of 128,539 reviews\n"
            "- Per-class and per-platform breakdowns, not just aggregates\n"
            "- McNemar's exact test on paired predictions\n"
            "- One executed ablation (metadata tagging)\n"
            "- Every reported figure written to `data/results/` by the script that produced it"
        )

with st.expander("Known limitations"):
    st.markdown(
        "Stated here for the same reason they are stated in the dissertation: they bound "
        "what the numbers above mean.\n\n"
        "- **The fine-tuned model's shared-test-set figure is the least controlled number** "
        "in the study. It was developed against a separately generated corpus version, so part "
        "of the shared test set overlaps data it saw. Its clean figure — 0.8375 on its own "
        "held-out partition — is the one to trust. The other four models are unaffected.\n"
        "- **Labels derive from star ratings**, so a four-star review containing a complaint is "
        "labelled positive. This bounds every model's ceiling.\n"
        "- **Generation was evaluated on five questions**, graded by the same model family that "
        "produced the answers. Directional, not precise.\n"
        "- **The 0.35 similarity threshold is unvalidated** — a starting point, not a calibrated "
        "constant.\n"
        "- **Three of four planned ablations were not run**, and component metrics point at "
        "retrieval as the weak link, so those are the most consequential missing experiments."
    )

st.divider()
st.caption(
    "FeedbackIQ v1.0 · Built with Python, FastAPI, Streamlit, Hugging Face, DistilBERT, "
    "BERTopic, Sentence Transformers, FAISS, LangChain and Groq."
)
