from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = ""
    groq_api_key: str = ""
    mistral_api_key: str = ""
    gemini_model: str = ""
    groq_model: str = ""
    mistral_model: str = ""

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
