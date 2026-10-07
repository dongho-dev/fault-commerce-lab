import http.client
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        return

    def do_GET(self):
        self.forward()

    def do_POST(self):
        self.forward()

    def forward(self):
        connection = None
        try:
            selector = Path(os.environ.get("LAB_EVIDENCE", "/evidence")) / "active-backend.json"
            target = json.loads(selector.read_text(encoding="utf-8"))["backend"] if selector.exists() else "app"
            if target not in {"app", "app-b"}:
                raise ValueError("Unrecognized backend")
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {key: value for key, value in self.headers.items() if key.lower() not in {"connection", "host"}}
            headers["Host"] = f"{target}:8000"
            connection = http.client.HTTPConnection(target, 8000, timeout=15)
            connection.request(self.command, self.path, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self.send_response_only(response.status)
            for name, value in response.getheaders():
                if name.lower() in {"content-type", "x-request-id"}:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        except (OSError, ValueError, http.client.HTTPException):
            self.send_response_only(503)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
        finally:
            self.close_connection = True
            if connection is not None:
                connection.close()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
