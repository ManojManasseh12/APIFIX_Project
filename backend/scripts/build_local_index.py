import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app.ai.rag.embedding import embedding  # noqa: E402


def main():
    csv_path = DATA / "kb_seed.csv"
    if not csv_path.exists():
        print(f"Error: {csv_path} not found.")
        sys.exit(1)

    df = pd.read_csv(csv_path).fillna("")
    print(f"Loading {len(df)} chunks from {csv_path}...")
    print(
        f"Generating {embedding.dimensions}-dimensional embeddings "
        f"with {embedding.model_name}..."
    )

    vectors = np.asarray(
        asyncio.run(embedding.embed_texts(df["chunk_text"].tolist())),
        dtype=float,
    )
    if vectors.ndim != 2 or vectors.shape[1] != embedding.dimensions:
        raise SystemExit(
            f"Unexpected embedding shape {vectors.shape}; expected "
            f"(n, {embedding.dimensions})."
        )

    # Normalise so the NumPy retriever's dot product is cosine similarity.
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = vectors / norms

    # 1. Save NumPy embeddings matrix
    npy_path = DATA / "kb_embeddings.npy"
    np.save(npy_path, vectors)

    # 2. Save JSON metadata
    rows = df.to_dict("records")
    meta_path = DATA / "kb_meta.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)

    # 3. Export JSONL
    jsonl_path = DATA / "kb_chunks.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"SUCCESS: Built local index for {len(rows)} chunks.")
    print(f"  - Model: {embedding.model_name}")
    print(f"  - Vectors: {npy_path} (shape={vectors.shape})")
    print(f"  - Metadata: {meta_path}")
    print(f"  - JSONL Export: {jsonl_path}")


if __name__ == "__main__":
    main()
