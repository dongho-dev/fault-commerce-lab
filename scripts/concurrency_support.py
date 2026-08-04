import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class RequestSpec:
    payload: dict[str, Any]
    group: str = "default"


@dataclass(frozen=True)
class RequestResult:
    group: str
    status_code: int | None
    elapsed_ms: float
    started_at: float
    ended_at: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile_value / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def run_batch(
    *,
    base_url: str,
    specs: list[RequestSpec],
    timeout_seconds: float = 20.0,
) -> list[RequestResult]:
    if not specs:
        return []
    barrier = threading.Barrier(len(specs) + 1)
    limits = httpx.Limits(max_connections=len(specs), max_keepalive_connections=len(specs))

    def send(client: httpx.Client, spec: RequestSpec) -> RequestResult:
        barrier.wait()
        started_at = time.perf_counter()
        try:
            response = client.post("/orders", json=spec.payload)
            ended_at = time.perf_counter()
            return RequestResult(
                group=spec.group,
                status_code=response.status_code,
                elapsed_ms=(ended_at - started_at) * 1_000,
                started_at=started_at,
                ended_at=ended_at,
            )
        except httpx.HTTPError as exc:
            ended_at = time.perf_counter()
            return RequestResult(
                group=spec.group,
                status_code=None,
                elapsed_ms=(ended_at - started_at) * 1_000,
                started_at=started_at,
                ended_at=ended_at,
                error=type(exc).__name__,
            )

    with httpx.Client(base_url=base_url, timeout=timeout_seconds, limits=limits) as client:
        with ThreadPoolExecutor(max_workers=len(specs)) as executor:
            futures = [executor.submit(send, client, spec) for spec in specs]
            barrier.wait()
            return [future.result() for future in futures]


def summarize_results(results: list[RequestResult]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    errors = 0
    latencies = [result.elapsed_ms for result in results]
    for result in results:
        key = "connection_error" if result.status_code is None else str(result.status_code)
        status_counts[key] = status_counts.get(key, 0) + 1
        if result.error is not None:
            errors += 1
    duration_ms = 0.0
    if results:
        duration_ms = (
            max(item.ended_at for item in results) - min(item.started_at for item in results)
        ) * 1_000
    return {
        "total": len(results),
        "status_counts": status_counts,
        "connection_errors": errors,
        "duration_ms": round(duration_ms, 3),
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 3),
            "p95": round(percentile(latencies, 95), 3),
            "max": round(max(latencies, default=0.0), 3),
        },
    }
