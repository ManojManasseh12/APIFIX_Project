import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


def main():
    df = pd.read_csv(DATA / "kb_seed.csv").fillna("")
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    vectors = model.encode(df["chunk_text"].tolist(), normalize_embeddings=True)
    np.save(DATA / "kb_embeddings.npy", vectors)
    rows = df.to_dict("records")
    json.dump(rows, open(DATA / "kb_meta.json", "w"), indent=2)
    print(f"local index: {len(rows)} chunks, shape={vectors.shape}")


if __name__ == "__main__":
    main()
