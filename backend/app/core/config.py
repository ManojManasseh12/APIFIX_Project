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
    upload_dir: Path = BACKEND_DIR / "uploads"
    embed_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    max_upload_mb: int = Field(default=20, ge=1)

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

    database_url: str = ""
    supabase_url: str = ""
    supabase_service_key: str = ""

    mock_base_url: str = "http://localhost:8001"
    retrieval_backend: str = "local"
    allowed_hosts: str = "localhost:8001"
    frontend_origin: str = "http://localhost:5173"

    @property
    def allowed_host_set(self) -> set[str]:
        return {h.strip().lower() for h in self.allowed_hosts.split(",") if h.strip()}


settings = Settings()
