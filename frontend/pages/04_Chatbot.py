"""
AI Assistant — conversational exploration of customer complaints using
retrieval-augmented generation.

Every answer lists the reviews it drew on — that's what makes it checkable (RQ4).
"""


import streamlit as st

import theme
from utils import api_post, page_header, render_sources, sidebar_status

st.set_page_config(
    page_title="Assistant | FeedbackIQ",
    page_icon="💬",
    layout="wide",
)

theme.inject()
sidebar_status()

page_header(
    "AI Assistant",
    "Ask about complaint patterns across 642,692 reviews. Every answer is "
    "generated **only** from reviews retrieved for your question, and lists them "
    "so you can check it.",
    rq=["RQ2", "RQ4"],
)

SUGGESTIONS = [
    "What are the top complaints on Amazon?",
    "Why are airline customers most dissatisfied?",
    "What do customers say about delivery times?",
    "Summarise the most common service complaints.",
]

if "chat_messages" not in st.session_state:
    st.session_state.chat_messages = []

# Empty state: suggestions that actually submit
if not st.session_state.chat_messages:
    st.caption("Start with one of these, or type your own question below.")
    cols = st.columns(len(SUGGESTIONS))
    for col, suggestion in zip(cols, SUGGESTIONS):
        with col:
            if st.button(suggestion, use_container_width=True, key=f"sug_{suggestion[:16]}"):
                st.session_state["pending_question"] = suggestion
                st.rerun()

    with st.container(border=True):
        st.markdown("**What this assistant will and won't do**")
        st.markdown(
            "- It answers from retrieved reviews, not from what the model already knows\n"
            "- It shows the reviews behind every answer, with platform, rating and ID\n"
            "- It **declines** when nothing relevant clears the similarity threshold, rather "
            "than producing a confident answer from weak evidence\n"
            "- It rejects questions outside customer feedback before spending a retrieval"
        )

# Conversation so far
for message in st.session_state.chat_messages:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant" and not message.get("grounded", True):
            st.warning(message["content"], icon="⚠️")
        else:
            st.write(message["content"])
        if message["role"] == "assistant":
            render_sources(message.get("sources", []))

# New turn
question = st.chat_input("Ask about customer complaints...")

# Suggestion clicks route through the same path as typed questions — one code path only.
if not question and st.session_state.get("pending_question"):
    question = st.session_state.pop("pending_question")

if question:

    st.session_state.chat_messages.append({"role": "user", "content": question})

    with st.chat_message("user"):
        st.write(question)

    # The backend expects prior turns only, in API role/content form.
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in st.session_state.chat_messages[:-1]
    ]

    with st.chat_message("assistant"):

        with st.spinner("Retrieving relevant reviews and generating a grounded answer..."):
            response = api_post(
                "/api/rag/chat",
                {"question": question, "chat_history": history},
                timeout=60,
            )

        if response:
            answer = response.get("answer", "Sorry, I couldn't generate an answer.")
            sources = response.get("sources", [])
            grounded = response.get("grounded", True)

            if grounded:
                st.write(answer)
            else:
                # Refusal is a designed outcome, not an error — show it distinctly.
                st.warning(answer, icon="⚠️")
                st.caption(
                    "The pipeline declined rather than answering. Either the question "
                    "was judged out of scope, or nothing retrieved cleared the "
                    "similarity threshold of 0.35."
                )

            render_sources(sources)

            st.session_state.chat_messages.append({
                "role": "assistant",
                "content": answer,
                "sources": sources,
                "grounded": grounded,
            })

        else:
            error_msg = "Sorry, I couldn't reach the backend to answer that."
            st.write(error_msg)
            st.session_state.chat_messages.append({
                "role": "assistant", "content": error_msg,
                "sources": [], "grounded": True,
            })

# Footer controls
if st.session_state.chat_messages:
    st.divider()
    left, right = st.columns([1, 5])
    with left:
        if st.button("Clear conversation", use_container_width=True):
            st.session_state.chat_messages = []
            st.rerun()
    with right:
        st.caption(
            "Measured on five questions: 0.540 faithfulness with source attribution, "
            "against an ungrounded baseline scoring higher on relevancy (0.753 vs 0.610) "
            "but with nothing to check it against. See the Evaluation page."
        )
