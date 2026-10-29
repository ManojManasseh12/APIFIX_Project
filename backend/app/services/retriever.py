# Backward-compatibility shim
from app.rag.retriever import (  # noqa: F401
    LocalRetriever, RetrievalResult, SupabaseRetriever,
    detect_version, get_retriever, rrf,
)
