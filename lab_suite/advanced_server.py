import contextlib
import json
import multiprocessing
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


def initialize_worker(engine):
    engine.dispose(close=False)


def _worker(listener):
    import uvicorn

    from app.database import engine
    from app.main import app

    initialize_worker(engine)

    async def identified_application(scope, receive, send):
        async def identified_send(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-lab-worker", str(os.getpid()).encode("ascii")),
                ]
            await send(message)

        await app(scope, receive, identified_send)

    uvicorn.run(identified_application, host="0.0.0.0", port=8000, fd=listener.fileno(),
                access_log=False, timeout_graceful_shutdown=5)


def _fixtures(app):
    from fastapi.testclient import TestClient

    products = []
    with TestClient(app) as client:
        for index in range(12):
            name = f"CS14 catalogue item {index:02d}"
            existing = client.get("/products", params={"q": name, "limit": 20})
            existing.raise_for_status()
            matches = [item for item in existing.json()["items"] if item["name"] == name]
            if matches:
                products.append(matches[0])
                continue
            response = client.post("/products", json={
                "name": name, "unit_price": 12000 + index * 100,
                "initial_stock": 10, "category": "digital",
                "description": f"Product specification {index:02d}",
            })
            response.raise_for_status()
            products.append(response.json())
    return products


def main():
    if sys.platform != "linux":
        raise RuntimeError("Prefork exercise runs only in the isolated Linux container")
    if os.environ.get("LAB_MIGRATE", "1") == "1":
        subprocess.run(["alembic", "upgrade", "head"], check=True)
    from sqlalchemy import text

    from app.database import engine
    from app.main import app

    products = _fixtures(app)
    evidence = Path(os.environ.get("LAB_EVIDENCE", "/evidence"))
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "case14-fixtures.json").write_text(
        json.dumps(products, ensure_ascii=False), encoding="utf-8"
    )
    engine.dispose()
    with contextlib.ExitStack() as stack:
        connections = [stack.enter_context(engine.connect()) for _ in range(8)]
        for connection in connections:
            connection.execute(text("SELECT 1"))
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", 8000))
    listener.listen(128)
    listener.set_inheritable(True)
    context = multiprocessing.get_context("fork")
    workers = [context.Process(target=_worker, args=(listener,)) for _ in range(2)]
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    for worker in workers:
        worker.start()
    print(json.dumps({"event": "workers_started", "parent": os.getpid(),
                      "workers": [worker.pid for worker in workers]}), flush=True)
    try:
        while not stopping and all(worker.is_alive() for worker in workers):
            time.sleep(0.1)
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
        for worker in workers:
            worker.join(timeout=8)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=3)
        listener.close()
        engine.dispose()
    if not stopping and any(worker.exitcode != 0 for worker in workers):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
