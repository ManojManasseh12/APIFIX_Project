import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.models import RequestSpec  # noqa: E402
from app.core.tester import execute  # noqa: E402

MOCK = "http://localhost:8001"
SCENARIOS = ROOT / "data" / "test_api_scenarios.csv"
REDACTED_TOKEN = "valid-user-token"


async def apply_setup(client: httpx.AsyncClient, raw: str | None) -> None:
    for item in json.loads(raw or "[]"):
        response = await client.post(f"{MOCK}/__control", json=item)
        response.raise_for_status()


def actual_result(observation) -> str:
    if observation.error_type:
        return observation.error_type
    return str(observation.status) if observation.status is not None else "none"


async def main(network: bool = False) -> int:
    failures = 0
    passed = 0
    skipped = 0

    async with httpx.AsyncClient(timeout=10) as client:
        with SCENARIOS.open(newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                if row["target"] != "local_mock" and not network:
                    skipped += 1
                    print(f"{row['scenario_id']} skipped (external; use --network)")
                    continue

                await client.post(f"{MOCK}/__reset")
                await apply_setup(client, row.get("mock_setup"))

                headers: dict[str, str] = {}
                raw_headers = row.get("headers_redacted") or ""
                if raw_headers and raw_headers != "none":
                    key, _, value = raw_headers.partition(": ")
                    headers[key] = value.replace("<REDACTED>", REDACTED_TOKEN)

                body = row.get("body") or None
                if body and body.lstrip().startswith(("{", "[")):
                    body = json.loads(body)
                    headers.setdefault("Content-Type", "application/json")

                spec = RequestSpec(
                    method=row["method"],
                    url=row["url"],
                    headers=headers,
                    body=body,
                    timeout_s=float(row.get("timeout_s") or 10),
                    approved=True,
                )
                observation = await execute(spec)
                actual = actual_result(observation)
                expected = row["expected_status"]
                ok = actual == expected
                if ok:
                    passed += 1
                else:
                    failures += 1
                print(
                    f"{row['scenario_id']} {actual:>16} expected {expected:<16} "
                    f"{'OK' if ok else 'FAIL'}"
                )
                if not ok and observation.error_message:
                    print(f"  {observation.error_message}")

    print(f"\n{passed} passed, {failures} failed, {skipped} skipped")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run mock API scenarios.")
    parser.add_argument(
        "--network",
        action="store_true",
        help="also run the external API checks",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(network=args.network)))
