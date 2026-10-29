# Backward-compatibility shim
from app.ai.llm import (  # noqa: F401
    GeminiProvider, GroqProvider, MistralProvider,
    Provider, get_providers, stream_with_fallback,
)
