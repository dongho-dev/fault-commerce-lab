"""Closed-loop storefront load test with stepped virtual users and SLO evaluation.

Each virtual user repeatedly performs a weighted storefront action (browse, search, product
detail, order) and waits a short think time. Every stage holds a fixed number of virtual users
for a fixed duration; the report shows throughput, latency percentiles, errors, and whether the
stage met the service level objective.
"""

import argparse
import asyncio
import json
import random
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from scripts.concurrency_support import percentile

DEFAULT_ARTIFACT_DIR = Path("artifacts")
LOAD_PRODUCT_PREFIX = "부하테스트 전용 상품"
SEARCH_TERMS = ("키보드", "커피", "세트", "무선", "매트", "크림", "감귤", "스테인리스", "없는상품")
CATEGORIES = ("digital", "home", "kitchen", "food", "beauty", "sports")
ACTION_WEIGHTS = {"browse": 30, "search": 15, "detail": 35, "order": 20}


@dataclass(frozen=True)
class Slo:
    p95_ms: float = 300.0
    p99_ms: float = 1_000.0
    error_rate: float = 0.01


@dataclass
class Sample:
    action: str
    status: int | None
    elapsed_ms: float
    error: str | None = None

    @property
    def failed(self) -> bool:
        # 409 INSUFFICIENT_STOCK is a correct business outcome, not a failure.
        return self.status is None or self.status >= 500 or (
            self.status >= 400 and self.status != 409
        )


@dataclass
class Targets:
    catalog_ids: list[int]
    order_ids: list[int]
    soldout_id: int


@dataclass
class StageReport:
    users: int
    seconds: float
    requests: int
    rps: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float
    error_rate: float
    status_counts: dict[str, int]
    errors: dict[str, int]
    by_action: dict[str, dict[str, float]] = field(default_factory=dict)
    slo_pass: bool = False


async def prepare_targets(client: httpx.AsyncClient, order_products: int) -> Targets:
    listing = await client.get("/products", params={"limit": 100})
    listing.raise_for_status()
    catalog_ids = [item["id"] for item in listing.json()["items"]]
    if not catalog_ids:
        raise SystemExit("catalog is empty: run `make seed` before the load test")

    async def create(name: str, stock: int) -> int:
        response = await client.post(
            "/products",
            json={
                "name": name,
                "unit_price": 10_000,
                "initial_stock": stock,
                "category": "etc",
                "description": "부하 테스트가 만든 상품입니다.",
            },
        )
        response.raise_for_status()
        return int(response.json()["id"])

    stamp = datetime.now(UTC).strftime("%H%M%S")
    order_ids = [
        await create(f"{LOAD_PRODUCT_PREFIX} {stamp}-{index:02d}", 1_000_000)
        for index in range(1, order_products + 1)
    ]
    soldout_id = await create(f"{LOAD_PRODUCT_PREFIX} {stamp}-품절", 0)
    return Targets(catalog_ids=catalog_ids, order_ids=order_ids, soldout_id=soldout_id)


async def perform(
    client: httpx.AsyncClient, rng: random.Random, targets: Targets, soldout_ratio: float
) -> Sample:
    action = rng.choices(list(ACTION_WEIGHTS), weights=list(ACTION_WEIGHTS.values()))[0]
    started = time.perf_counter()
    try:
        if action == "browse":
            params: dict[str, Any] = {"category": rng.choice(CATEGORIES), "limit": 20}
            if rng.random() < 0.3:
                params = {"sort": "discount", "limit": 10}
            response = await client.get("/products", params=params)
        elif action == "search":
            response = await client.get(
                "/products", params={"q": rng.choice(SEARCH_TERMS), "limit": 20}
            )
        elif action == "detail":
            response = await client.get(f"/products/{rng.choice(targets.catalog_ids)}")
        else:
            product_id = (
                targets.soldout_id
                if rng.random() < soldout_ratio
                else rng.choice(targets.order_ids)
            )
            response = await client.post(
                "/orders",
                json={"product_id": product_id, "quantity": 1, "postal_code": "06236"},
            )
        return Sample(action, response.status_code, (time.perf_counter() - started) * 1_000)
    except httpx.HTTPError as exc:
        return Sample(
            action, None, (time.perf_counter() - started) * 1_000, type(exc).__name__
        )


async def virtual_user(
    client: httpx.AsyncClient,
    seed: int,
    targets: Targets,
    deadline: float,
    think_ms: float,
    soldout_ratio: float,
    sink: list[Sample],
) -> None:
    rng = random.Random(seed)
    while time.perf_counter() < deadline:
        sink.append(await perform(client, rng, targets, soldout_ratio))
        if think_ms:
            await asyncio.sleep(rng.uniform(0.5, 1.5) * think_ms / 1_000)


def summarize(users: int, seconds: float, samples: list[Sample], slo: Slo) -> StageReport:
    latencies = [sample.elapsed_ms for sample in samples]
    failures = [sample for sample in samples if sample.failed]
    grouped: dict[str, list[float]] = defaultdict(list)
    for sample in samples:
        grouped[sample.action].append(sample.elapsed_ms)
    report = StageReport(
        users=users,
        seconds=round(seconds, 2),
        requests=len(samples),
        rps=round(len(samples) / seconds, 1) if seconds else 0.0,
        p50_ms=round(percentile(latencies, 50), 1),
        p95_ms=round(percentile(latencies, 95), 1),
        p99_ms=round(percentile(latencies, 99), 1),
        max_ms=round(max(latencies, default=0.0), 1),
        error_rate=round(len(failures) / len(samples), 4) if samples else 1.0,
        status_counts=dict(
            sorted(Counter(str(sample.status or "ERR") for sample in samples).items())
        ),
        errors=dict(Counter(sample.error or str(sample.status) for sample in failures)),
        by_action={
            action: {
                "count": len(values),
                "p50_ms": round(percentile(values, 50), 1),
                "p95_ms": round(percentile(values, 95), 1),
            }
            for action, values in sorted(grouped.items())
        },
    )
    report.slo_pass = bool(samples) and (
        report.p95_ms <= slo.p95_ms
        and report.p99_ms <= slo.p99_ms
        and report.error_rate < slo.error_rate
    )
    return report


async def run_stage(
    client: httpx.AsyncClient,
    targets: Targets,
    users: int,
    seconds: float,
    think_ms: float,
    soldout_ratio: float,
    seed: int,
) -> tuple[list[Sample], float]:
    samples: list[Sample] = []
    started = time.perf_counter()
    deadline = started + seconds
    await asyncio.gather(
        *(
            virtual_user(client, seed + index, targets, deadline, think_ms, soldout_ratio, samples)
            for index in range(users)
        )
    )
    return samples, time.perf_counter() - started


async def run_load_test(args: argparse.Namespace) -> dict[str, Any]:
    slo = Slo(p95_ms=args.slo_p95, p99_ms=args.slo_p99, error_rate=args.slo_error_rate)
    stages = [int(value) for value in args.stages.split(",") if value.strip()]
    limits = httpx.Limits(max_connections=max(stages) + 10, max_keepalive_connections=max(stages))
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=args.timeout, limits=limits
    ) as client:
        targets = await prepare_targets(client, args.order_products)
        if args.warmup:
            await run_stage(
                client, targets, min(stages), args.warmup, args.think_ms, args.soldout_ratio, 1
            )
        reports: list[StageReport] = []
        for index, users in enumerate(stages):
            samples, elapsed = await run_stage(
                client,
                targets,
                users,
                args.stage_seconds,
                args.think_ms,
                args.soldout_ratio,
                seed=10_000 * (index + 1),
            )
            report = summarize(users, elapsed, samples, slo)
            reports.append(report)
            print_stage(report)
            if args.stop_on_fail and not report.slo_pass:
                break

    passing = [report.users for report in reports if report.slo_pass]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "config": {
            "stages": stages,
            "stage_seconds": args.stage_seconds,
            "think_ms": args.think_ms,
            "soldout_ratio": args.soldout_ratio,
            "action_weights": ACTION_WEIGHTS,
            "order_products": args.order_products,
        },
        "slo": asdict(slo),
        "capacity_users": max(passing) if passing else 0,
        "stages": [asdict(report) for report in reports],
    }


def print_stage(report: StageReport) -> None:
    verdict = "PASS" if report.slo_pass else "FAIL"
    print(
        f"[{verdict}] users={report.users:>4} rps={report.rps:>7} "
        f"p50={report.p50_ms:>7}ms p95={report.p95_ms:>7}ms p99={report.p99_ms:>7}ms "
        f"err={report.error_rate:.2%} statuses={report.status_counts}",
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stepped storefront load test with SLO verdicts.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--stages", default="5,10,20,40,80", help="Comma-separated virtual users.")
    parser.add_argument("--stage-seconds", type=float, default=30.0)
    parser.add_argument("--warmup", type=float, default=5.0)
    parser.add_argument("--think-ms", type=float, default=100.0)
    parser.add_argument("--soldout-ratio", type=float, default=0.1)
    parser.add_argument("--order-products", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--slo-p95", type=float, default=300.0)
    parser.add_argument("--slo-p99", type=float, default=1_000.0)
    parser.add_argument("--slo-error-rate", type=float, default=0.01)
    parser.add_argument("--stop-on-fail", action="store_true")
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--label", default="latest")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = asyncio.run(run_load_test(args))
    args.artifact_dir.mkdir(parents=True, exist_ok=True)
    path = args.artifact_dir / f"load-{args.label}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"capacity_users={result['capacity_users']} artifact={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
