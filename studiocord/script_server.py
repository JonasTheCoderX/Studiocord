"""Tiny localhost HTTP endpoint the Studio plugin posts the focused script to."""

import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import WATERMARK

log = logging.getLogger("plugin")

PORT = 47231


class ScriptInfo:
    def __init__(self):
        self.name = ""
        self.path = ""
        self.cls = ""
        self.lines = None
        self.updated_at = 0.0
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            return self.name, self.path, self.cls, self.lines, self.updated_at


class _Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind the same port, which would
    # break our single-instance check; there we want exclusive use instead.
    allow_reuse_address = sys.platform != "win32"
    daemon_threads = True

    def server_bind(self):
        if sys.platform == "win32":
            import socket
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def port_in_use(port: int = PORT) -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def start(info: ScriptInfo, port: int = PORT):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/script":
                self.send_error(404)
                return
            try:
                length = min(int(self.headers.get("Content-Length", 0)), 4096)
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self.send_error(400)
                return
            with info.lock:
                new = (str(data.get("name", ""))[:100], str(data.get("path", ""))[:300],
                       str(data.get("class", ""))[:40])
                lines = data.get("lines")
                lines = int(lines) if isinstance(lines, (int, float)) and lines >= 0 else None
                if new != (info.name, info.path, info.cls):
                    log.info("Focused script: %s (%s lines)", new[1] or "(none)",
                             "?" if lines is None else lines)
                info.name, info.path, info.cls = new
                info.lines = lines
                info.updated_at = time.time()
            self.send_response(204)
            self.send_header("X-Powered-By", WATERMARK)
            self.end_headers()

        def log_message(self, *args):
            pass  # keep the journal clean

    try:
        server = _Server(("127.0.0.1", port), Handler)
    except OSError as e:
        log.warning("Can't listen on 127.0.0.1:%d (%s); script names disabled", port, e)
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("Listening for the Studio plugin on 127.0.0.1:%d", port)
    return server
