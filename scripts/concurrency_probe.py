import argparse
import json
from pathlib import Path
from typing import Any

from oracle.check import collect_report
from oracle.common import ensure_artifact_parent
from oracle.reset import reset_database
from scripts.concurrency_support import RequestSpec, run_batch, summarize_results

DEFAULT_ARTIFACT = Path("artifacts/concurrency-latest.json")


def run_probe(
    *,
    base_url: str,
    stock: int,
    request_count: int,
    quantity: int,
    database_url: str | None = None,
    artifact_path: Path = DEFAULT_ARTIFACT,
) -> dict[str, Any]:
    reset_state = reset_database([stock], database_url=database_url)
    product = reset_state["products"][0]
    specs = [
        RequestSpec(
            payload={
                "product_id": product["id"],
                "quantity": quantity,
                "postal_code": product["postal_code"],
            }
        )
        for _ in range(request_count)
    ]
    results = run_batch(base_url=base_url, specs=specs)
    summary = summarize_results(results)
    expected_success = min(request_count, stock // quantity)
    expected_rejected = request_count - expected_success
    statuses = summary["status_counts"]
    oracle = collect_report(database_url)
    passed = (
        statuses.get("201", 0) == expected_success
        and statuses.get("409", 0) == expected_rejected
        and set(statuses).issubset({"201", "409"})
        and summary["connection_errors"] == 0
        and oracle["passed"]
    )
    result = {
        "passed": passed,
        "configuration": {
            "stock": stock,
            "requests": request_count,
            "quantity": quantity,
        },
        "expected": {"201": expected_success, "409": expected_rejected},
        "summary": summary,
        "oracle": oracle,
    }
    ensure_artifact_parent(artifact_path)
    artifact_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe concurrent order correctness.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--stock", type=int, default=10)
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    args = parser.parse_args()
    if args.stock < 0 or args.requests < 1 or args.quantity < 1:
        raise SystemExit("stock must be non-negative; requests and quantity must be positive")
    result = run_probe(
        base_url=args.base_url,
        stock=args.stock,
        request_count=args.requests,
        quantity=args.quantity,
        database_url=args.database_url,
        artifact_path=args.artifact,
    )
    print(json.dumps(result["summary"], indent=2))
    print("CONCURRENCY PASS" if result["passed"] else "CONCURRENCY FAIL")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
