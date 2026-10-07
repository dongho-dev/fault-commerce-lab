import http.client
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(os.environ.get("LAB_EVIDENCE", "/evidence"))
GUARD = threading.Condition()
STATE = {"backend": "app", "accepting": True, "writes": 0}
HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.replace(path)


def manage():
    previous = None
    while True:
        try:
            request = json.loads((ROOT / "router-control.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.02)
            continue
        if request["token"] == previous:
            time.sleep(0.02)
            continue
        response = {"token": request["token"], "passed": False}
        try:
            with GUARD:
                if request["action"] == "fence":
                    STATE["accepting"] = False
                    write(ROOT / "router-observation.json", {"token": request["token"], **STATE})
                    deadline = time.monotonic() + 20
                    while STATE["writes"]:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("Accepted writes did not drain")
                        GUARD.wait(remaining)
                elif request["action"] == "activate":
                    if request["backend"] not in {"app", "app-b"}:
                        raise ValueError("Unknown backend")
                    STATE["backend"] = request["backend"]
                    STATE["accepting"] = True
                else:
                    raise ValueError("Unknown operation")
                response.update(passed=True, state=dict(STATE))
        except Exception as exc:
            response["error"] = str(exc)
        write(ROOT / "router-response.json", response)
        previous = request["token"]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def respond(self, status, payload, headers=()):
        self.send_response_only(status)
        for name, value in headers:
            if name.lower() in {"content-type", "x-request-id"}:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def forward(self):
        connection = None
        reserved = False
        started = False
        try:
            with GUARD:
                if self.command == "POST":
                    if not STATE["accepting"]:
                        started = True
                        self.respond(
                            503,
                            b'{"error":"temporarily unavailable"}',
                            [("Content-Type", "application/json")],
                        )
                        return
                    STATE["writes"] += 1
                    reserved = True
                target = STATE["backend"]
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            tokens = {x.strip().lower() for x in self.headers.get("Connection", "").split(",")}
            headers = {
                key: value for key, value in self.headers.items() if key.lower() not in HOP | tokens
            }
            headers["Host"] = f"{target}:8000"
            headers["Connection"] = "close"
            connection = http.client.HTTPConnection(target, 8000, timeout=15)
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            started = True
            self.respond(response.status, payload, response.getheaders())
        except (OSError, ValueError, http.client.HTTPException):
            if not started:
                try:
                    self.respond(503, b"")
                except OSError:
                    pass
        finally:
            self.close_connection = True
            if connection is not None:
                connection.close()
            if reserved:
                with GUARD:
                    STATE["writes"] -= 1
                    GUARD.notify_all()


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=manage, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
