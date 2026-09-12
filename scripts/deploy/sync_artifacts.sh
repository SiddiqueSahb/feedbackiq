#!/usr/bin/env bash
#
# Push the artefacts the backend needs at serve time to a GCS bucket, or pull
# them back down onto a VM.
#
#   ./scripts/deploy/sync_artifacts.sh push gs://my-bucket
#   ./scripts/deploy/sync_artifacts.sh pull gs://my-bucket
#
# Why a bucket rather than `gcloud compute scp`: it's ~3GB, scp of that over a
# flaky connection restarts from zero, and `gcloud storage rsync` resumes and
# skips files that already match. It also means rebuilding the VM doesn't mean
# re-uploading from your laptop.
#
# Only serve-time artefacts are listed below. The raw corpora (~13GB), the
# BERTopic models (~3.9GB) and review_embeddings.npy (942MB) are used by the
# offline training and discovery scripts, never by the API, so they stay put.

set -euo pipefail

PATHS=(
  # RAG: LangChain's FAISS index plus its docstore pickle
  "data/embeddings/langchain_index"
  # Semantic search: the raw FAISS index
  "data/embeddings/reviews.faiss"
  # Analytics, search metadata, keyword fallback
  "data/processed/reviews_unified.parquet"
  # Zero-shot categoriser: the 24 category descriptions
  "data/processed/complaint_categories_all_negative.json"
  # Evaluation page: the dissertation result CSVs/JSONs (~6MB)
  "data/results"
  # Sentiment: the fine-tuned model
  "models/distilbert-finetuned-final"
  # Sentiment baselines: TF-IDF vectoriser + classical models
  "models/classical"
)

usage() {
  echo "usage: $0 {push|pull} gs://BUCKET" >&2
  exit 2
}

[[ $# -eq 2 ]] || usage
DIRECTION="$1"
BUCKET="${2%/}"
[[ "$BUCKET" == gs://* ]] || usage

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

command -v gcloud >/dev/null || { echo "gcloud not found. Install the Google Cloud CLI." >&2; exit 1; }

for path in "${PATHS[@]}"; do

  if [[ "$DIRECTION" == "push" ]]; then

    if [[ ! -e "$path" ]]; then
      echo "MISSING  $path — generate it before deploying, or the endpoint that needs it will fail." >&2
      continue
    fi

    echo "==> push $path"
    if [[ -d "$path" ]]; then
      gcloud storage rsync --recursive "$path" "$BUCKET/$path"
    else
      gcloud storage cp "$path" "$BUCKET/$path"
    fi

  elif [[ "$DIRECTION" == "pull" ]]; then

    echo "==> pull $path"
    mkdir -p "$(dirname "$path")"
    if [[ "$path" == *.* && "$path" != */ ]] && ! gcloud storage ls "$BUCKET/$path/" >/dev/null 2>&1; then
      gcloud storage cp "$BUCKET/$path" "$path"
    else
      mkdir -p "$path"
      gcloud storage rsync --recursive "$BUCKET/$path" "$path"
    fi

  else
    usage
  fi

done

echo
echo "Done. Serve-time payload is roughly 3.0GB."
