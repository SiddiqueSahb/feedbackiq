from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Data paths
    DATA_PATH:             str = "data/processed/reviews_unified.parquet"
    RAW_DATA_PATH:         str = "data/raw"
    MODEL_PATH:            str = "models/distilbert-finetuned-final"
    INDEX_PATH:            str = "data/embeddings/reviews.faiss"
    EMBED_PATH:            str = "data/embeddings/review_embeddings.npy"
    LANGCHAIN_INDEX_PATH:  str = "data/embeddings/langchain_index"

    # MLflow (free, local) 
    MLFLOW_TRACKING_URI:  str = "mlruns"      # where MLflow saves experiment data
    MLFLOW_EXPERIMENT:    str = "Feedback_Sentiment"  # experiment name

    #Sentiment model 
    ROBERTA_MODEL: str = "cardiffnlp/twitter-roberta-base-sentiment-latest"

    # Logging
    LOG_LEVEL: str = "WARNING"

    # Embedding model configuration
    EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
    EMBEDDING_DIM: int = 384
    MAX_SEQ_LENGTH:   int = 128

    # Retrieval
    TOP_K_RETRIEVAL: int = 6

    # API
    API_HOST:  str = "0.0.0.0"
    API_PORT:  int = 8000
    API_URL:   str = "http://localhost:8000"

    # Required in x-api-key header on /api/* (see backend/api/deps.py)
    API_KEY:   str = "dev-key-feedbackiq"   # override in production .env

    # "*" for local dev; set to the deployed frontend URL in production
    ALLOWED_ORIGINS: str = "*"

    # No default keys — a missing .env entry should fail loudly, not fall back
    GOOGLE_API_KEY: str = ""
    GROQ_API_KEY:     str   = ""
    USE_GROQ:         bool  = True
    # openai/gpt-oss-20b fails with_structured_output() (tool_use_failed);
    # nlp/summariser._recover_from_failed_tool_call() works around it.
    # NOTE: dissertation results are for llama-3.1-8b-instant, not this model.
    GROQ_MODEL:       str   = "openai/gpt-oss-20b"
    LLM_TEMPERATURE:  float = 0.2

    #Zero-shot complaint categorisation
    ZEROSHOT_MODEL:          str   = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"  # primary — outperforms BART-large-mnli on zero-shot benchmarks, esp. with larger label sets
    ZEROSHOT_BASELINE_MODEL: str   = "facebook/bart-large-mnli"                     # kept for baseline comparison / ablation
    CATEGORY_SHORTLIST_K:    int   = 6      # embedding-similarity shortlist size fed to the NLI reranker
    CATEGORY_CONFIDENCE_THRESHOLD: float = 0.35  # below this, return "Unclassified / Emerging Complaint"



    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

