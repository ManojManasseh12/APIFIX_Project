import json
import os
import sys
from pathlib import Path

import pandas as pd

# Offline safety
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from chunk_text import token_count

MAX_TOKENS = 256
REQUIRED_COLUMNS = [
    "chunk_id",
    "source_type",
    "source_url",
    "source_title",
    "license",
    "chunk_text",
]


def validate_dataset(csv_path: Path | None = None) -> bool:
    path = csv_path or (ROOT / "data" / "kb_seed.csv")
    if not path.exists():
        print(f"FAIL: File not found: {path}")
        return False

    df = pd.read_csv(path).fillna("")
    print(f"Auditing dataset: {path} ({len(df)} chunks)")

    errors = []
    warnings = []

    # Check required columns
    for col in REQUIRED_COLUMNS:
        if col not in df.columns:
            errors.append(f"Missing required column: {col}")

    # Check duplicate chunk IDs
    duplicates = df[df.duplicated(subset=["chunk_id"], keep=False)]["chunk_id"].unique()
    if len(duplicates) > 0:
        errors.append(f"Duplicate chunk IDs found: {list(duplicates)}")

    # Check each row
    for idx, row in df.iterrows():
        cid = row.get("chunk_id", f"row_{idx}")

        # Check empty fields
        for col in REQUIRED_COLUMNS:
            val = str(row.get(col, "")).strip()
            if not val:
                errors.append(f"[{cid}] Empty value for required field '{col}'")

        # Check URL format
        url = str(row.get("source_url", "")).strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            errors.append(f"[{cid}] Invalid source_url (must start with http:// or https://): '{url}'")

        # Measure tokens
        text = str(row.get("chunk_text", "")).strip()
        cnt = token_count(text)
        if cnt > MAX_TOKENS:
            errors.append(f"[{cid}] Token count exceeds MiniLM limit of {MAX_TOKENS}: {cnt} tokens")
        elif cnt < 30:
            warnings.append(f"[{cid}] Very short chunk text: {cnt} tokens")

    # Verify coverage of evaluation cases
    evidence_path = ROOT / "eval" / "evidence_map.json"
    if evidence_path.exists():
        with open(evidence_path, "r", encoding="utf-8") as f:
            evidence_map = json.load(f)

        existing_cids = set(df["chunk_id"].tolist())
        missing_cids = set()
        for case_id, exp_cids in evidence_map.items():
            for exp_cid in exp_cids:
                if exp_cid and exp_cid not in existing_cids:
                    missing_cids.add(exp_cid)

        if missing_cids:
            errors.append(f"Missing chunk IDs referenced by eval/evidence_map.json: {sorted(missing_cids)}")
        else:
            print(f"PASS: All chunk IDs in eval/evidence_map.json are present in the knowledge base.")

    # Report results
    print(f"\n--- Validation Summary ---")
    print(f"Total Chunks: {len(df)}")
    print(f"Total Errors: {len(errors)}")
    print(f"Total Warnings: {len(warnings)}")

    if warnings:
        print("\nWarnings:")
        for w in warnings[:10]:
            print("  *", w)

    if errors:
        print("\nErrors:")
        for e in errors:
            print("  [ERROR]", e)
        return False

    print("\nSUCCESS: Dataset passed all validation checks!")
    return True


if __name__ == "__main__":
    p = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    ok = validate_dataset(p)
    sys.exit(0 if ok else 1)

