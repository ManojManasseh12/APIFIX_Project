import csv
import json
import os
import sys
from pathlib import Path

# Offline safety
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from chunk_text import token_count
from parse_openapi import parse_openapi

# Import base curated chunks
from assemble_dataset import CHUNKS


def build_full_dataset():
    output_path = ROOT / "data" / "kb_seed.csv"

    # 1. Base curated chunks
    all_chunks = [dict(c) for c in CHUNKS]
    existing_cids = {c["chunk_id"] for c in all_chunks}

    # 2. Parse Petstore OpenAPI
    petstore_path = ROOT / "data" / "raw" / "petstore-openapi.json"
    if petstore_path.exists():
        pet_chunks = parse_openapi(str(petstore_path), license_name="Apache-2.0")
        for pc in pet_chunks:
            if pc["chunk_id"] not in existing_cids:
                all_chunks.append(pc)
                existing_cids.add(pc["chunk_id"])

    # 3. Parse GitHub Issues OpenAPI
    github_path = ROOT / "data" / "raw" / "github-issues-openapi.json"
    if github_path.exists():
        gh_chunks = parse_openapi(str(github_path), license_name="MIT")
        for gc in gh_chunks:
            if gc["chunk_id"] not in existing_cids:
                all_chunks.append(gc)
                existing_cids.add(gc["chunk_id"])

    fieldnames = [
        "chunk_id",
        "source_type",
        "source_url",
        "source_title",
        "api_name",
        "api_version",
        "method",
        "path",
        "section",
        "page",
        "license",
        "retrieved_at",
        "token_count",
        "chunk_text",
    ]

    # Measure exact token counts for all chunks
    for c in all_chunks:
        c["token_count"] = token_count(c["chunk_text"])

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for c in all_chunks:
            writer.writerow(c)

    print(f"Compiled and saved {len(all_chunks)} complete citable chunks to {output_path}")


if __name__ == "__main__":
    build_full_dataset()

