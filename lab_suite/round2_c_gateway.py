import asyncio
import contextlib
import json
import multiprocessing
import os
import queue
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailer", "transfer-encoding", "upgrade", "content-length"}
MAX_BODY = 4 * 1024 * 1024


def emit(event, **fields):
    directory = Path(os.environ.get("LAB_EVIDENCE", "/evidence"))
    directory.mkdir(parents=True, exist_ok=True)
    record = {"event": event, "pid": os.getpid(), "at": time.monotonic(), **fields}
    with (directory / f"round2-c-{os.getpid()}.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")


async def read_headers(reader):
    raw = await reader.readuntil(b"\r\n\r\n")
    if len(raw) > 65536:
        raise ValueError("HTTP header block too large")
    lines = raw[:-4].split(b"\r\n")
    headers = []
    for line in lines[1:]:
        key, separator, value = line.partition(b":")
        if not separator or key[:1] in (b" ", b"\t"):
            raise ValueError("Invalid HTTP header")
        headers.append((key.decode("ascii").lower(), value.strip().decode("latin-1")))
    return lines[0].decode("latin-1"), headers


def values(headers, key):
    return [value for name, value in headers if name == key]


def content_length(headers):
    lengths = [int(value) for value in values(headers, "content-length")]
    if any(length < 0 or length > MAX_BODY for length in lengths):
        raise ValueError("Invalid body length")
    if len(set(lengths)) > 1:
        raise ValueError("Conflicting body lengths")
    return lengths[0] if lengths else None


async def read_chunks(reader):
    output = bytearray()
    while True:
        size = int((await reader.readline()).split(b";", 1)[0].strip(), 16)
        if size < 0 or len(output) + size > MAX_BODY:
            raise ValueError("Invalid chunk size")
        if not size:
            while await reader.readline() != b"\r\n":
                if reader.at_eof():
                    raise EOFError("Truncated trailers")
            return bytes(output)
        output.extend(await reader.readexactly(size))
        if await reader.readexactly(2) != b"\r\n":
            raise ValueError("Invalid chunk terminator")


async def read_response(reader, method):
    while True:
        status_line, headers = await read_headers(reader)
        status = int(status_line.split(" ", 2)[1])
        if 100 <= status < 200 and status != 101:
            continue
        break
    reusable = "close" not in ",".join(values(headers, "connection")).lower()
    if method == "HEAD" or status in (204, 304):
        body = b""
    elif values(headers, "transfer-encoding"):
        if values(headers, "transfer-encoding") != ["chunked"]:
            raise ValueError("Unsupported upstream transfer coding")
        body = await read_chunks(reader)
    elif (length := content_length(headers)) is not None:
        body = await reader.readexactly(length)
    else:
        reusable = False
        output = bytearray()
        while chunk := await reader.read(min(65536, MAX_BODY + 1 - len(output))):
            output.extend(chunk)
            if len(output) > MAX_BODY:
                raise ValueError("Unbounded upstream body")
        body = bytes(output)
    return status, headers, body, reusable


def request_bytes(method, path, headers, body, authority):
    tokens = {token.strip().lower() for value in values(headers, "connection")
              for token in value.split(",")}
    clean = [(key, value) for key, value in headers
             if key not in HOP | tokens | {"host", "expect"}]
    head = [f"{method} {path} HTTP/1.1", f"Host: {authority}", "Connection: keep-alive"]
    head.extend(f"{key}: {value}" for key, value in clean)
    if body or method == "POST":
        head.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body


def response_bytes(response, method):
    status, headers, body, _ = response
    tokens = {token.strip().lower() for value in values(headers, "connection")
              for token in value.split(",")}
    clean = [(key, value) for key, value in headers if key not in HOP | tokens]
    original_length = content_length(headers)
    length = original_length if method == "HEAD" and original_length is not None else len(body)
    head = [f"HTTP/1.1 {status} Response", f"Content-Length: {length}", "Connection: close"]
    head.extend(f"{key}: {value}" for key, value in clean)
    return ("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body


@dataclass
class Channel:
    identifier: int
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter


class OriginPool:
    def __init__(self, host, port, limit=8):
        self.host, self.port = host, port
        self.idle = []
        self.permits = asyncio.Semaphore(limit)
        self.sequence = 0

    async def acquire(self):
        await self.permits.acquire()
        try:
            while self.idle:
                channel = self.idle.pop()
                if not channel.reader.at_eof() and not channel.writer.is_closing():
                    return channel
                channel.writer.close()
            reader, writer = await asyncio.open_connection(self.host, self.port)
            self.sequence += 1
            return Channel(self.sequence, reader, writer)
        except BaseException:
            self.permits.release()
            raise

    def release(self, channel, complete):
        reusable = complete
        if reusable and not channel.reader.at_eof() and not channel.writer.is_closing():
            self.idle.append(channel)
        else:
            channel.writer.close()
        self.permits.release()

    async def exchange(self, method, path, headers, body):
        channel = await self.acquire()
        complete = False
        try:
            channel.writer.write(request_bytes(method, path, headers, body,
                                               f"{self.host}:{self.port}"))
            await channel.writer.drain()
            emit("origin_sent", path=path, channel=channel.identifier)
            response = await read_response(channel.reader, method)
            complete = response[3]
            emit("origin_complete", path=path, channel=channel.identifier)
            return response
        except asyncio.CancelledError:
            emit("origin_cancelled", path=path, channel=channel.identifier)
            raise
        finally:
            self.release(channel, complete)

    def close(self):
        for channel in self.idle:
            channel.writer.close()
        self.idle.clear()


def correlation_key(generation, sequence):
    return sequence


def worker_main(generation, inbox, outbox, upstream):
    import http.client

    target = urlsplit(upstream)
    while True:
        request = inbox.get()
        if request is None:
            return
        sequence, method, path, headers, body = request
        connection = http.client.HTTPConnection(target.hostname, target.port, timeout=15)
        try:
            emit("worker_started", generation=generation, sequence=sequence, path=path)
            forwarded = {key: value for key, value in headers if key not in HOP | {"host"}}
            connection.request(method, path, body=body, headers=forwarded)
            response = connection.getresponse()
            result = response.status, [(key.lower(), value) for key, value
                                       in response.getheaders()], response.read(), False
            outbox.put((generation, sequence, result, None))
            emit("worker_completed", generation=generation, sequence=sequence, path=path)
        except Exception as exc:
            outbox.put((generation, sequence, None, f"{type(exc).__name__}: {exc}"))
        finally:
            connection.close()


class WorkerBroker:
    def __init__(self, upstream):
        self.context = multiprocessing.get_context("spawn")
        self.outbox = self.context.Queue()
        self.upstream = upstream
        self.generation = 0
        self.sequence = 0
        self.pending = {}
        self.workers = {}
        self.received_control = None
        self.receivers = []
        self.rotate()

    def rotate(self):
        self.generation += 1
        self.sequence = 0
        inbox = self.context.Queue()
        worker = self.context.Process(target=worker_main,
                                      args=(self.generation, inbox, self.outbox, self.upstream))
        worker.start()
        for _, previous_inbox in self.workers.values():
            previous_inbox.put(None)
        self.workers[self.generation] = (worker, inbox)
        emit("worker_generation", generation=self.generation)

    async def exchange(self, method, path, headers, body):
        generation, sequence = self.generation, self.sequence
        self.sequence += 1
        key = correlation_key(generation, sequence)
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        self.workers[generation][1].put((sequence, method, path, headers, body))
        emit("broker_dispatched", generation=generation, sequence=sequence, path=path)
        try:
            return await future
        finally:
            if self.pending.get(key) is future:
                self.pending.pop(key, None)

    async def supervise(self):
        directory = Path(os.environ.get("LAB_EVIDENCE", "/evidence"))
        while True:
            for _ in range(64):
                try:
                    generation, sequence, response, error = self.outbox.get_nowait()
                except queue.Empty:
                    break
                key = correlation_key(generation, sequence)
                future = self.pending.pop(key, None)
                if future is not None and not future.done():
                    if error:
                        future.set_exception(RuntimeError(error))
                    else:
                        future.set_result(response)
                emit("broker_reply", generation=generation, sequence=sequence,
                     delivered=future is not None and not future.cancelled())
            control = directory / "round2-c-rotate.json"
            if control.exists():
                request = json.loads(control.read_text(encoding="utf-8"))
                if request.get("token") != self.received_control:
                    self.received_control = request["token"]
                    self.rotate()
                    emit("rotation_acknowledged", token=self.received_control,
                         generation=self.generation)
            for generation, (worker, inbox) in list(self.workers.items()):
                if generation != self.generation and not worker.is_alive():
                    worker.join()
                    inbox.close()
                    self.workers.pop(generation)
            await asyncio.sleep(0.01)

    async def close(self):
        for worker, inbox in self.workers.values():
            inbox.put(None)
            worker.join(timeout=0.2)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=1)
            inbox.close()
        self.outbox.close()


async def serve(case, host, port, upstream):
    target = urlsplit(upstream)
    pool = OriginPool(target.hostname, target.port or 80)
    broker = WorkerBroker(upstream) if case == "21" else None
    supervisor = asyncio.create_task(broker.supervise()) if broker else None

    async def handle(reader, writer):
        work = disconnected = None
        try:
            async with asyncio.timeout(20):
                line, headers = await read_headers(reader)
                method, path, version = line.split(" ", 2)
                if method not in {"GET", "HEAD", "POST"} or version != "HTTP/1.1":
                    raise ValueError("Unsupported request")
                if values(headers, "transfer-encoding"):
                    raise ValueError("Chunked client uploads are unsupported")
                length = content_length(headers) or 0
                body = await reader.readexactly(length)
                detail = method == "GET" and path.split("?", 1)[0].removeprefix(
                    "/products/").isdigit() and path.startswith("/products/")
                routed = detail if case == "20" else method == "POST" and path == "/orders"
                if routed:
                    exchange = broker.exchange if broker else pool.exchange
                    work = asyncio.create_task(exchange(method, path, headers, body))
                else:
                    disposable = OriginPool(target.hostname, target.port or 80, limit=1)

                    async def direct():
                        try:
                            return await disposable.exchange(method, path, headers, body)
                        finally:
                            disposable.close()

                    work = asyncio.create_task(direct())
                disconnected = asyncio.create_task(reader.read(1))
                done, _ = await asyncio.wait({work, disconnected},
                                             return_when=asyncio.FIRST_COMPLETED)
                if disconnected in done and work not in done:
                    work.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await work
                    emit("client_cancelled", path=path)
                    return
                response = await work
                writer.write(response_bytes(response, method))
                await writer.drain()
        except (EOFError, ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception as exc:
            emit("gateway_error", error=f"{type(exc).__name__}: {exc}")
            with contextlib.suppress(ConnectionError):
                body = b"upstream unavailable"
                writer.write(response_bytes((502, [("content-type", "text/plain")],
                                             body, False), "GET"))
                await writer.drain()
        finally:
            for task in (work, disconnected):
                if task is not None and not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    server = await asyncio.start_server(handle, host, port)
    emit("gateway_started", case=case, port=server.sockets[0].getsockname()[1])
    try:
        async with server:
            await server.serve_forever()
    finally:
        pool.close()
        if supervisor:
            supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await supervisor
            await broker.close()


def main():
    asyncio.run(serve(os.environ["LAB_CASE"], "0.0.0.0",
                      int(os.environ.get("PROXY_PORT", "8080")),
                      os.environ.get("UPSTREAM", "http://app:8000")))


if __name__ == "__main__":
    main()
