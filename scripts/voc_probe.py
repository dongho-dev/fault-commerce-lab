"""Measure storefront screens the way customers see them (TRAFFIC-001 VOC probe).

Each scenario replays the request bundle the frontend sends for one screen (parallel or
sequential, as the browser does) and records the time until the whole screen is ready.
Scenarios run under 0, 10, and 20 background virtual users from ``scripts.load_test``.

Environment: BASE_URL, ROUNDS (repetitions per condition), CONDITIONS (comma-separated users).
"""

import asyncio
import json
import os
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from scripts.concurrency_support import percentile
from scripts.load_test import Targets, perform, prepare_targets

BASE_URL = os.environ.get("BASE_URL", "http://app:8000")
CATEGORIES = ("digital", "home", "kitchen", "food", "beauty", "sports", "etc")
ROUNDS = int(os.environ.get("ROUNDS", "12"))
CONDITIONS = [int(value) for value in os.environ.get("CONDITIONS", "0,10,20").split(",")]
WARMUP_SECONDS = 5

ScreenResult = tuple[float, dict[str, float]]
Scenario = Callable[[httpx.AsyncClient, dict[str, Any]], Awaitable[ScreenResult]]


async def timed(request: Awaitable[httpx.Response]) -> tuple[httpx.Response, float]:
    started = time.perf_counter()
    response = await request
    return response, (time.perf_counter() - started) * 1_000


async def listing_screen(
    client: httpx.AsyncClient, *, q: str | None = None, category: str | None = None
) -> ScreenResult:
    """#/search screen: main list + total count + per-category counts, all in parallel."""
    base: dict[str, Any] = {"q": q} if q else {}
    main: dict[str, Any] = {**base, "sort": "recommended", "limit": 20, "offset": 0}
    if category:
        main["category"] = category
    requests = [
        client.get("/products", params=main),
        client.get("/products", params={**base, "limit": 1}),
        *(
            client.get("/products", params={**base, "category": slug, "limit": 1})
            for slug in CATEGORIES
        ),
    ]
    started = time.perf_counter()
    results = await asyncio.gather(*(timed(request) for request in requests))
    screen = (time.perf_counter() - started) * 1_000
    return screen, {
        "main_list": results[0][1],
        "count_reqs_max": max(elapsed for _, elapsed in results[1:]),
    }


async def category_home(client: httpx.AsyncClient, ctx: dict[str, Any]) -> ScreenResult:
    return await listing_screen(client, category="home")


async def search_coffee(client: httpx.AsyncClient, ctx: dict[str, Any]) -> ScreenResult:
    return await listing_screen(client, q="커피")


async def browse_categories(client: httpx.AsyncClient, ctx: dict[str, Any]) -> ScreenResult:
    total, parts = 0.0, {}
    for category in ("digital", "kitchen", "beauty"):
        screen, _ = await listing_screen(client, category=category)
        total += screen
        parts[category] = screen
    return total, parts


async def product_detail(client: httpx.AsyncClient, ctx: dict[str, Any]) -> ScreenResult:
    started = time.perf_counter()
    response, detail = await timed(client.get(f"/products/{random.choice(ctx['catalog'])}"))
    _, related = await timed(
        client.get("/products", params={"category": response.json()["category"], "limit": 6})
    )
    return (time.perf_counter() - started) * 1_000, {"detail_api": detail, "related_list": related}


async def place_order(client: httpx.AsyncClient, ctx: dict[str, Any]) -> ScreenResult:
    started = time.perf_counter()
    _, cart_check = await timed(client.get(f"/products/{ctx['order_id']}"))
    response, order = await timed(
        client.post(
            "/orders",
            json={"product_id": ctx["order_id"], "quantity": 1, "postal_code": "06236"},
        )
    )
    assert response.status_code == 201, response.text
    return (time.perf_counter() - started) * 1_000, {"cart_check": cart_check, "post_order": order}


SCENARIOS: dict[str, Scenario] = {
    "1_category_home": category_home,
    "2_search_coffee": search_coffee,
    "3_browse_3_categories": browse_categories,
    "4a_product_detail": product_detail,
    "4b_order": place_order,
}


def stats(values: list[float]) -> dict[str, float]:
    return {
        "p50": round(percentile(values, 50), 1),
        "p95": round(percentile(values, 95), 1),
        "max": round(max(values), 1),
    }


async def background(
    client: httpx.AsyncClient, users: int, targets: Targets, stop: asyncio.Event
) -> None:
    async def virtual_user(seed: int) -> None:
        rng = random.Random(seed)
        while not stop.is_set():
            await perform(client, rng, targets, 0.1)
            await asyncio.sleep(rng.uniform(0.05, 0.15))

    await asyncio.gather(*(virtual_user(seed) for seed in range(users)))


async def main() -> None:
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30, limits=limits) as client:
        targets = await prepare_targets(client, 20)
        ctx = {"catalog": targets.catalog_ids[:24], "order_id": targets.order_ids[0]}
        report: dict[int, dict[str, Any]] = {}
        for users in CONDITIONS:
            stop = asyncio.Event()
            load = asyncio.create_task(background(client, users, targets, stop)) if users else None
            await asyncio.sleep(WARMUP_SECONDS if users else 0)
            print(f"MARK start users={users} t={time.time():.0f}", flush=True)
            samples: dict[str, list[float]] = {name: [] for name in SCENARIOS}
            parts: dict[str, dict[str, list[float]]] = {name: {} for name in SCENARIOS}
            for _ in range(ROUNDS):
                for name, scenario in SCENARIOS.items():
                    total, detail = await scenario(client, ctx)
                    samples[name].append(total)
                    for part, value in detail.items():
                        parts[name].setdefault(part, []).append(value)
                    await asyncio.sleep(0.2)
            print(f"MARK end users={users} t={time.time():.0f}", flush=True)
            if load:
                stop.set()
                await load
            report[users] = {
                name: {
                    "screen": stats(samples[name]),
                    "parts": {part: stats(values) for part, values in parts[name].items()},
                }
                for name in SCENARIOS
            }
        print("RESULT " + json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
