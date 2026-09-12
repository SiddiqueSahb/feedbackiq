# Running FeedbackIQ

## Configuration

Copy `.env.example` to `.env` and fill it in. Two values matter:

- `GROQ_API_KEY` — needed by the chatbot and the LLM business summary.
- `API_KEY` — the shared secret every `/api/*` route requires in an
  `x-api-key` header (`/api/health` and `/` are deliberately open, so
  health checks work without credentials). Leave it blank locally and the
  development default in `src/feedbackiq/core/config.py` is used, with a warning on startup.

Set `ENVIRONMENT=production` and the backend **refuses to start** unless
`API_KEY` is a real value — a deployment quietly running on a key that's
published in source control is worse than one that fails loudly on boot.

The frontend reads the same `API_KEY` and sends it on every call, so if the
two services are configured separately they must agree or every request
comes back 403.

## One-off: precompute the dashboard keywords

```bash
python scripts/precompute_keywords.py
```

The keyword chart needs spaCy's dependency parser over every review — ~26
million words, tens of minutes. This runs it once and writes the counts to
`data/results/keywords/top_keywords.json`, which the API then serves
instantly. Until you run it the dashboard falls back to a 25,000-review
sample and labels the chart as sampled. Re-run it whenever
`reviews_unified.parquet` changes.

## Locally, without Docker

```bash
# Backend (from the project root)
uvicorn feedbackiq.api.main:app --reload --port 8000

# Frontend (separate terminal)
streamlit run frontend/app.py
```

The frontend reads `API_URL` from `frontend/app_settings.py` / the environment, defaulting to `http://localhost:8000`.

## With Docker

```bash
docker compose up --build
```

- Backend: http://localhost:8000 (interactive docs at `/docs`)
- Frontend: http://localhost:8501

`data/`, `models/`, `mlruns/` and `logs/` are mounted from the host, not copied into the images — they're multi-GB and already produced by the existing scripts, so there's no reason to bake them into a container image. Make sure they exist locally before starting (i.e. you've already run the preprocessing/training/index-building scripts).

The backend needs a Groq API key to answer chat questions and generate business summaries. Put `GROQ_API_KEY=...` in `.env` at the project root — `docker-compose.yml` loads that file into both containers automatically.

## Deploying to GCP

Both images take their port from `$PORT` (defaulting to 8000/8501), so they
run unchanged on any platform that assigns the port at runtime.

### What has to travel

`data/` and `models/` are gitignored, so cloning the repo on a server gets
you code and no artefacts. These seven paths — about 3.0 GB — are what the
API actually reads at serve time:

| Path | Size | Needed by |
|---|---|---|
| `data/embeddings/langchain_index/` | 1.35 GB | RAG |
| `data/embeddings/reviews.faiss` | 942 MB | semantic search |
| `data/processed/reviews_unified.parquet` | 407 MB | analytics, search metadata |
| `models/distilbert-finetuned-final/` | 256 MB | sentiment |
| `models/classical/` | 76 MB | TF-IDF + classical baselines |
| `data/results/` | 6 MB | evaluation page |
| `data/processed/complaint_categories_all_negative.json` | 32 KB | zero-shot categoriser |

The raw corpora (~13 GB), the BERTopic models (~3.9 GB) and
`review_embeddings.npy` (942 MB) belong to the offline training and
discovery scripts. They never ship.

`scripts/deploy/sync_artifacts.sh` moves exactly this list.

### Option A — Compute Engine (recommended)

The closest fit, because `docker-compose.yml` mounts `data/` and `models/`
from the host and a VM has a filesystem. No code changes at all.

```bash
# 0. Once, locally: generate the keyword file so the VM doesn't have to.
python scripts/precompute_keywords.py

# 1. Stage the artefacts in a bucket.
gcloud storage buckets create gs://feedbackiq-artifacts --location=europe-west2
./scripts/deploy/sync_artifacts.sh push gs://feedbackiq-artifacts

# 2. Create the VM. e2-standard-4 is 4 vCPU / 16GB — enough headroom for the
#    index, the parquet and the transformer models all resident at once.
gcloud compute instances create feedbackiq \
  --machine-type=e2-standard-4 \
  --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud \
  --boot-disk-size=50GB \
  --scopes=storage-ro \
  --tags=feedbackiq

# 3. Open only the frontend port. The frontend reaches the backend over the
#    compose network, so 8000 never needs to face the internet.
gcloud compute firewall-rules create feedbackiq-ui \
  --allow=tcp:8501 --target-tags=feedbackiq

# 4. On the VM:
gcloud compute ssh feedbackiq
```

```bash
# ---- from here on, on the VM ----
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git
sudo usermod -aG docker "$USER" && newgrp docker

git clone <your-repo-url> feedbackiq
cd feedbackiq
./scripts/deploy/sync_artifacts.sh pull gs://feedbackiq-artifacts

cat > .env <<'EOF'
ENVIRONMENT=production
GROQ_API_KEY=<your groq key>
API_KEY=<output of: python3 -c "import secrets; print(secrets.token_urlsafe(32))">
LOG_LEVEL=WARNING
EOF
chmod 600 .env

docker compose up -d --build
docker compose logs -f backend        # wait for "Warm-up complete"
```

The app is then at `http://<EXTERNAL_IP>:8501`.

Two things to be honest about with this setup. It's plain HTTP on a raw IP,
so put Caddy or a GCP load balancer in front before showing it to anyone who
isn't you. And `ENVIRONMENT=production` means the backend will refuse to
start if `API_KEY` is missing or still the development default — that's
deliberate, not a bug.

### Option B — Cloud Run

Worth it if you want scale-to-zero and no VM to maintain. No persistent
volumes, so mount the same paths from the bucket using Cloud Storage FUSE
volume mounts, and keep secrets in Secret Manager rather than a `.env`:

```bash
gcloud run deploy feedbackiq-backend \
  --source . --region=europe-west2 \
  --memory=8Gi --cpu=4 --timeout=300 --min-instances=1 \
  --add-volume=name=artifacts,type=cloud-storage,bucket=feedbackiq-artifacts \
  --add-volume-mount=volume=artifacts,mount-path=/app/data \
  --set-secrets=GROQ_API_KEY=groq-key:latest,API_KEY=feedbackiq-api-key:latest \
  --set-env-vars=ENVIRONMENT=production
```

The catch is cold start: the first request reads a 1.35 GB index over FUSE,
which is why `--min-instances=1` is there. Drop it and you get scale-to-zero
plus a first request that takes tens of seconds. Deploy the frontend the
same way with `--session-affinity` (Streamlit needs it for its websocket)
and point `API_URL` at the backend's URL.

### Applies either way

Keep uvicorn at one worker. `feedbackiq.rag.pipeline` and `feedbackiq.nlp.embedding_service`
cache their artefacts per process, so a second worker means a second 1.4 GB
copy of the index. Scale out with containers, not workers — the backend
`CMD` already pins `--workers 1`.

## What was added

- `src/feedbackiq/` — the installable application package: `api/` (FastAPI app with five route groups: sentiment, search, rag, analytics, evaluation), `services/`, `nlp/`, `rag/` and `core/` (settings, logging, paths).
- `frontend/` — Streamlit pages: Dashboard (already existed), Analyse, Search, Chatbot, Upload.
- `backend/Dockerfile`, `frontend/Dockerfile`, `docker-compose.yml` — images built from the project root; heavy data/model directories excluded via `.dockerignore` and mounted as volumes instead.
- `.github/workflows/ci.yml` — lints `backend/` and `frontend/` and builds both images on every push/PR.

## Where the roadmap items plug in

Nothing below is built yet — this is just where each piece would go, based on how the project is currently structured:

- **PostgreSQL** (the roadmap's Milestone 4): replace `_load_df()` in `src/feedbackiq/services/analytics_service.py` with SQL queries and add a `postgres` service to `docker-compose.yml`.
- **Live ingestion**: a new `src/feedbackiq/services/ingestion_service.py` writing into PostgreSQL instead of the parquet file, called on a schedule or webhook.
- **Retraining pipeline**: a script under `scripts/` that reruns `train_classical_models.py`/the fine-tuning notebook when new labelled data arrives, logged to the MLflow instance already configured in `src/feedbackiq/core/config.py`.
- **DVC**: `dvc init`, then track `data/` and `models/` with it instead of (or alongside) the current `.gitignore` exclusions.
- **Prometheus/Grafana**: add a `/metrics` endpoint to `src/feedbackiq/api/main.py` (e.g. via `prometheus-fastapi-instrumentator`) and `prometheus`/`grafana` services to `docker-compose.yml`.
