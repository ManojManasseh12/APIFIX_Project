from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]

# BaseSettings reads .env for typed settings, but provider adapters read
# os.environ directly. Load the same file into the process environment too,
# otherwise keys added to .env are invisible to the LLM providers.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    chroma_dir: Path = BACKEND_DIR / "data" / "chroma"
    runs_db: Path = BACKEND_DIR.parent / "data" / "runs.sqlite3"
    upload_dir: Path = BACKEND_DIR / "uploads"
    seed_kb: bool = True
    seed_kb_file: Path = BACKEND_DIR.parent / "data" / "kb_chunks.jsonl"
    # Gemini embedding model (same family as cloud_llm). The local tokenizer
    # below is used only for chunk sizing, never for embedding.
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = Field(default=768, ge=1)
    chunk_tokenizer_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    max_upload_mb: int = Field(default=20, ge=1)

    # Ingestion batching (same defaults as cloud_llm).
    batch_file_size: int = Field(default=10, ge=1)
    batch_max_concurrency: int = Field(default=4, ge=1)
    embed_max_chunks_per_request: int = Field(default=64, ge=1)
    embed_max_tokens_per_request: int = Field(default=8000, ge=1)
    embed_requests_per_minute: int = Field(default=60, ge=1)
    embed_tokens_per_minute: int = Field(default=100_000, ge=1)
    embed_max_retries: int = Field(default=3, ge=0)
    embed_retry_base_delay_s: float = Field(default=1.0, ge=0)
    embed_retry_max_delay_s: float = Field(default=8.0, ge=0)

    gemini_api_key: str = ""
    groq_api_key: str = ""
    mistral_api_key: str = ""
    gemini_api_keys: str = ""
    groq_api_keys: str = ""
    mistral_api_keys: str = ""
    gemini_model: str = ""
    groq_model: str = ""
    mistral_model: str = ""
    provider_order: str = "gemini,groq,mistral"
    llm_first_token_timeout_s: float = 20.0
    llm_timeout_s: float = 120.0
    # Retry the same model/key with exponential backoff before moving on.
    llm_max_attempts_per_provider: int = Field(default=3, ge=1)
    llm_retry_base_delay_s: float = Field(default=1.0, ge=0)
    llm_retry_max_delay_s: float = Field(default=8.0, ge=0)

    database_url: str = ""

    # Hybrid retrieval: weighted Reciprocal Rank Fusion. Dense results are the
    # stronger ranker on this corpus, so keyword contributes a small bonus and
    # the top dense_anchor results keep their order.
    rrf_k: int = Field(default=60, ge=1)
    rrf_dense_weight: float = Field(default=1.0, ge=0)
    rrf_keyword_weight: float = Field(default=0.05, ge=0)
    rrf_dense_anchor: int = Field(default=2, ge=0)

    mock_base_url: str = "http://localhost:8001"
    retrieval_backend: str = "local"
    allowed_hosts: str = "localhost:8001"
    frontend_origin: str = "http://localhost:5173"

    @property
    def database_url_configured(self) -> bool:
        """True only when DATABASE_URL is set to something real.

        Template values from .env.example (``<ref>``, ``<password>``, ...) must
        not trigger a connection attempt or a startup warning.
        """
        url = self.database_url.strip()
        if not url:
            return False
        placeholders = ("<ref>", "<password>", "<region>", "YOUR-", "[YOUR-")
        return not any(marker in url for marker in placeholders)

    @property
    def allowed_host_set(self) -> set[str]:
        return {h.strip().lower() for h in self.allowed_hosts.split(",") if h.strip()}


settings = Settings()
