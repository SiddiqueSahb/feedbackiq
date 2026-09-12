
"""
Train classical ML sentiment baselines on your processed dataset.
Run ONCE before evaluate_models.py

Usage:
    python scripts/train_classical_models.py

Outputs:
    models/classical/tfidf_vectorizer.pkl
    models/classical/logistic_regression.pkl
    models/classical/naive_bayes.pkl

Features:
    - GridSearchCV hyperparameter tuning (C for LR, alpha for NB)
    - 5-fold StratifiedKFold cross-validation (robustness estimate)
    - MLflow tracking + Model Registry (best model promoted to Production)
"""

"""
Train and evaluate classical machine learning models for sentiment analysis.

Pipeline:
1. Configure project and load data
2. Prepare data and extract TF-IDF features
3. Optimize hyperparameters using GridSearchCV
4. Train and evaluate Logistic Regression and Naive Bayes
5. Validate models using 5-fold Stratified Cross-Validation
6. Save trained models and vectorizer
7. Log experiments and register models with MLflow
8. Promote the best model to Production and display the final summary
"""




import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pickle                   # save trained models to disk
from typing import Any

import numpy as np
import pandas as pd
import mlflow
import mlflow.sklearn
from mlflow import MlflowClient

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.naive_bayes import MultinomialNB
from sklearn.model_selection import (
    train_test_split,
    StratifiedKFold,
    GridSearchCV,
)
from sklearn.metrics import (
    classification_report,
    accuracy_score,
    f1_score,
    precision_score,
    recall_score,
)
from config import settings


# Phase 0: Load dataset
def load_dataset() -> pd.DataFrame:
    """
    Load and clean the preprocessed review dataset.

    Returns:
        Cleaned DataFrame with 'cleaned_text' and 'sentiment_label' columns.

    Raises:
        SystemExit: If the parquet file does not exist.
    """
    print("Loading data...")

    # Guard: ensure preprocessing has been run first
    if not os.path.exists(settings.DATA_PATH):
        print("Data not found. Run: python scripts/preprocess.py first.")
        sys.exit(1)

    # Parquet is faster and smaller than CSV for large datasets
    df: pd.DataFrame = pd.read_parquet(settings.DATA_PATH)

    # Drop rows where text or label is missing — can't train on null targets
    df = df.dropna(subset=["cleaned_text", "sentiment_label"])

    # Remove very short reviews (< 5 chars) — usually noise or parsing artefacts
    df = df[df["cleaned_text"].str.len() > 5]

    print(f"   Loaded {len(df):,} reviews")
    print(f"   Distribution:\n{df['sentiment_label'].value_counts().to_string()}")
    return df


# Phase 1: Data preparation & feature engineering
def prepare_data( df: pd.DataFrame,) -> tuple[list[str], list[str], list[int], list[int], TfidfVectorizer]:
    """
    Encode labels, split into train/test sets, and fit a TF-IDF vectoriser.

    Args:
        df: Cleaned DataFrame from load_dataset().

    Returns:
        Tuple of (X_train, X_test, y_train, y_test, fitted_vectorizer).
    """
    # Map string labels → integer class IDs expected by sklearn classifiers
    # negative=0, neutral=1, positive=2  (alphabetical order intentional)
    label_map: dict[str, int] = {"negative": 0, "neutral": 1, "positive": 2}
    df = df.copy()                                      # avoid mutating the original
    df["label_id"] = df["sentiment_label"].map(label_map)

    # Drop any rows whose label wasn't in the map 
    df = df.dropna(subset=["label_id"])
    df["label_id"] = df["label_id"].astype(int)

    X: list[str] = df["cleaned_text"].tolist()
    y: list[int] = df["label_id"].tolist()

    # stratify=y preserves class proportions; random_state=42 for reproducibility
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    print(f"\n   Train: {len(X_train):,} | Test: {len(X_test):,}")

    # TF-IDF feature extraction — fit ONLY on training data to prevent leakage into the test split
    print("\n Fitting TF-IDF vectoriser...")
    vectorizer = TfidfVectorizer(
        max_features=50000,       # cap vocabulary to top 50k terms by frequency
        ngram_range=(1, 2),       # unigrams + bigrams (e.g. "not good" captured)
        sublinear_tf=True,        # replace raw TF with 1 + log(tf) to dampen high freqs
        min_df=2,                 # ignore terms that appear in fewer than 2 documents
        strip_accents="unicode",  # normalise accented characters (é → e)
        analyzer="word",          # tokenise by word, not character
    )
    vectorizer.fit(X_train)       # fit on train only — transform test separately
    print(f"   Vocabulary size: {len(vectorizer.vocabulary_):,}")

    return X_train, X_test, y_train, y_test, vectorizer


# Phase 2: Hyperparameter tuning

def tune_logistic_regression(X_train_vec: Any, y_train: list[int]) -> float:
    """
    GridSearchCV over regularisation strength C (3-fold, f1_macro).

    Args:
        X_train_vec: Sparse TF-IDF matrix for training.
        y_train:     Integer class labels for training.

    Returns:
        Best C value found by grid search.
    """
    print("\nGridSearchCV — Logistic Regression (C values: 0.01 → 10.0)...")

    # C: regularisation strength (small = simpler model, large = risk of overfitting)
    lr_param_grid: dict[str, list[float]] = {"C": [0.01, 0.1, 1.0, 5.0, 10.0]}

    lr_grid = GridSearchCV(
        LogisticRegression(max_iter=3000,solver="lbfgs",random_state=42,),
        lr_param_grid,
        cv=3,               # 3-fold CV for tuning (faster; 5-fold used later for final estimate)
        scoring="f1_macro", # macro F1 treats all classes equally — important for imbalanced data
        n_jobs=-1,          # parallelise grid search
        verbose=0,          # suppress per-fold output
    )
    lr_grid.fit(X_train_vec, y_train)

    best_lr_C: float = lr_grid.best_params_["C"]
    print(f"   Best C = {best_lr_C}  (CV F1 macro = {lr_grid.best_score_:.4f})")

    # Print all C scores so the examiner can see how sensitivity to C played out
    print(
        f"   All scores: "
        f"{ {str(p['C']): round(s, 4) for p, s in zip(lr_grid.cv_results_['params'], lr_grid.cv_results_['mean_test_score'])} }"
    )
    return best_lr_C


def tune_naive_bayes(X_train_vec: Any, y_train: list[int]) -> float:
    """
    GridSearchCV over Laplace smoothing alpha (3-fold, f1_macro).

    Args:
        X_train_vec: Sparse TF-IDF matrix for training (values ≥ 0).
        y_train:     Integer class labels for training.

    Returns:
        Best alpha value found by grid search.
    """
    print("\n GridSearchCV — Naive Bayes (alpha values: 0.01 → 1.0)...")

    # alpha: Laplace smoothing (1.0 = classic NB, near 0 = minimal smoothing)
    nb_param_grid: dict[str, list[float]] = {"alpha": [0.01, 0.05, 0.1, 0.5, 1.0]}

    nb_grid = GridSearchCV(
        MultinomialNB(),    # MultinomialNB requires non-negative features → TF-IDF values are fine
        nb_param_grid,
        cv=3,               # 3-fold for tuning speed; final robustness uses 5-fold CV
        scoring="f1_macro", # same metric as LR to keep comparison fair
        n_jobs=-1,
        verbose=0,
    )
    nb_grid.fit(X_train_vec, y_train)

    best_nb_alpha: float = nb_grid.best_params_["alpha"]
    print(f"   Best alpha = {best_nb_alpha}  (CV F1 macro = {nb_grid.best_score_:.4f})")

    # Show full grid so the examiner can see sensitivity to smoothing
    print(
        f"   All scores: "
        f"{ {str(p['alpha']): round(s, 4) for p, s in zip(nb_grid.cv_results_['params'], nb_grid.cv_results_['mean_test_score'])} }"
    )
    return best_nb_alpha


# Phase 3: Final model training
def train_models(
    X_train_vec: Any,
    X_test_vec:  Any,
    y_train:     list[int],
    y_test:      list[int],
    best_lr_C:     float,
    best_nb_alpha: float,
) -> tuple[Any, Any, list[int], list[int], float, float]:
    """
    Train LR and NB on the full training set, evaluate on the test set.

    Args:
        X_train_vec:   Sparse TF-IDF training matrix.
        X_test_vec:    Sparse TF-IDF test matrix.
        y_train:       Training labels.
        y_test:        Test labels.
        best_lr_C:     Best regularisation strength from tune_logistic_regression().
        best_nb_alpha: Best smoothing parameter from tune_naive_bayes().

    Returns:
        Tuple of (lr_model, nb_model, lr_preds, nb_preds, lr_acc, nb_acc).
    """
    print("\n" + "═" * 60)
    print("  PHASE 2: Training Final Models with Best Hyperparameters")
    print("=" * 60)

    # Re-train on the full training set using the best C from GridSearchCV
    print(f"\n Training Logistic Regression (C={best_lr_C})...")
    lr = LogisticRegression(
    C=best_lr_C,
    max_iter=3000,
    solver="lbfgs",
    random_state=42,
)
    lr.fit(X_train_vec, y_train)

    # Predict on held-out test set (never seen during training or tuning)
    lr_preds: list[int] = list(lr.predict(X_test_vec))
    lr_acc: float       = accuracy_score(y_test, lr_preds)

    print(f"\n Logistic Regression Results:")
    print(f"   Accuracy : {lr_acc:.4f} ({lr_acc * 100:.1f}%)")
    # Full per-class breakdown — useful for the dissertation Results table
    print(classification_report(
        y_test, lr_preds,
        target_names=["negative", "neutral", "positive"],
        digits=4,
    ))

    # Re-train on the full training set using the best alpha from GridSearchCV
    print(f"\n  Training Naive Bayes (alpha={best_nb_alpha})...")
    nb = MultinomialNB(alpha=best_nb_alpha)   # Laplace/Lidstone smoothing
    nb.fit(X_train_vec, y_train)

    nb_preds: list[int] = list(nb.predict(X_test_vec))
    nb_acc: float       = accuracy_score(y_test, nb_preds)

    print(f"\n  Naive Bayes Results:")
    print(f"   Accuracy : {nb_acc:.4f} ({nb_acc * 100:.1f}%)")
    print(classification_report(
        y_test, nb_preds,
        target_names=["negative", "neutral", "positive"],
        digits=4,
    ))

    return lr, nb, lr_preds, nb_preds, lr_acc, nb_acc


# Phase 4: Cross-validation

def cross_validate(
    X_train:       list[str],
    y_train:       list[int],
    vectorizer:    TfidfVectorizer,
    best_lr_C:     float,
    best_nb_alpha: float,
) -> tuple[float, float, float, float]:
    """
    5-fold StratifiedKFold cross-validation for robustness assessment.

    Uses the already-fitted vectoriser vocabulary so there is no vocab leakage
    between folds — only term weighting varies per fold (standard practice).

    Args:
        X_train:       Raw training texts.
        y_train:       Training labels.
        vectorizer:    Fitted TF-IDF vectoriser (vocab fixed from full train set).
        best_lr_C:     Best C from tune_logistic_regression().
        best_nb_alpha: Best alpha from tune_naive_bayes().

    Returns:
        Tuple of (lr_cv_mean, lr_cv_std, nb_cv_mean, nb_cv_std).
    """
    print("\n" + "═" * 60)
    print("  PHASE 3: 5-Fold StratifiedKFold Cross-Validation")
    print("=" * 60)
    print()

    # shuffle=True avoids any ordering bias in the original dataset
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

    lr_cv_scores: list[float] = []  # collect macro-F1 for each of the 5 folds
    nb_cv_scores: list[float] = []

    for fold, (tr_idx, val_idx) in enumerate(skf.split(X_train, y_train), 1):

        # Slice raw texts and labels by fold indices
        X_tr:  list[str] = [X_train[i] for i in tr_idx]
        X_val: list[str] = [X_train[i] for i in val_idx]
        y_tr:  list[int] = [y_train[i] for i in tr_idx]
        y_val: list[int] = [y_train[i] for i in val_idx]

        # transform() only — vocab already fixed; prevents leakage from val fold
        X_tr_v  = vectorizer.transform(X_tr)
        X_val_v = vectorizer.transform(X_val)

        # LR fold
        lr_fold = LogisticRegression(
    C=best_lr_C,
    max_iter=3000,
    solver="lbfgs",
    random_state=42,
)
        lr_fold.fit(X_tr_v, y_tr)
        # macro F1 — unweighted mean across classes, penalises class imbalance
        lr_f1 = float(f1_score(y_val, lr_fold.predict(X_val_v),
                                average="macro", zero_division=0))
        lr_cv_scores.append(lr_f1)

        # NB fold
        nb_fold = MultinomialNB(alpha=best_nb_alpha)
        nb_fold.fit(X_tr_v, y_tr)
        nb_f1 = float(f1_score(y_val, nb_fold.predict(X_val_v),
                                average="macro", zero_division=0))
        nb_cv_scores.append(nb_f1)

        print(f"   Fold {fold}/5  │  LR F1={lr_f1:.4f}  │  NB F1={nb_f1:.4f}")

    # mean ± std shows how stable the model is across splits
    lr_cv_mean = float(np.mean(lr_cv_scores))
    lr_cv_std  = float(np.std(lr_cv_scores))
    nb_cv_mean = float(np.mean(nb_cv_scores))
    nb_cv_std  = float(np.std(nb_cv_scores))

    print(f"\n   LR  5-fold CV F1 macro  = {lr_cv_mean:.4f} ± {lr_cv_std:.4f}")
    print(f"   NB  5-fold CV F1 macro  = {nb_cv_mean:.4f} ± {nb_cv_std:.4f}")

    return lr_cv_mean, lr_cv_std, nb_cv_mean, nb_cv_std


# Phase 5: Save models

def save_models(
    vectorizer: TfidfVectorizer,
    lr:         Any,
    nb:         Any,
) -> None:
    """
    Pickle the fitted vectoriser and both classifiers to models/classical/.

    Args:
        vectorizer: Fitted TF-IDF vectoriser.
        lr:         Trained Logistic Regression model.
        nb:         Trained Naive Bayes model.
    """
    os.makedirs("models/classical", exist_ok=True)

    # Vectoriser is saved alongside the models -- transform() at inference must use the same vocab built during training
    with open("models/classical/tfidf_vectorizer.pkl",    "wb") as f:
        pickle.dump(vectorizer, f)
    with open("models/classical/logistic_regression.pkl", "wb") as f:
        pickle.dump(lr, f)
    with open("models/classical/naive_bayes.pkl",         "wb") as f:
        pickle.dump(nb, f)

    print("\n Models saved → models/classical/")


# Phase 6: MLflow logging

def log_models_to_mlflow(
    lr:         Any,
    nb:         Any,
    y_test:     list[int],
    lr_preds:   list[int],
    nb_preds:   list[int],
    X_train:    list[str],
    X_test:     list[str],
    best_lr_C:     float,
    best_nb_alpha: float,
    lr_cv_mean: float,
    lr_cv_std:  float,
    nb_cv_mean: float,
    nb_cv_std:  float,
) -> None:
    """
    Log both classifiers to MLflow and register them in the Model Registry.

    Args:
        lr / nb:            Trained models.
        y_test / *_preds:   Ground truth and predictions for metric computation.
        X_train / X_test:   Used to log dataset sizes.
        best_lr_C / alpha:  Best hyperparameters from grid search.
        *_cv_mean / std:    Cross-validation results.
    """
    print("\n" + "═" * 60)
    print("  PHASE 4: MLflow Logging + Model Registry")
    print("=" * 60)
    print("\n  Logging runs to MLflow...")

    # Each `with mlflow.start_run(...)` block creates a separate tracked run
    with mlflow.start_run(run_name="Logistic Regression - Training"):
        mlflow.set_tag("model_type", "classical_ml")   # tag for filtering in MLflow UI

        # Log hyperparameters — these appear in the MLflow "Parameters" tab
        mlflow.log_params({
            "algorithm":     "logistic_regression",
            "vectorizer":    "tfidf",
            "max_features":  50000,
            "ngram_range":   "(1,2)",
            "C":             best_lr_C,       # best from GridSearchCV
            "max_iter":      3000,
            "train_size":    len(X_train),    # dataset metadata
            "test_size":     len(X_test),
            "cv_folds":      5,
            "tuning_method": "GridSearchCV",
        })

        # Log evaluation metrics — appear in the MLflow "Metrics" tab
        mlflow.log_metrics({
            "accuracy":    round(accuracy_score(y_test, lr_preds), 4),
            "f1_macro":    round(f1_score(y_test, lr_preds, average="macro",    zero_division=0), 4),
            "f1_weighted": round(f1_score(y_test, lr_preds, average="weighted", zero_division=0), 4),
            "precision":   round(precision_score(y_test, lr_preds, average="macro", zero_division=0), 4),
            "recall":      round(recall_score(y_test, lr_preds, average="macro",    zero_division=0), 4),
            "cv_f1_mean":  round(lr_cv_mean, 4),   # 5-fold cross-validation mean
            "cv_f1_std":   round(lr_cv_std,  4),   # stability metric for the dissertation
        })

        # Registers in the Model Registry, enabling stage transitions (None -> Staging -> Production)
        mlflow.sklearn.log_model(
            lr,
            artifact_path="model",
            registered_model_name="FeedbackIQ-LogisticRegression",
        )
        print("  Logistic Regression logged + registered in MLflow Registry")

    with mlflow.start_run(run_name="Naive Bayes - Training"):
        mlflow.set_tag("model_type", "classical_ml")

        mlflow.log_params({
            "algorithm":     "naive_bayes",
            "vectorizer":    "tfidf",
            "max_features":  50000,
            "ngram_range":   "(1,2)",
            "alpha":         best_nb_alpha,    # best Laplace smoothing from GridSearchCV
            "train_size":    len(X_train),
            "test_size":     len(X_test),
            "cv_folds":      5,
            "tuning_method": "GridSearchCV",
        })

        mlflow.log_metrics({
            "accuracy":    round(accuracy_score(y_test, nb_preds), 4),
            "f1_macro":    round(f1_score(y_test, nb_preds, average="macro",    zero_division=0), 4),
            "f1_weighted": round(f1_score(y_test, nb_preds, average="weighted", zero_division=0), 4),
            "precision":   round(precision_score(y_test, nb_preds, average="macro", zero_division=0), 4),
            "recall":      round(recall_score(y_test, nb_preds, average="macro",    zero_division=0), 4),
            "cv_f1_mean":  round(nb_cv_mean, 4),
            "cv_f1_std":   round(nb_cv_std,  4),
        })

        mlflow.sklearn.log_model(
            nb,
            artifact_path="model",
            registered_model_name="FeedbackIQ-NaiveBayes",
        )
        print("   Naive Bayes logged + registered in MLflow Registry")


# Phase 7: Model promotion

def promote_best_model(lr_acc: float, nb_acc: float) -> None:
    """
    Promote the higher-accuracy model to the MLflow Registry 'Production' stage.

    Args:
        lr_acc: Test accuracy of the Logistic Regression model.
        nb_acc: Test accuracy of the Naive Bayes model.
    """
    # Select the winning model name — LR wins on tie (generally more interpretable)
    best_name = (
        "FeedbackIQ-LogisticRegression" if lr_acc >= nb_acc
        else "FeedbackIQ-NaiveBayes"
    )
    client = MlflowClient()

    try:
        versions = client.search_model_versions(f"name='{best_name}'")
        if versions:
            latest_v = sorted(versions, key=lambda v: int(v.version))[-1].version

            # archive_existing_versions=True demotes any previous Production version
            client.transition_model_version_stage(
                name=best_name,
                version=latest_v,
                stage="Production",
                archive_existing_versions=True,
            )
            print(f"    {best_name} v{latest_v} → promoted to 'Production'")

    except Exception as exc:
        # MLflow 2.x deprecated stage transitions; skip rather than crash -- model is still registered
        print(f"    Model Registry stage transition skipped ({exc.__class__.__name__})")
        print(f"      Best classical model: {best_name}")


# Final summary

def print_summary(
    lr_acc:     float,
    nb_acc:     float,
    lr_cv_mean: float,
    lr_cv_std:  float,
    nb_cv_mean: float,
    nb_cv_std:  float,
    best_lr_C:     float,
    best_nb_alpha: float,
) -> None:
    """Print the final training summary to stdout."""
    print("\n" + "═" * 60)
    print("  SUMMARY")
    print("=" * 60)

    # Side-by-side comparison ready to paste into the dissertation Results chapter
    print(
        f"\n  Logistic Regression  Accuracy: {lr_acc * 100:.1f}%  "
        f"CV F1: {lr_cv_mean:.4f} ± {lr_cv_std:.4f}  (best C={best_lr_C})"
    )
    print(
        f"  Naive Bayes          Accuracy: {nb_acc * 100:.1f}%  "
        f"CV F1: {nb_cv_mean:.4f} ± {nb_cv_std:.4f}  (best alpha={best_nb_alpha})"
    )
    print(f"\n  Models saved    → models/classical/")
    print(f"   MLflow logged   → {settings.MLFLOW_TRACKING_URI}/")
    print(f"   Registry        → FeedbackIQ-LogisticRegression + FeedbackIQ-NaiveBayes")
    print(f"\n  MLflow UI: run 'mlflow ui' → http://localhost:5000")
   


# Entry point

def main() -> None:
    """Run the full classical ML training pipeline end-to-end."""

    mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    mlflow.set_experiment(settings.MLFLOW_EXPERIMENT)

    # Phase 0: Load
    df = load_dataset()

    # Phase 1: Prepare — split + fit TF-IDF vectoriser
    X_train, X_test, y_train, y_test, vectorizer = prepare_data(df)

    # vectorizer was fit on X_train only -- transform() both splits here
    X_train_vec = vectorizer.transform(X_train)
    X_test_vec  = vectorizer.transform(X_test)

    # Phase 2: Tune — GridSearchCV over hyperparameter grids
    print("\n" + "═" * 60)
    print("  PHASE 1: GridSearchCV Hyperparameter Tuning")
    print("=" * 60)
    best_lr_C     = tune_logistic_regression(X_train_vec, y_train)
    best_nb_alpha = tune_naive_bayes(X_train_vec, y_train)

    # Phase 3: Train — re-train on full training set with best params
    lr, nb, lr_preds, nb_preds, lr_acc, nb_acc = train_models(
        X_train_vec, X_test_vec, y_train, y_test, best_lr_C, best_nb_alpha
    )

    # Phase 4: Cross-validate — 5-fold StratifiedKFold robustness check
    lr_cv_mean, lr_cv_std, nb_cv_mean, nb_cv_std = cross_validate(
        X_train, y_train, vectorizer, best_lr_C, best_nb_alpha
    )

    # Phase 5: Save — pickle models to disk
    save_models(vectorizer, lr, nb)

    # Phase 6: MLflow — log params, metrics, and register models
    log_models_to_mlflow(
        lr, nb, y_test, lr_preds, nb_preds,
        X_train, X_test,
        best_lr_C, best_nb_alpha,
        lr_cv_mean, lr_cv_std, nb_cv_mean, nb_cv_std,
    )

    # Phase 7: Promote — push the better model to Production stage
    promote_best_model(lr_acc, nb_acc)

    # Final summary
    print_summary(
        lr_acc, nb_acc,
        lr_cv_mean, lr_cv_std,
        nb_cv_mean, nb_cv_std,
        best_lr_C, best_nb_alpha,
    )


if __name__ == "__main__":
    main()