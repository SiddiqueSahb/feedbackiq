"""
Settings for the Streamlit app.

The frontend is a pure HTTP client: it only needs to know where the API is and
which key to send. It deliberately does NOT import `feedbackiq.core.config`, so
its image stays light - no torch, no transformers, no faiss - and so the two
processes can be configured independently.

Values come from the environment first and then from a local `.env` file, which is
the same order as before Milestone 2, with the same defaults.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class FrontendSettings(BaseSettings):
    # Where the backend lives. docker-compose sets this to http://backend:8000.
    API_URL: str = "http://localhost:8000"

    # Sent as the `x-api-key` header on every call. Must match the backend's API_KEY,
    # or every request comes back 403. The development default matches the backend's.
    API_KEY: str = "dev-key-feedbackiq"

    # The first request after a cold start can take ~a minute while the backend loads
    # its index and models, hence the generous read timeout.
    API_GET_TIMEOUT: int = 90

    # No model loading sits behind /api/health, so it should answer quickly. The
    # sidebar calls it on every page render.
    API_HEALTH_TIMEOUT: int = 10

    # extra="ignore" because the shared .env also holds backend-only settings
    # such as GROQ_API_KEY, which the frontend has no business reading.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = FrontendSettings()
