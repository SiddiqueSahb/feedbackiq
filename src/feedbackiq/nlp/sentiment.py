"""
Five sentiment classifiers for dissertation comparison:
  1. Naive Bayes         — classical ML baseline (TF-IDF)
  2. Logistic Regression — classical ML baseline (TF-IDF)
  3. VADER               — rule-based NLP baseline
  4. RoBERTa             — pre-trained transformer (no fine-tuning)
  5. DistilBERT          — fine-tuned transformer (YOUR CONTRIBUTION)
"""

from __future__ import annotations
import sys,os
import threading
import torch
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Optional
from functools import lru_cache
from config import settings


def _thread_safe_cache(build_fn):
    """Zero-arg builder as a once-only lazy singleton. lru_cache alone doesn't
    stop two threads both building on a cold cache; the lock closes that gap."""
    lock = threading.Lock()
    cached = lru_cache(maxsize=1)(build_fn)

    def get():
        with lock:
            return cached()

    return get

class VaderSentiment:
    """Rule-based sentiment analyser using VADER compound score."""

    def __init__(self)-> None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        self.analyzer = SentimentIntensityAnalyzer()

    def predict(self,text:str) -> dict[str,object]:
        """Return sentiment label, confidence, and per-class scores."""
        if not str(text).strip():
            raise ValueError("Text cannot be empty.")
        scores: dict[str,float] = self.analyzer.polarity_scores(str(text))
        compound:float = scores["compound"]
        if compound >= 0.05:
            label = "positive"
        elif compound <= -0.05:
            label = "negative"
        else:
            label = "neutral"
        return {
            "label":label,
            "confidence": round(abs(compound),4),
            "scores" : {
                "positive": round(scores["pos"], 4),
                "neutral":  round(scores["neu"], 4),
                "negative": round(scores["neg"], 4),
            },
            "model": "vader",

        }

class RobertaSentiment:
    """cardiffnlp/twitter-roberta-base-sentiment-latest — pre-trained, no fine-tuning."""
    def __init__(self) -> None:
        from transformers import pipeline
        self.pipe = pipeline("text-classification",model=settings.ROBERTA_MODEL,top_k=None,truncation=True, max_length=512)

        self.label_map: dict[str,str] = {
            "positive": "positive",
            "negative": "negative",
            "neutral":  "neutral",
            "label_0":  "negative",   # some versions use numeric labels
            "label_1":  "neutral",
            "label_2":  "positive",
        }
    
    def predict(self,text:str) -> dict[str,object]:
        """Return sentiment label, confidence, and per-class scores."""
        results = self.pipe(str(text)[:512])[0]
        score_map:dict[str,float] = {}
        for item in results:
            lbl = self.label_map.get(item["label"].lower(),item["label"].lower())
            score_map[lbl] = round(item["score"],4)

        best:str = max(score_map,key=score_map.get)
        return {
            "label":best,
            "confidence":score_map[best],
            "scores":score_map,
            "model":"roBERTa"
        }      

    # Loads from settings.MODEL_PATH (default: models/distilbert-finetuned)

class FineTunedSentiment:
        """Fine-tuned DistilBERT classifier; falls back to RoBERTa if the model isn't found."""

        def __init__(self,model_path: Optional[str] = None) -> None:
            model_path = model_path or settings.MODEL_PATH
            if not os.path.exists(model_path):
                print(f" Fine-tuned model not found at {model_path}. "
                  f"Run fine-tuning on Colab first. Falling back to RoBERTa.")
                self._fallback: Optional[RobertaSentiment] = RobertaSentiment()
                self._loaded   = False
                return
        
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            self.tokenizer = AutoTokenizer.from_pretrained(model_path)
            self.model = AutoModelForSequenceClassification.from_pretrained(model_path)
            self.model.eval()
            self.id2label:dict[int,str] = self.model.config.id2label
            self._loaded = True
            self._fallback = None


        def predict(self, text: str) -> dict[str, object]:
            """Return the predicted sentiment, confidence, and class probabilities."""

            if not self._loaded:
                result: dict[str, object] = self._fallback.predict(text)
                result["model"] = "roberta_fallback"
                return result

            inputs = self.tokenizer(str(text), return_tensors="pt", truncation=True, max_length=128)

            with torch.no_grad():

                logits = self.model(**inputs).logits

                probs: list[float] = torch.softmax(logits, dim=-1).squeeze().tolist()

                pred_id: int = int(torch.argmax(logits))

                label_names: list[str] = [self.id2label.get(i, str(i)) for i in range(len(probs))]

                score_map: dict[str, float] = {lbl.lower(): round(p, 4) for lbl, p in zip(label_names, probs)}

                return {
                "label": label_names[pred_id].lower(),
                "confidence": round(probs[pred_id], 4),
                "scores": score_map,
                "model": "distilbert_finetuned",
                }

    # cached after first call, thread-safe against concurrent cold-start calls
get_vader = _thread_safe_cache(VaderSentiment)
"""Return cached VaderSentiment instance."""

get_roberta = _thread_safe_cache(RobertaSentiment)
"""Return cached RobertaSentiment instance."""

get_finetuned = _thread_safe_cache(FineTunedSentiment)
"""Return cached FineTunedSentiment instance."""


def _build_logistic_regression():
    from nlp.classical_models import LogisticRegressionSentiment
    return LogisticRegressionSentiment()


def _build_naive_bayes():
    from nlp.classical_models import NaiveBayesSentiment
    return NaiveBayesSentiment()


get_logistic_regression = _thread_safe_cache(_build_logistic_regression)
"""Return cached LogisticRegressionSentiment instance."""

get_naive_bayes = _thread_safe_cache(_build_naive_bayes)
"""Return cached NaiveBayesSentiment instance."""


def compare_all(text:str) -> dict[str,dict[str,object]]:
    """Run all 5 models (classical -> rule-based -> pretrained -> fine-tuned);
    returns a dict keyed by model name."""

    results:dict[str,dict[str,object]] = {}

    try:
        results["naive_bayes"] = get_naive_bayes().predict(text)
    except Exception as e:
        results["naive_bayes"] = {
            "label": "error",
            "confidence": 0.0,
            "scores": {}, "model": 
            "naive_bayes", "error": str(e)
            }
    try:
        results["logistic_regression"] = get_logistic_regression().predict(text)
    except Exception as e:
        results["logistic_regression"] = {
            "label": "error",
            "confidence": 0.0,
            "scores": {},
            "model": "logistic_regression",
            "error": str(e)
            }

    # Tier 2 — Rule-based
    try:
        results["vader"] = get_vader().predict(text)
    except Exception as e:
        results["vader"] = {
            "label": "error",
            "confidence": 0.0,
            "scores": {},
            "model": "vader", "error": str(e)}

    # Tier 3 — Pre-trained transformer
    try:
        results["roberta"] = get_roberta().predict(text)
    except Exception as e:
        results["roberta"] = {
            "label": "error",
            "confidence": 0.0,
            "scores": {},
            "model": "roberta",
            "error": str(e)
            }

    # Tier 4 — Fine-tuned transformer (key dissertation contribution)
    try:
        results["distilbert_finetuned"] = get_finetuned().predict(text)
    except Exception as e:
        results["distilbert_finetuned"] = {
            "label": "error",
            "confidence": 0.0,
            "scores": {},
            "model": "distilbert_finetuned-final",
            "error": str(e)}

    return results


def predict_sentiment(text: str) -> dict[str, object]:
    """Predict sentiment with the fine-tuned DistilBERT model — the production model used by the app."""
    return get_finetuned().predict(text)


if __name__ == "__main__":
    test_text = "The product arrived quickly and was easy to install. However, it disconnects several times a day, the battery lasts less than three hours, and customer support has not responded to my emails. Overall, I cannot recommend this product."
    

    results = compare_all(test_text)

    print("\n" + "=" * 70)
    print(f"Input: {test_text}")
    print("=" * 70)

    for model_name, result in results.items():
        print(f"\nModel: {model_name}")
        print(f"Label      : {result['label']}")
        print(f"Confidence : {result['confidence']}")
        print(f"Scores     : {result['scores']}")
        print(f"Model Used : {result['model']}")

        if "error" in result:
            print(f"Error      : {result['error']}")