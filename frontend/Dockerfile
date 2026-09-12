# FeedbackIQ frontend — Streamlit UI.
#
# Build from the project root:
#   docker build -f frontend/Dockerfile -t feedbackiq-frontend .
#
# Or just: docker compose up frontend
#
# This image is deliberately light — no torch/faiss/transformers — since
# the frontend only calls the backend over HTTP (see API_URL below).

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY frontend/requirements.txt ./frontend/requirements.txt

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r frontend/requirements.txt

# The Streamlit app only needs itself: it reads its configuration from
# frontend/app_settings.py and talks to the backend over HTTP, so it never imports
# the feedbackiq package (and therefore never pulls in torch).
COPY frontend/ ./frontend/
COPY .streamlit/ ./.streamlit/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    API_URL=http://backend:8000 \
    PORT=8501

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:${PORT}/_stcore/health || exit 1

# Shell form so $PORT expands — see the same note in backend/Dockerfile.
#
# --server.headless skips the "enter your email" prompt Streamlit shows on
# first run, which otherwise blocks startup in a container. XSRF protection
# is disabled because it depends on cookies that don't survive a proxy
# terminating TLS in front of the app; there are no state-changing forms
# here, every action goes to the backend over HTTP with an API key.
CMD exec streamlit run frontend/app.py \
    --server.port=${PORT:-8501} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableXsrfProtection=false
