import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import httpx

from oracle.check import collect_report
from oracle.common import ensure_artifact_parent
from oracle.reset import reset_database
from scripts.concurrency_support import (
    RequestResult,
    RequestSpec,
    run_batch,
    summarize_results,
)

DEFAULT_ARTIFACT = Path("artifacts/baseline-validation-latest.json")


def status_counts_are(summary: dict[str, Any], *, successes: int, rejections: int) -> bool:
    counts = summary["status_counts"]
    return (
        counts.get("201", 0) == successes
        and counts.get("409", 0) == rejections
        and set(counts).issubset({"201", "409"})
        and summary["connection_errors"] == 0
    )


def run_round_zero(base_url: str, database_url: str | None) -> dict[str, Any]:
    reset_database([], database_url=database_url)
    checks: dict[str, bool] = {}
    with httpx.Client(base_url=base_url, timeout=15.0) as client:
        create_response = client.post(
            "/products",
            json={"name": "Round Zero Product", "unit_price": 25_000, "initial_stock": 3},
        )
        checks["product_create"] = create_response.status_code == 201
        if create_response.status_code != 201:
            return {"status": "FAIL", "checks": checks, "oracle": None}
        product = create_response.json()
        product_id = int(product["id"])
        checks["product_get"] = client.get(f"/products/{product_id}").status_code == 200
        normal_order = client.post(
            "/orders",
            json={"product_id": product_id, "quantity": 1, "postal_code": "16841"},
        )
        checks["normal_order"] = normal_order.status_code == 201
        insufficient = client.post(
            "/orders",
            json={"product_id": product_id, "quantity": 3, "postal_code": "16841"},
        )
        checks["insufficient_stock"] = (
            insufficient.status_code == 409
            and insufficient.json().get("code") == "INSUFFICIENT_STOCK"
        )
        missing = client.post(
            "/orders",
            json={"product_id": product_id + 999_999, "quantity": 1, "postal_code": "16841"},
        )
        checks["missing_product"] = (
            missing.status_code == 404 and missing.json().get("code") == "PRODUCT_NOT_FOUND"
        )
    oracle = collect_report(database_url)
    checks["oracle"] = bool(oracle["passed"])
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "oracle": oracle,
    }


def make_specs(
    *, product_id: int, quantity: int, postal_code: str, count: int, group: str
) -> list[RequestSpec]:
    return [
        RequestSpec(
            group=group,
            payload={
                "product_id": product_id,
                "quantity": quantity,
                "postal_code": postal_code,
            },
        )
        for _ in range(count)
    ]


def run_round_one(base_url: str, database_url: str | None, repeats: int) -> dict[str, Any]:
    repetitions: list[dict[str, Any]] = []
    passed = True
    for number in range(1, repeats + 1):
        state = reset_database([10], database_url=database_url)
        product = state["products"][0]
        results = run_batch(
            base_url=base_url,
            specs=make_specs(
                product_id=int(product["id"]),
                quantity=1,
                postal_code=str(product["postal_code"]),
                count=40,
                group="single_product",
            ),
        )
        summary = summarize_results(results)
        oracle = collect_report(database_url)
        product_check = oracle["products"][0] if oracle["products"] else {}
        iteration_passed = (
            status_counts_are(summary, successes=10, rejections=30)
            and oracle["passed"]
            and product_check.get("confirmed_quantity") == 10
            and product_check.get("current_stock") == 0
        )
        passed = passed and iteration_passed
        repetitions.append(
            {
                "iteration": number,
                "passed": iteration_passed,
                "summary": summary,
                "oracle": oracle,
            }
        )
    return {"status": "PASS" if passed else "FAIL", "repetitions": repetitions}


def interval_for(results: list[RequestResult]) -> tuple[float, float]:
    return min(item.started_at for item in results), max(item.ended_at for item in results)


def overlap_measure(
    a_results: list[RequestResult], b_results: list[RequestResult]
) -> dict[str, float]:
    a_start, a_end = interval_for(a_results)
    b_start, b_end = interval_for(b_results)
    overlap_seconds = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    shorter_seconds = max(min(a_end - a_start, b_end - b_start), 0.000_001)
    return {
        "milliseconds": round(overlap_seconds * 1_000, 3),
        "ratio_of_shorter_interval": round(overlap_seconds / shorter_seconds, 4),
    }


def measure_single_product(
    *,
    base_url: str,
    database_url: str | None,
    stock: int,
    quantity: int,
    request_count: int,
    group: str,
) -> dict[str, Any]:
    state = reset_database([stock], database_url=database_url)
    product = state["products"][0]
    results = run_batch(
        base_url=base_url,
        specs=make_specs(
            product_id=int(product["id"]),
            quantity=quantity,
            postal_code=str(product["postal_code"]),
            count=request_count,
            group=group,
        ),
    )
    summary = summarize_results(results)
    successes = stock // quantity
    oracle = collect_report(database_url)
    return {
        "passed": status_counts_are(
            summary, successes=successes, rejections=request_count - successes
        )
        and oracle["passed"],
        "summary": summary,
        "oracle": oracle,
    }


def measure_two_products(base_url: str, database_url: str | None) -> dict[str, Any]:
    state = reset_database([13, 18], database_url=database_url)
    product_a, product_b = state["products"]
    specs = make_specs(
        product_id=int(product_a["id"]),
        quantity=1,
        postal_code=str(product_a["postal_code"]),
        count=30,
        group="A",
    )
    specs.extend(
        make_specs(
            product_id=int(product_b["id"]),
            quantity=2,
            postal_code=str(product_b["postal_code"]),
            count=20,
            group="B",
        )
    )
    results = run_batch(base_url=base_url, specs=specs)
    a_results = [item for item in results if item.group == "A"]
    b_results = [item for item in results if item.group == "B"]
    a_summary = summarize_results(a_results)
    b_summary = summarize_results(b_results)
    combined_summary = summarize_results(results)
    oracle = collect_report(database_url)
    passed = (
        status_counts_are(a_summary, successes=13, rejections=17)
        and status_counts_are(b_summary, successes=9, rejections=11)
        and oracle["passed"]
    )
    return {
        "passed": passed,
        "product_a": a_summary,
        "product_b": b_summary,
        "combined": combined_summary,
        "overlap": overlap_measure(a_results, b_results),
        "oracle": oracle,
    }


def run_round_two(base_url: str, database_url: str | None, repeats: int) -> dict[str, Any]:
    measurements: list[dict[str, Any]] = []
    consistency_passed = True
    for number in range(1, repeats + 1):
        single_a = measure_single_product(
            base_url=base_url,
            database_url=database_url,
            stock=13,
            quantity=1,
            request_count=30,
            group="A",
        )
        single_b = measure_single_product(
            base_url=base_url,
            database_url=database_url,
            stock=18,
            quantity=2,
            request_count=20,
            group="B",
        )
        combined = measure_two_products(base_url, database_url)
        measurement_passed = single_a["passed"] and single_b["passed"] and combined["passed"]
        consistency_passed = consistency_passed and measurement_passed
        measurements.append(
            {
                "iteration": number,
                "passed": measurement_passed,
                "single_a": single_a,
                "single_b": single_b,
                "combined": combined,
            }
        )

    performance: dict[str, Any]
    if not consistency_passed:
        status = "FAIL"
        performance = {"classification": "not_evaluated_due_to_consistency_failure"}
    else:
        single_a_median = statistics.median(
            item["single_a"]["summary"]["duration_ms"] for item in measurements
        )
        single_b_median = statistics.median(
            item["single_b"]["summary"]["duration_ms"] for item in measurements
        )
        combined_median = statistics.median(
            item["combined"]["combined"]["duration_ms"] for item in measurements
        )
        overlap_median = statistics.median(
            item["combined"]["overlap"]["ratio_of_shorter_interval"] for item in measurements
        )
        sequential_baseline = max(single_a_median + single_b_median, 0.001)
        combined_to_sequential_ratio = combined_median / sequential_baseline
        iteration_ratios = [
            (
                item["combined"]["combined"]["duration_ms"]
                / max(
                    item["single_a"]["summary"]["duration_ms"]
                    + item["single_b"]["summary"]["duration_ms"],
                    0.001,
                )
            )
            for item in measurements
        ]
        ratio_median = statistics.median(iteration_ratios)
        ratio_mad = statistics.median(abs(value - ratio_median) for value in iteration_ratios)
        relative_mad = ratio_mad / max(ratio_median, 0.001)
        ratio_range = max(iteration_ratios) - min(iteration_ratios)
        timing_noise_high = relative_mad > 0.10 or ratio_range > 0.35

        if ratio_median <= 0.92 and overlap_median > 0:
            status = "PASS"
        elif timing_noise_high:
            status = "INCONCLUSIVE"
        elif ratio_median >= 1.08:
            status = "GLOBAL_LOCK_SUSPECTED"
        else:
            status = "INCONCLUSIVE"
        performance = {
            "classification": status,
            "single_a_duration_median_ms": round(single_a_median, 3),
            "single_b_duration_median_ms": round(single_b_median, 3),
            "combined_duration_median_ms": round(combined_median, 3),
            "combined_to_sequential_ratio": round(combined_to_sequential_ratio, 4),
            "iteration_ratio_median": round(ratio_median, 4),
            "iteration_ratios": [round(value, 4) for value in iteration_ratios],
            "iteration_ratio_mad": round(ratio_mad, 4),
            "iteration_ratio_relative_mad": round(relative_mad, 4),
            "iteration_ratio_range": round(ratio_range, 4),
            "timing_noise_high": timing_noise_high,
            "request_interval_overlap_median": round(overlap_median, 4),
        }
    return {"status": status, "performance": performance, "measurements": measurements}


def verify_baseline(
    *,
    base_url: str,
    database_url: str | None,
    round_one_repeats: int,
    round_two_repeats: int,
    artifact_path: Path,
) -> dict[str, Any]:
    round_zero = run_round_zero(base_url, database_url)
    round_one = run_round_one(base_url, database_url, round_one_repeats)
    round_two = run_round_two(base_url, database_url, round_two_repeats)
    passed = (
        round_zero["status"] == "PASS"
        and round_one["status"] == "PASS"
        and round_two["status"] in {"PASS", "INCONCLUSIVE"}
    )
    result = {
        "overall_status": "PASS" if passed else "FAIL",
        "round_0": round_zero,
        "round_1": round_one,
        "round_2": round_two,
    }
    ensure_artifact_parent(artifact_path)
    artifact_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the complete L1 commerce baseline.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--round1-repeats", type=int, default=20)
    parser.add_argument("--round2-repeats", type=int, default=5)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    args = parser.parse_args()
    if args.round1_repeats < 20:
        raise SystemExit("Round 1 requires at least 20 repetitions")
    if args.round2_repeats < 5:
        raise SystemExit("Round 2 requires at least 5 repetitions")
    result = verify_baseline(
        base_url=args.base_url,
        database_url=args.database_url,
        round_one_repeats=args.round1_repeats,
        round_two_repeats=args.round2_repeats,
        artifact_path=args.artifact,
    )
    print(f"Round 0: {result['round_0']['status']}")
    print(f"Round 1: {result['round_1']['status']}")
    print(f"Round 2: {result['round_2']['status']}")
    print(f"BASELINE: {result['overall_status']}")
    return 0 if result["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
