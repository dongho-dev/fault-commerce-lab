import asyncio
import contextlib
import json

import pytest

from lab_suite.round2_c_gateway import OriginPool, read_headers


@contextlib.asynccontextmanager
async def origin_server(handler):
    tasks = set()

    async def accept(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            await handler(reader, writer)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


def response_bytes(path, *, close=False):
    body = json.dumps({"path": path}).encode()
    connection = b"Connection: close\r\n" if close else b""
    headers = (
        b"HTTP/1.1 200 OK\r\nContent-Length: "
        + str(len(body)).encode()
        + b"\r\n"
        + connection
        + b"\r\n"
    )
    return headers, body


@pytest.mark.parametrize("phase", ["before_headers", "during_body"])
def test_cancelled_response_does_not_block_or_replace_next_product(tmp_path, monkeypatch, phase):
    monkeypatch.setenv("LAB_EVIDENCE", str(tmp_path))

    async def exercise():
        waiting = asyncio.Event()
        release = asyncio.Event()
        connections = []

        async def origin(reader, writer):
            connections.append(writer.get_extra_info("peername"))
            while True:
                line, _ = await read_headers(reader)
                path = line.split()[1]
                headers, body = response_bytes(path)
                if path == "/products/1":
                    if phase == "during_body":
                        writer.write(headers + body[:5])
                        await writer.drain()
                        headers, body = b"", body[5:]
                    waiting.set()
                    await release.wait()
                writer.write(headers + body)
                await writer.drain()

        async with origin_server(origin) as port:
            pool = OriginPool("127.0.0.1", port, limit=1)
            first = asyncio.create_task(pool.exchange("GET", "/products/1", [], b""))
            try:
                await asyncio.wait_for(waiting.wait(), 2)
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                for path in ("/products/2", "/products/3"):
                    response = await asyncio.wait_for(pool.exchange("GET", path, [], b""), 2)
                    assert response[0] == 200
                    assert json.loads(response[2]) == {"path": path}
                assert len(connections) == 2
            finally:
                release.set()
                first.cancel()
                await asyncio.gather(first, return_exceptions=True)
                pool.close()

    asyncio.run(exercise())


def test_completed_responses_reuse_the_same_origin_connection(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_EVIDENCE", str(tmp_path))

    async def exercise():
        connections = []

        async def origin(reader, writer):
            connections.append(writer.get_extra_info("peername"))
            while True:
                line, _ = await read_headers(reader)
                headers, body = response_bytes(line.split()[1])
                writer.write(headers + body)
                await writer.drain()

        async with origin_server(origin) as port:
            pool = OriginPool("127.0.0.1", port, limit=1)
            try:
                for path in ("/products/1", "/products/2"):
                    response = await asyncio.wait_for(pool.exchange("GET", path, [], b""), 2)
                    assert json.loads(response[2]) == {"path": path}
                assert len(connections) == 1
            finally:
                pool.close()

    asyncio.run(exercise())


def test_connection_close_response_is_not_returned_to_the_pool(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_EVIDENCE", str(tmp_path))

    async def exercise():
        disconnected = asyncio.Event()

        async def origin(reader, writer):
            line, _ = await read_headers(reader)
            headers, body = response_bytes(line.split()[1], close=True)
            writer.write(headers + body)
            await writer.drain()
            if await reader.read(1) == b"":
                disconnected.set()

        async with origin_server(origin) as port:
            pool = OriginPool("127.0.0.1", port, limit=1)
            try:
                response = await asyncio.wait_for(pool.exchange("GET", "/products/1", [], b""), 2)
                assert json.loads(response[2]) == {"path": "/products/1"}
                await asyncio.wait_for(disconnected.wait(), 2)
            finally:
                pool.close()

    asyncio.run(exercise())
