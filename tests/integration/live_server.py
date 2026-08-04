import socket
import subprocess
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager

import httpx


def available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def running_server() -> Generator[str, None, None]:
    port = available_port()
    base_url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"live server exited with status {process.returncode}")
            try:
                response = httpx.get(f"{base_url}/health/ready", timeout=1.0)
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            try:
                exit_status = process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                continue
            raise RuntimeError(f"live server exited with status {exit_status}")
        else:
            raise RuntimeError("live server did not become ready")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
