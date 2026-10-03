from http.server import BaseHTTPRequestHandler, HTTPServer
import os

FAIL = os.environ.get("FAIL", "0") == "1"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length)

        print(
            f"[backend] path={self.path} bytes={len(body)} fail={FAIL}",
            flush=True,
        )

        if FAIL:
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"backend unavailable")
            return

        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, format, *args):
        return


print(f"backend listening on :5000 fail={FAIL}", flush=True)
HTTPServer(("0.0.0.0", 5000), Handler).serve_forever()
