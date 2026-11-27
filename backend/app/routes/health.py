from fastapi import APIRouter

from app.ai.llm import get_providers
from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """Real stack facts, used by the frontend status pills."""
    families: list[str] = []
    try:
        for provider in get_providers():
            family = provider.label.split(":", 1)[0]
            if family not in families:
                families.append(family)
    except Exception:  # never let status reporting break health
        families = []
    return {
        "ok": True,
        "service": "apifix-ai",
        "runner": "httpx",
        "retrieval_backend": settings.retrieval_backend,
        "diagnose_engine": (
            "langgraph" if settings.use_langgraph_diagnose else "legacy"
        ),
        "database": "supabase" if settings.database_url_configured else "sqlite",
        "providers": families,
        "langsmith": {
            "enabled": settings.langsmith_enabled,
            "project": settings.langsmith_project if settings.langsmith_enabled else None,
        },
    }
