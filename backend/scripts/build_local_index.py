import asyncio
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.ai.rag.batch_processor import batch_processor  # noqa: E402
from app.ai.rag.embedding import embedding  # noqa: E402


def load_chunks(path: Path) -> list[dict]:
    """Read the knowledge base.

    ``data/kb_chunks.jsonl`` is the single source of truth (the same file the
    backend auto-loads into Chroma/Supabase). A CSV path can still be passed
    explicitly for the curation workflow, but the committed JSONL is what the
    local index and the runtime retrievers must agree on.
    """
    rows: list[dict] = []
    if path.suffix == ".jsonl":
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    else:
        import pandas as pd

        rows = pd.read_csv(path).fillna("").to_dict("records")
    return rows


def main():
    # Default to the curated JSONL, not kb_seed.csv: the CSV has drifted
    # (candidate chunks) and rebuilding from it would overwrite the 88-chunk
    # knowledge base the backend seeds from.
    jsonl_path = DATA / "kb_chunks.jsonl"
    if not jsonl_path.exists():
        print(f"Error: {jsonl_path} not found.")
        sys.exit(1)

    rows = load_chunks(jsonl_path)
    print(f"Loading {len(rows)} chunks from {jsonl_path}...")
    print(
        f"Generating {embedding.dimensions}-dimensional embeddings "
        f"with {embedding.model_name}..."
    )

    vectors = np.asarray(
        asyncio.run(batch_processor.embed_texts([r["chunk_text"] for r in rows])),
        dtype=float,
    )
    if vectors.ndim != 2 or vectors.shape[1] != embedding.dimensions:
        raise SystemExit(
            f"Unexpected embedding shape {vectors.shape}; expected "
            f"(n, {embedding.dimensions})."
        )
    if vectors.shape[0] != len(rows):
        raise SystemExit(
            f"Got {vectors.shape[0]} embeddings for {len(rows)} chunks."
        )

    # Normalise so the NumPy retriever's dot product is cosine similarity.
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = vectors / norms

    # 1. Save NumPy embeddings matrix (kb_chunks.jsonl itself is the input and
    #    is never rewritten here).
    npy_path = DATA / "kb_embeddings.npy"
    np.save(npy_path, vectors)

    # 2. Save JSON metadata mirroring kb_chunks.jsonl row order.
    meta_path = DATA / "kb_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"SUCCESS: Built local index for {len(rows)} chunks.")
    print(f"  - Model: {embedding.model_name}")
    print(f"  - Vectors: {npy_path} (shape={vectors.shape})")
    print(f"  - Metadata: {meta_path}")


if __name__ == "__main__":
    main()
