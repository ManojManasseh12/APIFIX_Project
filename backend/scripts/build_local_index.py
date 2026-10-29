import json
import os
import sys
from pathlib import Path

# Offline safety
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def main():
    csv_path = DATA / "kb_seed.csv"
    if not csv_path.exists():
        print(f"Error: {csv_path} not found.")
        sys.exit(1)

    df = pd.read_csv(csv_path).fillna("")
    print(f"Loading {len(df)} chunks from {csv_path}...")

    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    texts = df["chunk_text"].tolist()

    print("Generating dense 384-dimensional embeddings...")
    vectors = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)

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
    print(f"  - Vectors: {npy_path} (shape={vectors.shape})")
    print(f"  - Metadata: {meta_path}")
    print(f"  - JSONL Export: {jsonl_path}")


if __name__ == "__main__":
    main()
