import asyncio
import json
import os
import sys
from datetime import UTC, datetime

import pytest

BASE_URL = os.getenv("BASE_URL", "http://app:8000").rstrip("/")


def _cart_snapshot(
    *, product_id: int = 901, quantity: int = 2, unit_price: int = 10_000
) -> list[dict]:
    return [
        {
            "id": product_id,
            "qty": quantity,
            "selected": True,
            "snap": {
                "name": "브라우저 quote 상품",
                "brand": "Fault Lab",
                "unit_price": unit_price,
                "list_price": unit_price,
                "image_url": "",
                "category": "digital",
                "current_stock": 20,
            },
        }
    ]


def _quote(
    *, product_id: int, quantity: int, postal_code: str, unit_price: int, shipping_fee: int
) -> dict:
    merchandise_amount = unit_price * quantity
    return {
        "product_id": product_id,
        "quantity": quantity,
        "unit_price": unit_price,
        "postal_code": postal_code,
        "merchandise_amount": merchandise_amount,
        "shipping_fee": shipping_fee,
        "total_amount": merchandise_amount + shipping_fee,
    }


def _order(quote: dict) -> dict:
    return {
        "id": 7001,
        "product_id": quote["product_id"],
        "quantity": quote["quantity"],
        "unit_price": quote["unit_price"],
        "postal_code": quote["postal_code"],
        "shipping_fee": quote["shipping_fee"],
        "total_amount": quote["total_amount"],
        "status": "CONFIRMED",
        "created_at": datetime.now(UTC).isoformat(),
    }


async def _open_checkout(page, *, postal: str = "") -> None:
    cart_value = json.dumps(json.dumps(_cart_snapshot(), ensure_ascii=False))
    postal_value = json.dumps(json.dumps(postal))
    await page.add_init_script(
        f"localStorage.setItem('faultmart.cart.v1', {cart_value});"
        f"if ({'true' if postal else 'false'}) "
        f"localStorage.setItem('faultmart.postal', {postal_value});"
    )
    await page.goto(f"{BASE_URL}/#/checkout", wait_until="domcontentloaded")
    await page.locator("[data-checkout-form]").wait_for()


async def _browser_context():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip("Playwright is available only in the isolated browser container")
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True, args=["--no-sandbox"])
    context = await browser.new_context(viewport={"width": 1440, "height": 1100})
    return playwright, browser, context


@pytest.mark.skipif(
    sys.platform != "linux", reason="browser checks run in the isolated Linux probe container"
)
def test_checkout_quotes_are_server_source_and_preserve_leading_zero() -> None:
    async def run() -> None:
        playwright, browser, context = await _browser_context()
        quote_calls: list[dict] = []
        order_calls: list[dict] = []

        async def quote_route(route) -> None:
            payload = json.loads(route.request.post_data or "{}")
            quote_calls.append(payload)
            response = _quote(
                product_id=payload["product_id"],
                quantity=payload["quantity"],
                postal_code=payload["postal_code"],
                unit_price=12_345,
                shipping_fee=7,
            )
            await route.fulfill(
                status=200, content_type="application/json", body=json.dumps(response)
            )

        async def order_route(route) -> None:
            payload = json.loads(route.request.post_data or "{}")
            order_calls.append(payload)
            response = _order(
                _quote(
                    product_id=payload["product_id"],
                    quantity=payload["quantity"],
                    postal_code=payload["postal_code"],
                    unit_price=12_345,
                    shipping_fee=7,
                )
            )
            await route.fulfill(
                status=201, content_type="application/json", body=json.dumps(response)
            )

        await context.route("**/orders/quote", quote_route)
        await context.route("**/orders", order_route)
        page = await context.new_page()
        try:
            await _open_checkout(page)
            await page.locator("[data-postal]").fill("06236")
            await page.wait_for_function(
                "() => document.querySelector('[data-pay]') "
                "&& !document.querySelector('[data-pay]').disabled"
            )
            summary = await page.locator("[data-co-summary]").inner_text()
            lines = await page.locator("[data-co-lines]").inner_text()
            assert "24,690원" in summary
            assert "7원" in summary
            assert "24,697원" in summary
            assert "12,345원" in lines
            assert "10,000원" not in lines
            assert quote_calls == [{"product_id": 901, "quantity": 2, "postal_code": "06236"}]

            await page.locator("[data-pay]").click()
            await page.wait_for_function("() => location.hash === '#/complete'")
            assert order_calls == [{"product_id": 901, "quantity": 2, "postal_code": "06236"}]
        finally:
            await context.close()
            await browser.close()
            await playwright.stop()

    asyncio.run(run())


@pytest.mark.skipif(
    sys.platform != "linux", reason="browser checks run in the isolated Linux probe container"
)
def test_checkout_ignores_stale_quote_and_retries_server_error() -> None:
    async def run() -> None:
        playwright, browser, context = await _browser_context()
        quote_calls: list[dict] = []
        attempts: dict[str, int] = {}
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        first_finished = asyncio.Event()

        async def quote_route(route) -> None:
            payload = json.loads(route.request.post_data or "{}")
            postal = payload["postal_code"]
            quote_calls.append(payload)
            attempts[postal] = attempts.get(postal, 0) + 1
            if postal == "06236":
                first_started.set()
                await release_first.wait()
                response = _quote(
                    product_id=payload["product_id"],
                    quantity=payload["quantity"],
                    postal_code=postal,
                    unit_price=11_000,
                    shipping_fee=3_000,
                )
                try:
                    await route.fulfill(
                        status=200, content_type="application/json", body=json.dumps(response)
                    )
                finally:
                    first_finished.set()
                return
            if postal == "48058" and attempts[postal] == 1:
                await route.fulfill(
                    status=503,
                    content_type="application/json",
                    body=json.dumps(
                        {"code": "TEMPORARY_QUOTE_FAILURE", "message": "일시적인 계산 오류"}
                    ),
                )
                return
            response = _quote(
                product_id=payload["product_id"],
                quantity=payload["quantity"],
                postal_code=postal,
                unit_price=12_000,
                shipping_fee=4_000 if postal == "16841" else 5_000,
            )
            await asyncio.sleep(0.02)
            await route.fulfill(
                status=200, content_type="application/json", body=json.dumps(response)
            )

        await context.route("**/orders/quote", quote_route)
        page = await context.new_page()
        try:
            await _open_checkout(page)
            await page.locator("[data-postal]").fill("06236")
            await asyncio.wait_for(first_started.wait(), timeout=5)
            assert await page.locator("[data-pay]").is_disabled()
            await page.locator("[data-postal]").fill("16841")
            assert await page.locator("[data-pay]").is_disabled()
            await page.wait_for_function(
                "() => document.querySelector('[data-pay]') "
                "&& !document.querySelector('[data-pay]').disabled"
            )
            summary = await page.locator("[data-co-summary]").inner_text()
            assert "28,000원" in summary
            assert "25,000원" not in summary
            assert [call["postal_code"] for call in quote_calls[:2]] == ["06236", "16841"]
            release_first.set()
            await asyncio.wait_for(first_finished.wait(), timeout=5)
            await page.wait_for_timeout(100)
            assert "28,000원" in await page.locator("[data-co-summary]").inner_text()

            await page.locator("[data-postal]").fill("48058")
            await page.locator("[data-quote-retry]").wait_for()
            assert await page.locator("[data-pay]").is_disabled()
            await page.locator("[data-quote-retry]").click()
            await page.wait_for_function(
                "() => document.querySelector('[data-pay]') "
                "&& !document.querySelector('[data-pay]').disabled"
            )
            assert "29,000원" in await page.locator("[data-co-summary]").inner_text()
            assert attempts["48058"] == 2
        finally:
            release_first.set()
            await context.close()
            await browser.close()
            await playwright.stop()

    asyncio.run(run())


@pytest.mark.skipif(
    sys.platform != "linux", reason="browser checks run in the isolated Linux probe container"
)
def test_cart_defers_without_postal_and_uses_server_quotes_with_saved_postal() -> None:
    async def run() -> None:
        playwright, browser, context = await _browser_context()
        quote_calls: list[dict] = []

        async def quote_route(route) -> None:
            payload = json.loads(route.request.post_data or "{}")
            quote_calls.append(payload)
            response = _quote(
                product_id=payload["product_id"],
                quantity=payload["quantity"],
                postal_code=payload["postal_code"],
                unit_price=13_000,
                shipping_fee=9_000,
            )
            await route.fulfill(
                status=200, content_type="application/json", body=json.dumps(response)
            )

        async def product_route(route) -> None:
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "id": 901,
                        "name": "브라우저 quote 상품",
                        "brand": "Fault Lab",
                        "unit_price": 10_000,
                        "list_price": 10_000,
                        "image_url": "",
                        "category": "digital",
                        "description": "",
                        "current_stock": 20,
                    }
                ),
            )

        await context.route("**/products/901", product_route)
        await context.route("**/orders/quote", quote_route)
        page = await context.new_page()
        try:
            await page.add_init_script(
                "localStorage.setItem('faultmart.cart.v1', "
                f"{json.dumps(json.dumps(_cart_snapshot(), ensure_ascii=False))});"
            )
            await page.goto(f"{BASE_URL}/#/cart", wait_until="domcontentloaded")
            await page.locator("[data-go-checkout]").wait_for()
            assert "주문 단계에서 확정" in await page.locator(".summary dl").inner_text()
            assert quote_calls == []

            await page.evaluate("localStorage.setItem('faultmart.postal', JSON.stringify('06236'))")
            await page.reload(wait_until="domcontentloaded")
            await page.locator("[data-go-checkout]").wait_for()
            await page.wait_for_function(
                "() => document.querySelector('.line-total')?.textContent.includes('26,000원')"
            )
            assert "9,000원" in await page.locator(".summary dl").inner_text()
            assert "35,000원" in await page.locator(".summary dl").inner_text()
            assert quote_calls == [{"product_id": 901, "quantity": 2, "postal_code": "06236"}]
            await page.locator("[data-check='901']").uncheck()
            assert "선택하지 않은 상품" in await page.locator(".cart-info").inner_text()
            assert "확인 중" not in await page.locator(".cart-line").inner_text()
            assert "선택된 상품 없음" in await page.locator(".summary dl").inner_text()
            assert await page.locator("[data-go-checkout]").is_disabled()
        finally:
            await context.close()
            await browser.close()
            await playwright.stop()

    asyncio.run(run())


@pytest.mark.skipif(
    sys.platform != "linux", reason="browser checks run in the isolated Linux probe container"
)
def test_order_submission_is_once_and_saved_after_navigation() -> None:
    async def run() -> None:
        playwright, browser, context = await _browser_context()
        order_started = asyncio.Event()
        release_order = asyncio.Event()
        order_calls = []
        errors = []
        quote = _quote(
            product_id=901, quantity=2, postal_code="06236", unit_price=10_000, shipping_fee=3_000
        )

        async def quote_route(route) -> None:
            await route.fulfill(status=200, content_type="application/json", body=json.dumps(quote))

        async def order_route(route) -> None:
            order_calls.append(json.loads(route.request.post_data))
            order_started.set()
            await release_order.wait()
            await route.fulfill(
                status=201, content_type="application/json", body=json.dumps(_order(quote))
            )

        await context.route("**/orders/quote", quote_route)
        await context.route("**/orders", order_route)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await _open_checkout(page, postal="06236")
            await page.locator("[data-pay]:enabled").wait_for()
            await page.locator("[data-pay]").click()
            await asyncio.wait_for(order_started.wait(), timeout=5)
            await page.locator("[data-checkout-form]").evaluate(
                "form => { form.requestSubmit(); form.requestSubmit(); }"
            )
            await page.evaluate("location.hash = '#/orders'")
            await page.locator(".page-title").filter(has_text="주문내역").wait_for()
            release_order.set()
            await page.wait_for_function(
                "() => JSON.parse(localStorage.getItem('faultmart.orders.v1') || '[]')"
                "[0]?.orders[0]?.id === 7001"
            )
            assert await page.evaluate("location.hash") == "#/orders"
            assert order_calls == [{"product_id": 901, "quantity": 2, "postal_code": "06236"}]
            assert errors == []
            await page.reload(wait_until="domcontentloaded")
            await page.locator(".order-card").wait_for()
            assert "23,000원" in await page.locator("#app").inner_text()
        finally:
            release_order.set()
            await context.close()
            await browser.close()
            await playwright.stop()

    asyncio.run(run())
