import asyncio
import sys
from pathlib import Path

import pandas as pd
import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
load_dotenv(ROOT / ".env")

from app.ai.rag.batch_processor import batch_processor  # noqa: E402
from app.ai.rag.embedding import embedding  # noqa: E402
from scripts.chunk_text import token_count  # noqa: E402

PIPELINE = "v1"


def main(csv_path: str | None = None):
    import os

    path = Path(csv_path) if csv_path else ROOT / "data" / "kb_seed.csv"
    df = pd.read_csv(path).fillna("")
    texts = df["chunk_text"].tolist()

    print(
        f"Embedding {len(texts)} chunks with {embedding.model_name} "
        f"({embedding.dimensions} dims)..."
    )
    embeddings = asyncio.run(batch_processor.embed_texts(texts))

    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            for row, vector in zip(df.to_dict("records"), embeddings):
                cur.execute(
                    """
                    insert into kb_chunks (
                      chunk_id, source_type, source_url, source_title, api_name,
                      api_version, method, path, section, page, license, retrieved_at,
                      token_count, chunk_text, embedding, embedding_model,
                      embedding_dim, embedding_pipeline_version, source_file
                    ) values (
                      %(chunk_id)s, %(source_type)s, %(source_url)s, %(source_title)s,
                      %(api_name)s, %(api_version)s, %(method)s, %(path)s, %(section)s,
                      %(page)s, %(license)s, %(retrieved_at)s, %(token_count)s,
                      %(chunk_text)s, %(embedding)s, %(embedding_model)s,
                      %(embedding_dim)s, %(pipeline)s, %(source_file)s
                    )
                    on conflict (chunk_id) do update set
                      source_type = excluded.source_type,
                      source_url = excluded.source_url,
                      source_title = excluded.source_title,
                      api_name = excluded.api_name,
                      api_version = excluded.api_version,
                      method = excluded.method,
                      path = excluded.path,
                      section = excluded.section,
                      page = excluded.page,
                      license = excluded.license,
                      retrieved_at = excluded.retrieved_at,
                      chunk_text = excluded.chunk_text,
                      embedding = excluded.embedding,
                      token_count = excluded.token_count,
                      embedding_model = excluded.embedding_model,
                      embedding_dim = excluded.embedding_dim,
                      embedding_pipeline_version = excluded.embedding_pipeline_version,
                      source_file = excluded.source_file
                    """,
                    {
                        **row,
                        "page": int(row["page"]) if str(row["page"]).isdigit() else None,
                        "token_count": token_count(row["chunk_text"]),
                        "embedding": vector,
                        "embedding_model": embedding.model_name,
                        "embedding_dim": len(vector),
                        "pipeline": PIPELINE,
                        "source_file": row.get("source_file") or path.name,
                    },
                )
    print(f"upserted {len(df)} chunks")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
