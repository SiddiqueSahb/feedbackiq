"""
Evaluation - Model & Pipeline Performance

Reads and displays results already produced by scripts/evaluate_*.py — sentiment,
retrieval (TF-IDF vs semantic) and RAG/RAGAS. Runs nothing itself.
"""


import pandas as pd
import plotly.express as px
import streamlit as st

import theme
from utils import api_get, page_header, sidebar_status

st.set_page_config(
    page_title="Evaluation | FeedbackIQ",
    page_icon="🧪",
    layout="wide",
)

theme.inject()
sidebar_status()

page_header(
    "Evaluation",
    "The evidence behind the claims on the home page. Every figure here was "
    "written to `data/results/` by the script that computed it — nothing on this "
    "page was typed by hand.",
    rq=["RQ1", "RQ2", "RQ3", "RQ4"],
)

tab_sentiment, tab_retrieval, tab_rag = st.tabs(
    ["Sentiment — RQ1", "Retrieval — RQ4", "Generation — RQ2"]
)


# Sentiment Classification
with tab_sentiment:

    with st.spinner("Loading sentiment evaluation results..."):
        sentiment_eval = api_get("/api/evaluation/sentiment")

    comparison = sentiment_eval.get("model_comparison", []) if sentiment_eval else []

    if not comparison:
        st.info(
            "No sentiment evaluation results found. Run "
            "`python scripts/evaluate_models.py` to generate them."
        )
    else:
        df_comparison = pd.DataFrame(comparison).sort_values(
            "accuracy", ascending=False
        )

        best = df_comparison.iloc[0]
        st.write(
            f"**{best['model']}** performs best overall, with "
            f"**{best['accuracy']:.1%} accuracy** and a macro F1 of "
            f"**{best['f1_macro']:.3f}** across all five classifiers compared."
        )

        col1, col2 = st.columns([1.2, 1])

        with col1:
            st.subheader("Model Comparison")

            fig = px.bar(
                df_comparison.melt(
                    id_vars="model",
                    value_vars=["accuracy", "f1_macro"],
                    var_name="metric",
                    value_name="score",
                ),
                x="model",
                y="score",
                color="metric",
                barmode="group",
                labels={"model": "Model", "score": "Score", "metric": "Metric"},
            )
            fig.update_layout(
                height=420,
                plot_bgcolor="white",
                margin=dict(l=10, r=10, t=30, b=10),
                yaxis=dict(range=[0, 1]),
            )
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            st.subheader("Full Metrics")
            st.dataframe(
                df_comparison.set_index("model").style.format("{:.4f}"),
                use_container_width=True,
            )

        st.divider()

        # Confusion matrix
        st.subheader("Confusion Matrix")

        matrices = sentiment_eval.get("confusion_matrices", {})

        if matrices:
            selected_model = st.selectbox(
                "Model", list(matrices.keys()), index=0
            )
            cm = matrices[selected_model]

            fig = px.imshow(
                cm["confusion_matrix"],
                x=cm["labels"],
                y=cm["labels"],
                text_auto=True,
                color_continuous_scale="Blues",
                labels=dict(x="Predicted", y="Actual", color="Reviews"),
            )
            fig.update_layout(
                height=420,
                margin=dict(l=10, r=10, t=30, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)

        st.divider()

        # Significance tests
        significance = sentiment_eval.get("significance_tests", [])

        if significance:
            st.subheader("Statistical Significance (McNemar's Test)")
            st.caption(
                "Pairwise comparison of each model's predictions on the same "
                "reviews — a p-value below 0.05 means the accuracy difference "
                "is unlikely to be due to chance."
            )
            st.dataframe(
                pd.DataFrame(significance),
                use_container_width=True,
                hide_index=True,
            )


# Retrieval Evaluation
with tab_retrieval:

    with st.spinner("Loading retrieval evaluation results..."):
        retrieval_eval = api_get("/api/evaluation/retrieval")

    tfidf = retrieval_eval.get("tfidf", {}) if retrieval_eval else {}
    semantic = retrieval_eval.get("semantic_search", {}) if retrieval_eval else {}
    dataset_info = retrieval_eval.get("dataset_info", {}) if retrieval_eval else {}

    if not tfidf and not semantic:
        st.info(
            "No retrieval evaluation results found. Run "
            "`python scripts/evaluate_tf_idf.py` and "
            "`python scripts/evaluate_semantic_search.py` to generate them."
        )
    else:
        if dataset_info:
            st.caption(
                f"Indexed {int(float(dataset_info.get('Total Reviews Indexed', 0))):,} reviews "
                f"with {dataset_info.get('Embedding Model', 'N/A')} "
                f"({dataset_info.get('Embedding Dimension', 'N/A')}-dim), stored in "
                f"{dataset_info.get('Vector Database', 'N/A')}."
            )

        col1, col2 = st.columns(2)

        with col1, st.container(border=True):
            st.subheader("TF-IDF")
            st.metric("Avg Precision@3", f"{float(tfidf.get('Average Precision@3', 0)):.2f}")
            st.metric("Avg Similarity Score", f"{float(tfidf.get('Average Similarity Score', 0)):.3f}")
            st.metric("Avg Retrieval Time", f"{float(tfidf.get('Average Retrieval Time (ms)', 0)):.1f} ms")

        with col2, st.container(border=True):
            st.subheader("Semantic Search (FAISS)")
            st.metric("Avg Precision@3", f"{float(semantic.get('Average Precision@3', 0)):.2f}")
            st.metric("Avg Similarity Score", f"{float(semantic.get('Average Similarity Score', 0)):.3f}")
            st.metric("Avg Retrieval Time", f"{float(semantic.get('Average Retrieval Time (ms)', 0)):.1f} ms")

        st.divider()

        st.subheader("Retrieval Time Comparison")
        speed_df = pd.DataFrame([
            {"method": "TF-IDF", "latency_ms": float(tfidf.get("Average Retrieval Time (ms)", 0))},
            {"method": "Semantic Search", "latency_ms": float(semantic.get("Average Retrieval Time (ms)", 0))},
        ])
        fig = px.bar(
            speed_df, x="method", y="latency_ms", text="latency_ms",
            labels={"method": "Method", "latency_ms": "Avg Retrieval Time (ms)"},
        )
        fig.update_traces(texttemplate="%{text:.1f} ms", textposition="outside")
        fig.update_layout(height=380, plot_bgcolor="white", showlegend=False,
                           margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig, use_container_width=True)

        st.caption(
            "Semantic search retrieves conceptually similar reviews even "
            "without exact keyword overlap; TF-IDF matches on exact terms. "
            "Both are compared here on the same query set."
        )


# RAG / RAGAS Evaluation
with tab_rag:

    with st.spinner("Loading RAG evaluation results..."):
        rag_eval = api_get("/api/evaluation/rag")

    if not rag_eval or not rag_eval.get("available"):
        st.info(
            "RAG/RAGAS evaluation hasn't been run yet. From the project root, run:\n\n"
            "```\npython evaluate/evaluate_llm_vs_rag.py\n```\n\n"
            "This makes live Groq API calls and can take a minute or two. "
            "Once it finishes, refresh this page to see the results."
        )
    else:
        overall = rag_eval.get("overall", {})
        comparison = rag_eval.get("comparison", [])

        n_questions = int(float(overall.get("Questions Evaluated", 0)))
        st.write(
            f"RAG pipeline compared against a standalone LLM (no retrieval) "
            f"on the same **{n_questions} questions**, scored with RAGAS."
        )
        st.caption(
            "Small sample — per-question variance dominates these means. "
            "The judge model is the same model used for generation, which "
            "is a known self-grading bias; treat these as directional, not precise."
        )

        col1, col2, col3, col4 = st.columns(4)

        with col1, st.container(border=True):
            st.metric("Faithfulness (RAG)", f"{float(overall.get('Average Faithfulness', 0)):.3f}")
        with col2, st.container(border=True):
            st.metric(
                "Answer Relevancy",
                f"{float(overall.get('Average Answer Relevancy (RAG)', 0)):.3f}",
                delta=f"{float(overall.get('Average Answer Relevancy (RAG)', 0)) - float(overall.get('Average Answer Relevancy (LLM)', 0)):+.3f} vs LLM-only",
                delta_color="off",
            )
        with col3, st.container(border=True):
            st.metric("Context Precision", f"{float(overall.get('Average Context Precision', 0)):.3f}")
        with col4, st.container(border=True):
            st.metric("Context Recall", f"{float(overall.get('Average Context Recall', 0)):.3f}")

        st.divider()

        col1, col2 = st.columns([1, 1])

        with col1:
            st.subheader("RAG vs Standalone LLM")
            if comparison:
                st.dataframe(
                    pd.DataFrame(comparison), use_container_width=True, hide_index=True
                )

        with col2:
            st.subheader("Latency")
            latency_df = pd.DataFrame([
                {"condition": "LLM only", "latency_ms": float(overall.get("Average LLM Latency (ms)", 0))},
                {"condition": "RAG", "latency_ms": float(overall.get("Average RAG Latency (ms)", 0))},
            ])
            fig = px.bar(
                latency_df, x="condition", y="latency_ms", text="latency_ms",
                labels={"condition": "", "latency_ms": "Avg latency (ms)"},
            )
            fig.update_traces(texttemplate="%{text:.0f} ms", textposition="outside")
            fig.update_layout(height=300, plot_bgcolor="white", showlegend=False,
                               margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)

        st.divider()

        st.subheader("Per-Question Breakdown (RAG)")
        rag_per_question = rag_eval.get("rag_per_question", [])
        if rag_per_question:
            df_q = pd.DataFrame(rag_per_question)
            df_q["answer"] = df_q["answer"].str.slice(0, 150) + "…"
            st.dataframe(
                df_q[["question", "retrieved_count", "faithfulness", "answer_relevancy",
                      "context_precision", "context_recall", "answer"]],
                use_container_width=True,
                hide_index=True,
            )
            st.caption(
                "Five questions means one bad or lucky answer swings the average "
                "noticeably — read this table, not just the KPI cards above."
            )

st.divider()
st.caption(
    "FeedbackIQ • Evaluation results generated by the scripts/evaluate_*.py "
    "and evaluate/evaluate_*.py suite."
)
