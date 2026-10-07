import http.client
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOP_HEADERS = frozenset(
    {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
     "te", "trailer", "transfer-encoding", "upgrade"}
)
TARGET = urlsplit(os.environ.get("UPSTREAM", "http://catalog-service:8000"))


def resolve_host(host):
    return socket.gethostbyname(host)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def do_HEAD(self):
        self.forward()

    def forward(self):
        connection = None
        response_started = False
        address = None
        try:
            address = resolve_host(TARGET.hostname)
            connection = http.client.HTTPConnection(address, TARGET.port or 80, timeout=1)
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length) if length else None
            tokens = {
                value.strip().lower() for value in self.headers.get("Connection", "").split(",")
            }
            headers = {
                key: value for key, value in self.headers.items()
                if key.lower() not in HOP_HEADERS | tokens | {"host"}
            }
            headers["Host"] = TARGET.netloc
            headers["Connection"] = "close"
            connection.request(self.command, TARGET.path.rstrip("/") + self.path,
                               body=payload, headers=headers)
            response = connection.getresponse()
            body = response.read()
            response_tokens = {
                value.strip().lower()
                for value in response.getheader("Connection", "").split(",")
            }
            self.send_response_only(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in HOP_HEADERS | response_tokens | {"content-length"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            response_started = True
            if self.command != "HEAD":
                self.wfile.write(body)
                self.wfile.flush()
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if not response_started:
                body = b"upstream unavailable"
                self.send_response_only(502)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass
            print(json.dumps({"event": "upstream_error", "address": address,
                              "error": type(exc).__name__}), flush=True)
        finally:
            self.close_connection = True
            if connection is not None:
                connection.close()


def main():
    if TARGET.scheme != "http" or TARGET.hostname is None:
        raise ValueError("This exercise requires an HTTP upstream")
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
