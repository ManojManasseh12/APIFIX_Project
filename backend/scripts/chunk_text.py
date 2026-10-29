import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from sentence_transformers import SentenceTransformer

_MODEL = None


def get_model():
    global _MODEL
    if _MODEL is None:
        _MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _MODEL


def token_count(text: str) -> int:
    """Returns exact tokenizer-measured token count for MiniLM-L6-v2."""
    if not text:
        return 0
    model = get_model()
    return len(model.tokenizer.encode(text, add_special_tokens=False))


def chunk_by_tokens(text: str, target: int = 200, overlap: int = 35) -> list[str]:
    """
    Splits text into chunks of roughly `target` tokens with `overlap` tokens.
    Guarantees that no chunk exceeds MiniLM's 256-token context window.
    """
    if not text:
        return []

    model = get_model()
    tokens = model.tokenizer.encode(text, add_special_tokens=False)

    if len(tokens) <= target:
        return [text]

    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + target, len(tokens))
        chunk_text = model.tokenizer.decode(tokens[start:end], skip_special_tokens=True).strip()
        if chunk_text:
            chunks.append(chunk_text)
        if end == len(tokens):
            break
        start = max(end - overlap, start + 1)

    return chunks


if __name__ == "__main__":
    sample = "The 401 Unauthorized status indicates that the request lacks valid authentication credentials."
    print("Token count:", token_count(sample))
    print("Chunks:", chunk_by_tokens(sample))
