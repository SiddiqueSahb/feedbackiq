"""
Two models trained with TF-IDF features:
  1. Logistic Regression (LR)  — strong classical baseline
  2. Naive Bayes (NB)          — simple probabilistic baseline

These represent pre-transformer NLP approaches (pre-2018).
They are trained on your dataset via: python scripts/train_classical_models.py
Saved to: models/classical/
"""

from __future__ import annotations

import os
import pickle
import re
from typing import Any

# Make project root importable
from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
log = get_logger("nlp.classical")

# Resolved against the project root by core/config.py, so a different working
# directory cannot turn predictions into a silent "unknown".
MODEL_DIR = str(settings.classical_model_dir)
LR_PATH   = os.path.join(MODEL_DIR, "logistic_regression.pkl")
NB_PATH   = os.path.join(MODEL_DIR, "naive_bayes.pkl")
VEC_PATH  = os.path.join(MODEL_DIR, "tfidf_vectorizer.pkl")

LABEL_MAP: dict[int,str] = {0:"negative",1:"neutral",2:"positive"}

def _clean(text: str) -> str:
    """Text cleanup matching the training-time preprocessing pipeline."""

    text = str(text)

    text = re.sub(r"http\S+|www\S+", " ", text)

    text = re.sub(r"@\w+", " ", text)

    # Strip leading '#' but keep the hashtag word
    text = re.sub(r"#(\w+)", r"\1", text)

    text = re.sub(r"<.*?>", " ", text)

    # Demojize if the emoji package is available
    try:
        import emoji
        text = emoji.demojize(text, delimiters=(" ", " "))
    except ImportError:
        pass

    # emoji.demojize() output uses underscores, e.g. :red_heart:
    text = text.replace("_", " ")

    text = re.sub(r"[^\x00-\x7F]+", " ", text)

    text = re.sub(r"\s+", " ", text).strip()

    return text.lower()

def _load(path:str) -> Any:
    """Deserialise a pickle file and return its contents."""
    with open(path, "rb") as f:
        return pickle.load(f)

class LogisticRegressionSentiment:
    """
    TF-IDF + Logistic Regression sentiment classifier.
    Classical ML baseline from the pre-transformer era.
    Train with: python scripts/train_classical_models.py
    """

    def __init__(self) -> None:
        if not os.path.exists(LR_PATH) or not os.path.exists(VEC_PATH):
            log.warning("LR model not found. Run: python scripts/train_classical_models.py")
            self._loaded = False
            return

        self.vectorizer: Any = _load(VEC_PATH)
        self.model: Any = _load(LR_PATH)

        self._loaded = True
        log.info("Logistic Regression model loaded from %s", LR_PATH)

    def predict(self, text: str) -> dict[str, object]:
        """Predict sentiment; returns label, confidence, per-class scores, model name."""

        if not self._loaded:
            return {
                "label": "unknown",
                "confidence": 0.0,
                "scores": {
                    "positive": 0.0,
                    "neutral": 0.0,
                    "negative": 0.0,
                },
                "model": "logistic_regression",
                "error": "Model not trained. Run: python scripts/train_classical_models.py",
            }

        # Apply the same preprocessing used during model training
        cleaned: str = _clean(text)

        vec = self.vectorizer.transform([cleaned])

        probs: list[float] = list(self.model.predict_proba(vec)[0])
        pred_id: int = int(self.model.predict(vec)[0])
        classes: list[int] = list(self.model.classes_)

        score_map: dict[str, float] = {
            LABEL_MAP.get(c, str(c)): round(float(p), 4)
            for c, p in zip(classes, probs)
        }

        for k in ["positive", "neutral", "negative"]:
            score_map.setdefault(k, 0.0)

        label: str = LABEL_MAP.get(pred_id, str(pred_id))

        return {
            "label": label,
            "confidence": round(float(max(probs)), 4),
            "scores": score_map,
            "model": "logistic_regression",
        }


class NaiveBayesSentiment:
    """
    TF-IDF + Multinomial Naive Bayes sentiment classifier.
    Simple probabilistic baseline.
    Train with: python scripts/train_classical_models.py
    """

    def __init__(self) -> None:
        if not os.path.exists(NB_PATH) or not os.path.exists(VEC_PATH):
            log.warning("NB model not found. Run: python scripts/train_classical_models.py")
            self._loaded = False
            return

        self.vectorizer: Any = _load(VEC_PATH)
        self.model: Any = _load(NB_PATH)

        self._loaded = True
        log.info("Naive Bayes model loaded from %s", NB_PATH)

    def predict(self, text: str) -> dict[str, object]:
        """Predict sentiment; returns label, confidence, per-class scores, model name."""

        if not self._loaded:
            return {
                "label": "unknown",
                "confidence": 0.0,
                "scores": {
                    "positive": 0.0,
                    "neutral": 0.0,
                    "negative": 0.0,
                },
                "model": "naive_bayes",
                "error": "Model not trained. Run: python scripts/train_classical_models.py",
            }

        # Apply the same preprocessing used during model training
        cleaned: str = _clean(text)

        vec = self.vectorizer.transform([cleaned])

        probs: list[float] = list(self.model.predict_proba(vec)[0])
        pred_id: int = int(self.model.predict(vec)[0])
        classes: list[int] = list(self.model.classes_)

        score_map: dict[str, float] = {
            LABEL_MAP.get(c, str(c)): round(float(p), 4)
            for c, p in zip(classes, probs)
        }

        for k in ["positive", "neutral", "negative"]:
            score_map.setdefault(k, 0.0)

        label: str = LABEL_MAP.get(pred_id, str(pred_id))

        return {
            "label": label,
            "confidence": round(float(max(probs)), 4),
            "scores": score_map,
            "model": "naive_bayes",
        }