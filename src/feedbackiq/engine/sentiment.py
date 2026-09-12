"""
Sentiment for a batch of feedback.

The dissertation path scored one review per call, which is fine for a demo and wrong
for an import of ten thousand rows. `BatchSentimentModel` tokenises and runs the whole
batch in one forward pass, which is where nearly all of the speed-up comes from on CPU.

Padding does not change a prediction: the attention mask tells the model which tokens
are real. The production benchmark checks that batched and single-item scoring agree.
"""

from __future__ import annotations

from typing import Any, Protocol, Sequence

from feedbackiq.core.config import settings
from feedbackiq.core.logging import get_logger
from feedbackiq.engine.errors import ModelUnavailableError
from feedbackiq.engine.types import SentimentPrediction
from feedbackiq.engine.versions import sentiment_model_version

log = get_logger("engine.sentiment")


class SentimentModel(Protocol):
    """Anything that can label a batch of texts."""

    def predict_batch(self, texts: Sequence[str]) -> list[SentimentPrediction]:
        ...


class BatchSentimentModel:
    """
    The fine-tuned DistilBERT classifier, scored in batches.

    `classifier` is the object from `feedbackiq.nlp.sentiment.get_finetuned()`; it is
    injectable so tests can pass a fake and never load 256 MB of weights. When the
    fine-tuned model is unavailable that object falls back to RoBERTa internally, and
    this class then scores one text at a time through its `predict()` - correctness
    first, speed second.
    """

    def __init__(
        self,
        classifier: Any | None = None,
        *,
        batch_size: int = 32,
        max_length: int | None = None,
        model_version: str | None = None,
    ) -> None:
        self._classifier = classifier
        self.batch_size = batch_size
        self.max_length = max_length or settings.MAX_SEQ_LENGTH
        self._model_version = model_version

    # ---------------------------------------------------------------- setup

    def _get_classifier(self) -> Any:
        if self._classifier is None:
            from feedbackiq.nlp.sentiment import get_finetuned

            self._classifier = get_finetuned()
        return self._classifier

    @property
    def model_version(self) -> str:
        if self._model_version is None:
            self._model_version = sentiment_model_version()
        return self._model_version

    # ---------------------------------------------------------------- scoring

    def predict_batch(self, texts: Sequence[str]) -> list[SentimentPrediction]:
        if not texts:
            return []

        classifier = self._get_classifier()

        # A loaded fine-tuned model exposes its tokenizer and weights, so we can run
        # a real batch. Anything else (the RoBERTa fallback, or an injected fake) is
        # scored per item through its own predict().
        if getattr(classifier, "_loaded", False) and hasattr(classifier, "tokenizer"):
            return self._predict_batched(classifier, texts)

        return [self._predict_single(classifier, text) for text in texts]

    def _predict_single(self, classifier: Any, text: str) -> SentimentPrediction:
        raw = classifier.predict(text)

        return SentimentPrediction(
            label=str(raw["label"]),
            confidence=float(raw.get("confidence", 0.0)),
            scores={k: float(v) for k, v in (raw.get("scores") or {}).items()},
            model_version=str(raw.get("model") or self.model_version),
        )

    def _predict_batched(self, classifier: Any, texts: Sequence[str]) -> list[SentimentPrediction]:
        import torch  # local import: keeps torch out of the import path for light callers

        predictions: list[SentimentPrediction] = []
        id2label = classifier.id2label

        for start in range(0, len(texts), self.batch_size):
            chunk = [str(t) for t in texts[start:start + self.batch_size]]

            inputs = classifier.tokenizer(
                chunk,
                return_tensors="pt",
                truncation=True,
                padding=True,
                max_length=self.max_length,
            )

            with torch.no_grad():
                logits = classifier.model(**inputs).logits
                probabilities = torch.softmax(logits, dim=-1)

            for row in probabilities:
                scores = [float(p) for p in row.tolist()]
                best = max(range(len(scores)), key=lambda i: scores[i])
                labels = [str(id2label.get(i, i)).lower() for i in range(len(scores))]

                predictions.append(
                    SentimentPrediction(
                        label=labels[best],
                        confidence=round(scores[best], 4),
                        scores={label: round(score, 4) for label, score in zip(labels, scores)},
                        model_version=self.model_version,
                    )
                )

        return predictions


class SentimentWithFallback:
    """
    A primary model with a cheaper stand-in for when it fails.

    The dissertation app fell back from DistilBERT to VADER so a single bad call could
    not empty the whole response; that behaviour is preserved here, but it is explicit
    and testable instead of a `try/except` buried in a service. The fallback's own
    model version is recorded, so a result never claims to come from a model that
    did not produce it.
    """

    def __init__(self, primary: SentimentModel, fallback: SentimentModel | None = None) -> None:
        self.primary = primary
        self.fallback = fallback

    def predict_batch(self, texts: Sequence[str]) -> list[SentimentPrediction]:
        try:
            return self.primary.predict_batch(texts)
        except Exception as exc:
            if self.fallback is None:
                raise ModelUnavailableError(
                    f"Sentiment model failed and no fallback is configured: {exc}"
                ) from exc

            log.warning("Primary sentiment model failed (%s); using the fallback.", type(exc).__name__)
            return self.fallback.predict_batch(texts)


class SingleCallSentimentModel:
    """
    Adapter for the dissertation's one-text-at-a-time classifiers (VADER, RoBERTa,
    logistic regression, Naive Bayes), so any of them satisfies `SentimentModel`.
    """

    def __init__(self, classifier: Any, *, model_version: str | None = None) -> None:
        self._classifier = classifier
        self._model_version = model_version

    def predict_batch(self, texts: Sequence[str]) -> list[SentimentPrediction]:
        results: list[SentimentPrediction] = []

        for text in texts:
            raw = self._classifier.predict(text)
            results.append(
                SentimentPrediction(
                    label=str(raw["label"]),
                    confidence=float(raw.get("confidence", 0.0)),
                    scores={k: float(v) for k, v in (raw.get("scores") or {}).items()},
                    model_version=self._model_version or str(raw.get("model", "unknown")),
                )
            )

        return results
