"""Token-accurate chunking with the MiniLM-L6-v2 wordpiece tokenizer.

Only the tokenizer is needed here (token counts + windows) — never the torch
model. Importing sentence-transformers costs ~800 MB of RSS (torch + CUDA
wheels) and OOM-kills small hosts like the Render free tier (512 MB). The
`tokenizers` Rust wheel loads the same tokenizer.json in ~5 MB and produces
identical ids, so chunk boundaries are unchanged.
"""

import os
import re
import threading

from tokenizers import Tokenizer

_TOKENIZERS: dict[str, "_Model"] = {}
_MODEL_LOCK = threading.Lock()


class _Wordpiece:
    """The `.tokenizer` surface callers expect: text -> ids, ids -> text."""

    def __init__(self, tokenizer: Tokenizer):
        self._tokenizer = tokenizer

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        return self._tokenizer.encode(text, add_special_tokens=add_special_tokens).ids

    def decode(self, tokens: list[int], skip_special_tokens: bool = True) -> str:
        return self._tokenizer.decode(tokens, skip_special_tokens=skip_special_tokens)


class _Model:
    """Duck-type of the old SentenceTransformer: only `.tokenizer` is used."""

    def __init__(self, tokenizer: Tokenizer):
        self.tokenizer = _Wordpiece(tokenizer)


def get_model(model_name: str | None = None) -> _Model:
    """The tokenizer for the chunking model (not the full torch model).

    Cached by name; the download only happens when the HF hub cache does not
    already hold the tokenizer files.
    """
    requested_name = model_name or os.getenv(
        "CHUNK_TOKENIZER_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
    )
    with _MODEL_LOCK:
        model = _TOKENIZERS.get(requested_name)
        if model is None:
            tokenizer = Tokenizer.from_pretrained(requested_name)
            # tokenizer.json ships with truncation + padding to 128 tokens for
            # model inference; chunking needs the raw token stream.
            tokenizer.no_truncation()
            tokenizer.no_padding()
            model = _Model(tokenizer)
            _TOKENIZERS[requested_name] = model
    return model


def token_count(text: str) -> int:
    """Returns exact tokenizer-measured token count for MiniLM-L6-v2."""
    if not text:
        return 0
    return len(get_model().tokenizer.encode(text))


def _chunk_plain_text(text: str, target: int, overlap: int, model) -> list[str]:
    tokens = model.tokenizer.encode(text)
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
