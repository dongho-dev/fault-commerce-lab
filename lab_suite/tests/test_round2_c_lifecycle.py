import asyncio
import contextlib
import json
import sys
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from lab_suite import round2_c_gateway as gateway
from lab_suite.cases import advanced_round2_c as cases


def variant(fault):
    source = Path(gateway.__file__).read_text(encoding="utf-8")
    if fault:
        _, before, after = cases.replacements("20")[0]
        assert source.count(before) == 1
        source = source.replace(before, after, 1)
    name = f"round2_c_test_variant_{int(fault)}"
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, str(gateway.__file__), "exec"), module.__dict__)
    return module


@pytest.mark.parametrize("fault", [False, True])
def test_cancelled_origin_response_cannot_be_delivered_to_next_customer(
        tmp_path, monkeypatch, fault):
    monkeypatch.setenv("LAB_EVIDENCE", str(tmp_path))
    runtime = variant(fault)

    async def exercise():
        started = asyncio.Event()
        release = asyncio.Event()
        handler_tasks = set()

        async def origin(reader, writer):
            handler_tasks.add(asyncio.current_task())
            try:
                while True:
                    line, _ = await gateway.read_headers(reader)
                    path = line.split()[1]
                    if path == "/products/1":
                        started.set()
                        await release.wait()
                    body = json.dumps({"path": path}).encode()
                    wire = (b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode()
                            + b"\r\n\r\n" + body)
                    writer.write(wire)
                    await writer.drain()
            except (asyncio.IncompleteReadError, ConnectionError):
                pass
            finally:
                writer.close()
                handler_tasks.discard(asyncio.current_task())

        server = await asyncio.start_server(origin, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        pool = runtime.OriginPool("127.0.0.1", port)
        try:
            first = asyncio.create_task(pool.exchange("GET", "/products/1", [], b""))
            await asyncio.wait_for(started.wait(), 2)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(pool.exchange("GET", "/products/2", [], b""))
            if not fault:
                response = await asyncio.wait_for(second, 2)
                assert json.loads(response[2]) == {"path": "/products/2"}
            release.set()
            response = await asyncio.wait_for(second, 2)
            assert json.loads(response[2]) == {
                "path": "/products/1" if fault else "/products/2"}
            third = await pool.exchange("GET", "/products/3", [], b"")
            assert json.loads(third[2]) == {
                "path": "/products/2" if fault else "/products/3"}
            assert pool.sequence == (1 if fault else 2)
            assert pool.permits._value == 8
        finally:
            release.set()
            pool.close()
            server.close()
            await server.wait_closed()
            for task in list(handler_tasks):
                task.cancel()
            if handler_tasks:
                await asyncio.gather(*handler_tasks, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("method,wire,expected,reusable", [
    ("GET", b"HTTP/1.1 103 Early Hints\r\nLink: </x>\r\n\r\n"
     b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok", b"ok", True),
    ("GET", b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
     b"2\r\nok\r\n0\r\nX-Trace: yes\r\n\r\n", b"ok", True),
    ("HEAD", b"HTTP/1.1 200 OK\r\nContent-Length: 123\r\n\r\n", b"", True),
    ("GET", b"HTTP/1.1 304 Not Modified\r\nContent-Length: 123\r\n\r\n", b"", True),
    ("GET", b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nok", b"ok", False),
])
def test_response_boundary_matrix_over_real_tcp(method, wire, expected, reusable):
    async def exercise():
        async def origin(reader, writer):
            writer.write(wire)
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(origin, "127.0.0.1", 0)
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", server.sockets[0].getsockname()[1])
            result = await asyncio.wait_for(gateway.read_response(reader, method), 2)
            assert result[2:] == (expected, reusable)
            writer.close()
            await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(exercise())


@pytest.mark.parametrize("cancel_old", [False, True])
def test_worker_generation_overlap_retains_each_live_reply_owner(
        tmp_path, monkeypatch, cancel_old):
    monkeypatch.setenv("LAB_EVIDENCE", str(tmp_path))
    entered = {"/products/1": threading.Event(), "/products/2": threading.Event()}
    release = {"/products/1": threading.Event(), "/products/2": threading.Event()}

    class Origin(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            entered[self.path].set()
            if not release[self.path].wait(10):
                self.send_error(504)
                return
            body = json.dumps({"path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            with contextlib.suppress(ConnectionError):
                self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def exercise():
        broker = gateway.WorkerBroker(f"http://127.0.0.1:{server.server_port}")
        monitor = asyncio.create_task(broker.supervise())
        pending = []
        try:
            old_generation = broker.generation
            old = asyncio.create_task(broker.exchange("GET", "/products/1", [], b""))
            pending.append(old)
            assert await asyncio.to_thread(entered["/products/1"].wait, 5)
            if cancel_old:
                old.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await old
            broker.rotate()
            current = asyncio.create_task(broker.exchange("GET", "/products/2", [], b""))
            pending.append(current)
            assert await asyncio.to_thread(entered["/products/2"].wait, 5)
            release["/products/1"].set()
            deadline = time.monotonic() + 5
            while not any(item["event"] == "broker_reply"
                          and item["generation"] == old_generation
                          for item in cases._events(tmp_path)):
                assert time.monotonic() < deadline
                await asyncio.sleep(0.01)
            assert not current.done()
            if not cancel_old:
                response = await asyncio.wait_for(old, 2)
                assert json.loads(response[2]) == {"path": "/products/1"}
            release["/products/2"].set()
            response = await asyncio.wait_for(current, 5)
            assert json.loads(response[2]) == {"path": "/products/2"}
            assert broker.pending == {}
        finally:
            for event in release.values():
                event.set()
            for task in pending:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            monitor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await monitor
            await broker.close()

    try:
        asyncio.run(exercise())
    finally:
        for event in release.values():
            event.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_fault_anchors_are_unique_and_protected_paths_are_not_replaced():
    for case_id in ("20", "21"):
        for path, before, after in cases.replacements(case_id):
            source = Path(path).read_text(encoding="utf-8")
            assert source.count(before) == 1
            assert before != after
            assert path == "lab_suite/round2_c_gateway.py"
        assert cases.runtime_paths(case_id) == [
            "lab_suite/round2_c_gateway.py"]


def test_generation_identity_survives_reused_local_sequence():
    assert gateway.correlation_key(1, 0) != gateway.correlation_key(2, 0)
    assert gateway.correlation_key(2, 0) != gateway.correlation_key(2, 1)
