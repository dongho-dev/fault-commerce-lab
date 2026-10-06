import http.client
import itertools
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
UPSTREAMS = tuple(
    value.strip().rstrip("/")
    for value in os.environ.get("UPSTREAMS", os.environ.get("UPSTREAM", "http://app:8000")).split(
        ","
    )
    if value.strip()
)
if not UPSTREAMS:
    raise ValueError("at least one upstream is required")
ROTATION = itertools.cycle(UPSTREAMS)
ROTATION_LOCK = threading.Lock()


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args) -> None:
        return

    def do_GET(self) -> None:
        self.forward()

    def do_POST(self) -> None:
        self.forward()

    def do_HEAD(self) -> None:
        self.forward()

    def forward(self) -> None:
        with ROTATION_LOCK:
            target = urlsplit(next(ROTATION))
        connection_class = (
            http.client.HTTPSConnection if target.scheme == "https" else http.client.HTTPConnection
        )
        connection = connection_class(target.hostname, target.port, timeout=15)
        response_started = False
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            request_body = self.rfile.read(content_length) if content_length else None
            connection_tokens = {
                item.strip().lower() for item in self.headers.get("Connection", "").split(",")
            }
            request_headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in HOP_HEADERS | connection_tokens | {"host"}
            }
            request_headers["Host"] = target.netloc
            request_headers["Connection"] = "close"
            path = (target.path.rstrip("/") + self.path) or "/"
            connection.request(self.command, path, body=request_body, headers=request_headers)
            upstream_response = connection.getresponse()
            content_type = upstream_response.getheader("Content-Type", "")
            if "application/json" in content_type.lower():
                body = upstream_response.read()
            else:
                body = upstream_response.read(None)
            self.send_response_only(upstream_response.status, upstream_response.reason)
            response_connection_tokens = {
                item.strip().lower()
                for item in upstream_response.getheader("Connection", "").split(",")
            }
            has_content_length = False
            for key, value in upstream_response.getheaders():
                if key.lower() not in HOP_HEADERS | response_connection_tokens:
                    self.send_header(key, value)
                    has_content_length |= key.lower() == "content-length"
            if not has_content_length:
                self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            response_started = True
            if self.command != "HEAD":
                self.wfile.write(body)
                self.wfile.flush()
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if not response_started:
                body = ("upstream transport error: " + type(exc).__name__).encode()
                self.send_response_only(502)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
        finally:
            self.close_connection = True
            connection.close()


def main() -> None:
    server = ThreadingHTTPServer(
        ("0.0.0.0", int(os.environ.get("PROXY_PORT", "8080"))), ProxyHandler
    )
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
