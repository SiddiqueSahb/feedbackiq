# FeedbackIQ backend — FastAPI service.
#
# Build from the project root (data/ and models/ are intentionally left out
# of the image — see .dockerignore — and mounted as volumes at runtime
# instead, since they're multiple GB and change independently of the code):
#
#   docker build -f backend/Dockerfile -t feedbackiq-backend .
#
# Or just: docker compose up backend

FROM python:3.12-slim

WORKDIR /app

# libgomp1 is required at runtime by torch and faiss-cpu (OpenMP). git is
# needed by a couple of pip packages that install directly from source.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgomp1 \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies before copying the rest of the code, so this slow
# layer is only rebuilt when the requirements files actually change.
COPY requirements.txt ./requirements.txt
COPY backend/requirements-extra.txt ./backend/requirements-extra.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt -r backend/requirements-extra.txt \
    && pip install --no-cache-dir https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

# The application package, installed so `feedbackiq` is importable without any
# sys.path manipulation. --no-deps because every dependency was already installed
# from the pinned requirements files above; this step adds no versions of its own.
# pyproject.toml also marks /app as the project root, which is how core/paths.py
# resolves data/ and models/ (mounted as volumes at runtime).
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir -e . --no-deps

# Migrations travel with the code, so the running container can create or update its own
# schema (`alembic upgrade head`) instead of needing a checkout to do it. alembic.ini points
# at migrations/ and reads the database URL from the environment, never from the file.
COPY alembic.ini ./alembic.ini
COPY migrations/ ./migrations/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    OMP_NUM_THREADS=1 \
    KMP_DUPLICATE_LIB_OK=TRUE \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:${PORT}/api/health || exit 1

# Shell form on purpose: exec form ["uvicorn", ..., "8000"] can't expand
# $PORT, and platforms that assign the port at runtime — Cloud Run, App
# Runner, Heroku — inject it as an environment variable. The ${PORT:-8000}
# default keeps `docker run` and docker-compose behaving exactly as before.
#
# One worker deliberately. feedbackiq.rag.pipeline and feedbackiq.nlp.embedding_service both
# cache their loaded artefacts per process (lru_cache + a lock), so each
# extra uvicorn worker would load its own ~1.4GB copy of the FAISS index.
# Scale by running more containers, not more workers in one container.
CMD exec uvicorn feedbackiq.api.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
