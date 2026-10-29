import os
import sys
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
PIPELINE = "v1"


def token_count(model, text: str) -> int:
    return len(model.tokenizer.encode(text, add_special_tokens=False))


def main(csv_path: str | None = None):
    path = Path(csv_path) if csv_path else ROOT / "data" / "kb_seed.csv"
    df = pd.read_csv(path).fillna("")
    model = SentenceTransformer(MODEL_NAME)
    embeddings = model.encode(df["chunk_text"].tolist(), normalize_embeddings=True)

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            for row, embedding in zip(df.to_dict("records"), embeddings):
                cur.execute(
                    """
                    insert into kb_chunks (
                      chunk_id, source_type, source_url, source_title, api_name,
                      api_version, method, path, section, page, license, retrieved_at,
                      token_count, chunk_text, embedding, embedding_model,
                      embedding_dim, embedding_pipeline_version
                    ) values (
                      %(chunk_id)s, %(source_type)s, %(source_url)s, %(source_title)s,
                      %(api_name)s, %(api_version)s, %(method)s, %(path)s, %(section)s,
                      %(page)s, %(license)s, %(retrieved_at)s, %(token_count)s,
                      %(chunk_text)s, %(embedding)s, %(embedding_model)s, 384, %(pipeline)s
                    )
                    on conflict (chunk_id) do update set
                      chunk_text = excluded.chunk_text,
                      embedding = excluded.embedding,
                      token_count = excluded.token_count,
                      embedding_pipeline_version = excluded.embedding_pipeline_version
                    """,
                    {
                        **row,
                        "page": int(row["page"]) if str(row["page"]).isdigit() else None,
                        "token_count": token_count(model, row["chunk_text"]),
                        "embedding": embedding,
                        "embedding_model": MODEL_NAME,
                        "pipeline": PIPELINE,
                    },
                )
    print(f"upserted {len(df)} chunks")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
