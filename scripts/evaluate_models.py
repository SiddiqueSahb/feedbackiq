"""
Full Model Comparison + MLflow Experiment Tracking

Evaluates ALL 5 models and logs every metric to MLflow (free, local).
Best model is automatically registered in MLflow Model Registry.

After running, open the MLflow dashboard:
    mlflow ui --backend-store-uri mlruns
    → http://localhost:5000

Usage:
    python scripts/evaluate_models.py

Prerequisites:
    1. python scripts/preprocess.py
    2. python scripts/train_classical_models.py
    3. Fine-tuned DistilBERT in models/distilbert-finetuned/ (optional)
"""


import os,sys
import json
from typing import Any

import pandas as pd
import numpy as np
import mlflow
import mlflow.sklearn
from mlflow import MlflowClient
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
    confusion_matrix,
    classification_report,
)
from feedbackiq.core.config import settings


GREEN  = "\033[92m"; YELLOW = "\033[93m"; CYAN = "\033[96m"
BOLD   = "\033[1m";  RESET  = "\033[0m";  RED  = "\033[91m"


def header(msg: str) -> None:
    print(f"\n{BOLD}{'─' * 60}\n  {msg}\n{'─' * 60}{RESET}")

#mlflow setup
mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
mlflow.set_experiment(settings.MLFLOW_EXPERIMENT)
print(f"\n{CYAN}MLflow tracking → {os.path.abspath(settings.MLFLOW_TRACKING_URI)}{RESET}")
print(f"{CYAN}Experiment      → {settings.MLFLOW_EXPERIMENT}{RESET}")
print(f"{CYAN}Dashboard       → run: mlflow ui   then open http://localhost:5000{RESET}")

#load test set
header("Loading Dataset")
if not os.path.exists(settings.DATA_PATH):
    print(f"{RED} Data not found. Run: python scripts/preprocess.py{RESET}")
    sys.exit(1)

df: pd.DataFrame = pd.read_parquet(settings.DATA_PATH)
df = df.dropna(subset=["cleaned_text", "sentiment_label"])
df = df[df["cleaned_text"].str.len() > 5]
label_map:   dict[str, int] = {"negative": 0, "neutral": 1, "positive": 2}
label_names: list[str]      = ["negative", "neutral", "positive"]
df["label_id"] = df["sentiment_label"].map(label_map)
df = df.dropna(subset=["label_id"])
df["label_id"] = df["label_id"].astype(int)

_,test_df = train_test_split(df,test_size=0.2,random_state=42,stratify=df["label_id"])
texts: list[str] = list(test_df["cleaned_text"])
y_true: list[str] = list(test_df["label_id"])
print(f"Distribution: {test_df['sentiment_label'].value_counts().to_dict()}")


all_results: dict[str, dict[str, float]] = {}
os.makedirs("data/results", exist_ok=True)


#core evaluation function
def evaluate_and_log(model_name:str,preds:list[int],model_type:str,params:dict[str,Any]| None = None,sklearn_model:Any = None) -> dict[str, float]:
    """Compute metrics, log to MLflow, print results """
    acc  = accuracy_score(y_true, preds)
    f1m  = f1_score(y_true, preds, average="macro",    zero_division=0)
    f1w  = f1_score(y_true, preds, average="weighted", zero_division=0)
    prec = precision_score(y_true, preds, average="macro", zero_division=0)
    rec  = recall_score(y_true, preds, average="macro",    zero_division=0)
    cm   = cm = confusion_matrix(y_true, preds).tolist()

    metrics: dict[str, float] = {
        "accuracy":    round(acc,  4),
        "f1_macro":    round(f1m,  4),
        "f1_weighted": round(f1w,  4),
        "precision":   round(prec, 4),
        "recall":      round(rec,  4),
    }

    # Log to MLflow 
    with mlflow.start_run(run_name=model_name):
        # Tags - mlflow.set_tags() is used to store metadata about an experiment run.
        mlflow.set_tags({
            "model_name": model_name,
            "model_type": model_type,   # classical_ml / rule_based / transformer
            "dataset":    settings.DATA_PATH,
            "test_size":  len(texts),
        })

        # Parameters
        if params:
            print(params)
            print(type(params))
            print("Logging params...")
            mlflow.log_params(params)
            

        # Metrics
        print("Logging metrics...")
        mlflow.log_metrics(metrics)

        # Confusion matrix as JSON artifact
        print("Saving confusion matrix...")
        cm_path = f"data/results/cm_{model_name.replace(' ', '_').lower()}.json"
        with open(cm_path, "w") as f:
            json.dump({"confusion_matrix": cm, "labels": label_names}, f, indent=2)
        mlflow.log_artifact(cm_path, artifact_path="confusion_matrices")

        # Classification report as artifact
        print("Saving report...")
        report = classification_report(
            y_true, preds, target_names=label_names, digits=4, output_dict=True
        )
        report_path = f"data/results/report_{model_name.replace(' ', '_').lower()}.json"
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        mlflow.log_artifact(report_path, artifact_path="classification_reports")

        # Log sklearn model if provided (enables model registry)
        if sklearn_model is not None:
            print("Logging sklearn model...")
            mlflow.sklearn.log_model(sklearn_model, artifact_path="model")

    all_results[model_name] = metrics
    print(f"  {GREEN}{RESET}  {model_name:<35} "
          f"Acc={acc * 100:.1f}%  F1_macro={f1m:.3f}  "
          f"F1_weighted={f1w:.3f}  {GREEN}→ logged to MLflow{RESET}")
    return metrics


# Model 1: Naive Bayes
header("Model 1 — Naive Bayes (TF-IDF)  [Classical ML Baseline]")
try:
    from feedbackiq.nlp.classical_models import NaiveBayesSentiment
    nb = NaiveBayesSentiment()
    if nb._loaded:
        preds = [label_map.get(nb.predict(t)["label"], 1) for t in texts]
        evaluate_and_log(
            "Naive Bayes",
            preds,
            model_type="classical_ml",
            params={"vectorizer": "tfidf", "algorithm": "multinomial_nb", "alpha": 0.1},
            sklearn_model=nb.model,
        )
    else:
        print(f"  {YELLOW}  Not trained. Run: python scripts/train_classical_models.py{RESET}")
except Exception as e:
    print(f"  {RED} Failed: {e}{RESET}")


#Model 2: Logistic Regression
header("Model 2 — Logistic Regression (TF-IDF)  [Classical ML Baseline]")
try:
    from feedbackiq.nlp.classical_models import LogisticRegressionSentiment
    lr = LogisticRegressionSentiment()
    if lr._loaded:
        preds = [label_map.get(lr.predict(t)["label"], 1) for t in texts]
        evaluate_and_log(
            "Logistic Regression",
            preds,
            model_type="classical_ml",
            params={"vectorizer": "tfidf", "algorithm": "logistic_regression",
                    "C": 1.0, "max_iter": 3000, "ngram_range": "(1,2)"},
            sklearn_model=lr.model,
        )
    else:
        print(f"  {YELLOW}  Not trained. Run: python scripts/train_classical_models.py{RESET}")
except Exception as e:
    print(f"  {RED}  Failed: {e}{RESET}")


# Model 3: VADER
header("Model 3 — VADER  [Rule-based Baseline]")
try:
    from feedbackiq.nlp.sentiment import VaderSentiment
    vader = VaderSentiment()
    preds = [label_map.get(vader.predict(t)["label"], 1) for t in texts]
    evaluate_and_log(
        "VADER",
        preds,
        model_type="rule_based",
        params={"algorithm": "vader", "threshold_pos": 0.05, "threshold_neg": -0.05},
    )
except Exception as e:
    print(f"  {RED}  Failed: {e}{RESET}")


# Model 4: RoBERTa
header("Model 4 — RoBERTa  [Pre-trained Transformer]")
print("  Loading RoBERTa (downloads ~500 MB on first run)...")
try:
    from feedbackiq.nlp.sentiment import RobertaSentiment
    roberta = RobertaSentiment()
    preds   = [label_map.get(roberta.predict(t)["label"], 1) for t in texts]
    evaluate_and_log(
        "RoBERTa (pre-trained)",
        preds,
        model_type="pretrained_transformer",
        params={"model": settings.ROBERTA_MODEL, "max_length": 512,
                "fine_tuned": False},
    )
except Exception as e:
    print(f"  {RED}  Failed: {e}{RESET}")


# Model 5: DistilBERT Fine-tuned 
header("Model 5 — DistilBERT (Fine-tuned)  [YOUR CONTRIBUTION]")
try:
    from feedbackiq.nlp.sentiment import FineTunedSentiment
    ft = FineTunedSentiment()
    if ft._loaded:
        preds = [label_map.get(ft.predict(t)["label"], 1) for t in texts]
        evaluate_and_log(
            "DistilBERT (fine-tuned)",
            preds,
            model_type="finetuned_transformer",
            params={"base_model": "distilbert-base-uncased",
                    "fine_tuned": True,
                    "max_length": 128,
                    "model_path": settings.MODEL_PATH},
        )
    else:
        print(f"  {YELLOW}  Model not found — run Colab training first{RESET}")
        print(f"     Then copy model to: {settings.MODEL_PATH}")
except Exception as e:
    print(f"  {RED}  Failed: {e}{RESET}")



# Print dissertation results table
if all_results:
    header("DISSERTATION RESULTS TABLE")

    metrics_cols = ["accuracy", "f1_macro", "f1_weighted", "precision", "recall"]
    headers      = ["Model", "Accuracy", "F1 Macro", "F1 Weighted", "Precision", "Recall"]

    print(f"\n  {headers[0]:<35}", end="")
    for h in headers[1:]:
        print(f"  {h:>11}", end="")
    print(f"\n  {'─' * 35}", end="")
    for _ in headers[1:]:
        print(f"  {'─' * 11}", end="")
    print()

    best_f1    = max(all_results.values(), key=lambda x: x["f1_macro"])["f1_macro"]
    best_model = max(all_results, key=lambda k: all_results[k]["f1_macro"])

    for name, m in all_results.items():
        marker = f" {GREEN}← BEST{RESET}" if name == best_model else ""
        print(f"  {name:<35}", end="")
        for col in metrics_cols:
            val = m.get(col, 0)
            print(f"  {val * 100:>10.1f}%", end="")
        print(marker)

    print(f"\n  {GREEN}{BOLD}Best model: {best_model}  "
          f"(F1 Macro = {best_f1 * 100:.1f}%){RESET}")

    #Save combined results
    out_path = "data/results/model_comparison.json"
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\n  Results saved → {out_path}")

    #  MLflow Model Registry: register best overall model 
    header("MLflow Model Registry — Registering Best Model")
    client = MlflowClient()

    # Map display name → registered model name
    registry_name_map: dict[str, str] = {
        "Naive Bayes":              "FeedbackIQ-NaiveBayes",
        "Logistic Regression":      "FeedbackIQ-LogisticRegression",
        "VADER":                    "FeedbackIQ-VADER",
        "RoBERTa (pre-trained)":    "FeedbackIQ-RoBERTa",
        "DistilBERT (fine-tuned)":  "FeedbackIQ-DistilBERT",
    }
    #just chnaging the name 
    reg_name = registry_name_map.get(best_model, "FeedbackIQ-BestModel")


    # Find the MLflow run for the best model and create a registry entry
    try:
        experiment = mlflow.get_experiment_by_name(settings.MLFLOW_EXPERIMENT)
        if experiment:
            runs = mlflow.search_runs(
                experiment_ids=[experiment.experiment_id],
                filter_string=f"tags.model_name = '{best_model}'",
                order_by=["metrics.f1_macro DESC"],
                max_results=1,
            )
            if not runs.empty:
                run_id = runs.iloc[0]["run_id"]
                model_uri = f"runs:/{run_id}/model"
                try:
                    mv = mlflow.register_model(model_uri, reg_name)
                    print(f"  {GREEN}  Best model '{best_model}' registered as '{reg_name}' "
                          f"v{mv.version}{RESET}")

                    # Promote to Production
                    client.transition_model_version_stage(
                        name=reg_name,
                        version=mv.version,
                        stage="Production",
                        archive_existing_versions=True,
                    )
                    print(f"  {GREEN}  {reg_name} v{mv.version} promoted to 'Production'{RESET}")
                except Exception as exc:
                    print(f"  {YELLOW}  Registry stage transition not supported in this "
                          f"MLflow version ({exc.__class__.__name__}) — model logged without stage{RESET}")
    except Exception as exc:
        print(f"  {YELLOW}  Model Registry step skipped: {exc}{RESET}")

    print(f"""
{CYAN}{BOLD}MLflow Dashboard:{RESET}
  Run:  mlflow ui --backend-store-uri mlruns
  Open: http://localhost:5000
  → Compare all models, view charts, download metrics

{CYAN}{BOLD}What to do next:{RESET}
  1. Open MLflow dashboard → screenshot for dissertation
  2. Copy results table above into your Results chapter
  3. Run: python evaluate/evaluate_llm_vs_rag.py
""")
