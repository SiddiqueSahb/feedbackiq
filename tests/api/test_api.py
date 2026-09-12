"""
HTTP contract - backend/main.py, backend/api/deps.py, backend/api/routes/*

Protects:
  * authentication: missing key -> 401, wrong key -> 403, valid key -> accepted,
    and every /api route except /api/health requires a key
  * validation: invalid requests are rejected with 422 before any service runs
  * error mapping: expected errors -> 422/503, unexpected errors -> 500 without internal details
  * response shapes the Streamlit frontend depends on

Uses FastAPI's TestClient against the real app, with service functions replaced by fakes.
The client is created without a `with` block, which skips the startup hook that loads
the 407 MB parquet file. Importing backend.main still imports torch, transformers and
LangChain, so this file is the slowest part of the suite.
"""

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from backend.api.routes import rag as rag_routes
from backend.api.routes import search as search_routes
from backend.api.routes import sentiment as sentiment_routes
from backend.main import app
from config import settings

VALID_KEY = {"x-api-key": settings.API_KEY}  # "test-api-key", set in tests/conftest.py


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def fail_if_called(*args, **kwargs):
    raise AssertionError("an invalid request reached the service layer")


# Authentication

def test_health_check_is_open_without_a_key(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_missing_api_key_is_rejected(client):
    response = client.get("/api/sentiment/models")
    assert response.status_code == 401
    assert "x-api-key" in response.json()["detail"]


def test_wrong_api_key_is_rejected(client):
    response = client.get("/api/sentiment/models", headers={"x-api-key": "not-the-key"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid API key."


def test_valid_api_key_is_accepted(client):
    response = client.get("/api/sentiment/models", headers=VALID_KEY)
    assert response.status_code == 200
    assert {model["id"] for model in response.json()} == {
        "distilbert", "roberta", "logistic_regression", "naive_bayes", "vader",
    }


def protected_routes():
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path.startswith("/api") and route.path != "/api/health":
            for method in sorted(route.methods):
                yield method, route.path


@pytest.mark.parametrize("method, path", list(protected_routes()))
def test_every_api_route_requires_a_key(client, method, path):
    assert client.request(method, path).status_code == 401


# Validation

@pytest.mark.parametrize(
    "path, body",
    [
        ("/api/sentiment/predict", {"text": "ab"}),
        ("/api/sentiment/predict", {"text": "The battery died.", "model": "gpt-4"}),
        ("/api/sentiment/analyse", {"text": "   \x00   "}),
        ("/api/sentiment/batch-predict", {"texts": []}),
        ("/api/search", {"query": "battery", "top_k": 0}),
        ("/api/rag/chat", {"question": "hi"}),
    ],
    ids=["text-too-short", "unknown-model", "blank-text", "empty-batch", "top-k-zero", "question-too-short"],
)
def test_invalid_requests_are_rejected_before_reaching_services(client, monkeypatch, path, body):
    for module, name in [
        (sentiment_routes, "predict_only"),
        (sentiment_routes, "analyse_review"),
        (sentiment_routes, "batch_predict"),
        (search_routes, "search_reviews"),
        (rag_routes, "ask_question"),
    ]:
        monkeypatch.setattr(module, name, fail_if_called)

    response = client.post(path, json=body, headers=VALID_KEY)

    assert response.status_code == 422


# Error mapping and response shapes

PREDICTION = {
    "label": "negative",
    "confidence": 0.97,
    "scores": {"positive": 0.01, "neutral": 0.02, "negative": 0.97},
    "model": "distilbert_finetuned",
}


def test_prediction_is_returned_unchanged(client, monkeypatch):
    monkeypatch.setattr(sentiment_routes, "predict_only", lambda text, model: PREDICTION)

    response = client.post("/api/sentiment/predict", json={"text": "The battery died."}, headers=VALID_KEY)

    assert response.status_code == 200
    assert response.json() == PREDICTION


def test_expected_errors_become_422_with_their_message(client, monkeypatch):
    def reject(text, model):
        raise ValueError("Review text cannot be empty.")

    monkeypatch.setattr(sentiment_routes, "predict_only", reject)

    response = client.post("/api/sentiment/predict", json={"text": "The battery died."}, headers=VALID_KEY)

    assert response.status_code == 422
    assert response.json()["detail"] == "Review text cannot be empty."


def test_unexpected_errors_become_500_without_internal_details(client, monkeypatch):
    def crash(text, model):
        raise RuntimeError("out of memory loading /models/distilbert")

    monkeypatch.setattr(sentiment_routes, "predict_only", crash)

    response = client.post("/api/sentiment/predict", json={"text": "The battery died."}, headers=VALID_KEY)

    assert response.status_code == 500
    assert response.json() == {"detail": "Sentiment prediction failed."}
    assert "/models/distilbert" not in response.text


def test_missing_search_index_becomes_503(client, monkeypatch):
    def no_index(**kwargs):
        raise FileNotFoundError("data/embeddings/reviews.faiss")

    monkeypatch.setattr(search_routes, "search_reviews", no_index)

    response = client.post("/api/search", json={"query": "battery"}, headers=VALID_KEY)

    assert response.status_code == 503


def test_analyse_response_shape(client, monkeypatch):
    pipeline_result = {
        "review": "The battery died.",
        "sentiment": PREDICTION,
        "categories": [{"category": "Product Performance Failures", "score": 0.81, "description": "d"}],
        "category": "Product Performance Failures",
        "similar_reviews": [{
            "review_id": 12345, "text": "Battery dead in a week.", "platform": "amazon",
            "rating": 1.0, "sentiment_label": "negative", "similarity_score": 0.7,
        }],
        "summary": "Battery failed.",
        "keywords": ["battery", "failure", "charging", "defect", "quality"],
        "business_insight": "Review supplier quality.",
        "severity": "High",
        "priority": "High",
        "department": "Quality Assurance",
        "executive_summary": "Early battery failures.",
    }
    monkeypatch.setattr(sentiment_routes, "analyse_review", lambda text, model: pipeline_result)

    response = client.post("/api/sentiment/analyse", json={"text": "The battery died."}, headers=VALID_KEY)

    assert response.status_code == 200
    body = response.json()
    assert body["categories"][0]["confidence"] == 0.81        # categoriser "score" becomes "confidence"
    assert body["analysis"]["severity"] == "High"             # flat LLM fields are nested under "analysis"
    assert body["similar_reviews"][0]["review_id"] == "12345"  # numeric IDs from pandas become strings


def test_rag_refusal_is_returned_as_an_ungrounded_answer(client, monkeypatch):
    refusal = {
        "answer": "I couldn't find enough relevant customer reviews to answer this question.",
        "sources": [],
        "use_case": "complaint_exploration",
        "retrieval_count": 0,
        "grounded": False,
    }
    monkeypatch.setattr(rag_routes, "ask_question", lambda question, history: refusal)

    response = client.post("/api/rag/chat", json={"question": "Anything about crypto payments?"}, headers=VALID_KEY)

    assert response.status_code == 200
    assert response.json() == {"answer": refusal["answer"], "sources": [], "retrieval_count": 0, "grounded": False}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known issue: /api/sentiment/analyse accepts `platform` but never passes it to "
        "the pipeline. Decide in a later milestone whether to use it or remove it."
    ),
)
def test_analyse_passes_platform_to_the_pipeline(client, monkeypatch):
    received = []

    def record(*args, **kwargs):
        received.append((args, kwargs))
        raise ValueError("stop after recording the call")

    monkeypatch.setattr(sentiment_routes, "analyse_review", record)

    client.post("/api/sentiment/analyse", json={"text": "The battery died.", "platform": "amazon"}, headers=VALID_KEY)

    assert "amazon" in repr(received)
