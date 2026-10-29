import asyncio
import csv

import httpx

MOCK = "http://localhost:8001"


async def main():
    async with httpx.AsyncClient(timeout=25) as client:
        with open("data/test_api_scenarios.csv", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["target"] != "local_mock":
                    continue
                await client.post(f"{MOCK}/__reset")
                headers = {}
                raw = row["headers_redacted"]
                if raw and raw != "none":
                    key, _, value = raw.partition(": ")
                    headers[key] = "Bearer valid-user-token" if "REDACTED" in value else value
                body = row["body"] or None
                if body and body.strip().startswith("{"):
                    headers.setdefault("Content-Type", "application/json")
                try:
                    response = await client.request(
                        row["method"], row["url"], headers=headers, content=body, timeout=20
                    )
                    print(row["scenario_id"], response.status_code, "expected", row["expected_status"])
                except httpx.TimeoutException:
                    print(row["scenario_id"], "timeout", "expected", row["expected_status"])


if __name__ == "__main__":
    asyncio.run(main())
