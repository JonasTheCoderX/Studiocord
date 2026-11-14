"""Tiny localhost HTTP endpoint.

- POST /script      the Studio plugin reports the focused script
- POST /app/<cmd>   another Studiocord process (second launch, installer) asks the
                    running one to show its settings or to quit. Needs the
                    X-Studiocord header, which web pages can't send without a
                    CORS preflight that we never answer.
"""

import json
import logging
import sys
import threading
import time
import urllib.request
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
        self.collaborators = 0  # people in the Team Create session (incl. you)
        self.updated_at = 0.0   # last time the plugin checked in
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            return (self.name, self.path, self.cls, self.lines, self.updated_at,
                    self.collaborators)

    def clear(self):
        with self.lock:
            self.name = self.path = self.cls = ""
            self.lines = None
            self.collaborators = 0
            self.updated_at = 0.0


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


def send_command(cmd: str, port: int = PORT) -> bool:
    """Ask the running Studiocord to do something ("show-settings", "quit")."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/app/{cmd}", data=b"",
                                 method="POST", headers={"X-Studiocord": "1"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 204
    except OSError:
        return False


def start(info: ScriptInfo, port: int = PORT, commands: dict | None = None):
    commands = commands or {}

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code):
            self.send_response(code)
            self.send_header("X-Powered-By", WATERMARK)
            self.end_headers()

        def do_POST(self):
            if self.path.startswith("/app/"):
                fn = commands.get(self.path[5:])
                if fn is None or self.headers.get("X-Studiocord") != "1":
                    self._reply(404)
                    return
                self._reply(204)
                fn()
                return
            if self.path != "/script":
                self._reply(404)
                return
            try:
                length = min(int(self.headers.get("Content-Length", 0)), 4096)
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, OSError):
                self._reply(400)
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
                n = data.get("collaborators")
                info.collaborators = int(n) if isinstance(n, (int, float)) and 0 <= n < 1000 else 0
                info.updated_at = time.time()
            self._reply(204)

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
