import os
import re
import threading

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from sentence_transformers import SentenceTransformer

_MODEL = None
_MODEL_NAME = None
_MODEL_LOCK = threading.Lock()


def get_model(model_name: str | None = None):
    global _MODEL, _MODEL_NAME
    requested_name = model_name or os.getenv(
        "EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    with _MODEL_LOCK:
        if _MODEL is None:
            _MODEL = SentenceTransformer(requested_name)
            _MODEL_NAME = requested_name
        elif requested_name != _MODEL_NAME:
            raise RuntimeError(
                f"Model {_MODEL_NAME!r} is already loaded; "
                f"cannot switch to {requested_name!r}"
            )
    return _MODEL


def token_count(text: str) -> int:
    """Returns exact tokenizer-measured token count for MiniLM-L6-v2."""
    if not text:
        return 0
    model = get_model()
    return len(model.tokenizer.encode(text, add_special_tokens=False))


def _chunk_plain_text(text: str, target: int, overlap: int, model) -> list[str]:
    tokens = model.tokenizer.encode(text, add_special_tokens=False)
    if len(tokens) <= target:
        return [text.strip()] if text.strip() else []

    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + target, len(tokens))
        value = model.tokenizer.decode(
            tokens[start:end], skip_special_tokens=True
        ).strip()
        if value:
            chunks.append(value)
        if end == len(tokens):
            break
        start = max(end - overlap, start + 1)
    return chunks


def chunk_by_tokens(
    text: str,
    target: int = 500,
    overlap: int = 80,
    model_name: str | None = None,
) -> list[str]:
    """
    Split prose into token windows while keeping fenced code blocks intact.

    A code block larger than the target remains a single chunk rather than
    being split into invalid code fragments.
    """
    if not text:
        return []

    if target < 1 or overlap < 0 or overlap >= target:
        raise ValueError("target must be positive and overlap must be smaller than target")

    model = get_model(model_name)
    pieces = []
    prose_lines = []
    code_lines = []
    fence_character = None
    fence_length = 0
    for line in text.splitlines(keepends=True):
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence_character is None:
            if fence:
                if prose_lines:
                    pieces.append(("prose", "".join(prose_lines)))
                    prose_lines = []
                marker = fence.group(1)
                fence_character = marker[0]
                fence_length = len(marker)
                code_lines = [line]
            else:
                prose_lines.append(line)
        else:
            code_lines.append(line)
            if (
                fence
                and fence.group(1)[0] == fence_character
                and len(fence.group(1)) >= fence_length
            ):
                pieces.append(("code", "".join(code_lines)))
                code_lines = []
                fence_character = None
                fence_length = 0
    if code_lines:
        pieces.append(("code", "".join(code_lines)))
    if prose_lines:
        pieces.append(("prose", "".join(prose_lines)))

    chunks = []
    for kind, piece in pieces:
        if not piece.strip():
            continue
        if kind == "code":
            chunks.append(piece.strip())
        else:
            chunks.extend(_chunk_plain_text(piece, target, overlap, model))
    return chunks


if __name__ == "__main__":
    sample = "The 401 Unauthorized status indicates that the request lacks valid authentication credentials."
    print("Token count:", token_count(sample))
    print("Chunks:", chunk_by_tokens(sample))
