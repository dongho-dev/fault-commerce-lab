import hashlib
import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HOP_HEADERS = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade",
})
UPSTREAM = urlsplit(os.environ.get("UPSTREAM", "http://app:8000"))


def representation_tag(request_target, representation):
    validator_input = request_target.encode("utf-8") + b"\0" + representation
    return '"' + hashlib.sha256(validator_input).hexdigest() + '"'


def matches(condition, tag):
    if not condition:
        return False
    return any(value.strip().removeprefix("W/") == tag or value.strip() == "*"
               for value in condition.split(","))


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
        started = False
        catalogue = self.command == "GET" and urlsplit(self.path).path == "/products"
        try:
            connection = http.client.HTTPConnection(
                UPSTREAM.hostname, UPSTREAM.port or 80, timeout=10
            )
            length = int(self.headers.get("Content-Length", "0"))
            payload = self.rfile.read(length) if length else None
            tokens = {
                value.strip().lower() for value in self.headers.get("Connection", "").split(",")
            }
            excluded = HOP_HEADERS | tokens | {"host"}
            if catalogue:
                excluded |= {"if-none-match", "if-modified-since"}
            headers = {
                key: value for key, value in self.headers.items()
                if key.lower() not in excluded
            }
            headers["Host"] = UPSTREAM.netloc
            headers["Connection"] = "close"
            connection.request(self.command, UPSTREAM.path.rstrip("/") + self.path,
                               body=payload, headers=headers)
            response = connection.getresponse()
            body = response.read()
            status = response.status
            tag = None
            if catalogue and status == 200:
                tag = representation_tag(self.path, body)
                if matches(self.headers.get("If-None-Match"), tag):
                    status = 304
            self.send_response_only(status)
            response_tokens = {
                value.strip().lower()
                for value in response.getheader("Connection", "").split(",")
            }
            excluded = HOP_HEADERS | response_tokens | {"content-length"}
            if tag is not None:
                excluded |= {"etag", "cache-control", "last-modified", "expires"}
            for key, value in response.getheaders():
                if key.lower() not in excluded:
                    self.send_header(key, value)
            if tag is not None:
                self.send_header("ETag", tag)
                self.send_header("Cache-Control", "no-cache")
            if status not in {204, 304}:
                content_length = (
                    response.getheader("Content-Length") if self.command == "HEAD" else None
                )
                self.send_header("Content-Length", content_length or str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            started = True
            if self.command != "HEAD" and status not in {204, 304}:
                self.wfile.write(body)
                self.wfile.flush()
            if catalogue:
                print(json.dumps({
                    "event": "catalogue_response", "target": self.path,
                    "status": status, "upstream_status": response.status,
                    "if_none_match": self.headers.get("If-None-Match"),
                    "etag": tag, "representation_sha256": hashlib.sha256(body).hexdigest(),
                    "representation_bytes": len(body),
                }, ensure_ascii=False), flush=True)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            if not started:
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
            print(json.dumps({"event": "gateway_error", "error": type(exc).__name__}), flush=True)
        finally:
            self.close_connection = True
            if connection is not None:
                connection.close()


def main():
    if UPSTREAM.scheme != "http" or UPSTREAM.hostname is None:
        raise ValueError("This exercise requires an HTTP upstream")
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
