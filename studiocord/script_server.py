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


_EMPTY = ("", "", "", None, 0.0, 0, "")
STALE_AFTER = 60  # the plugin checks in at least every 20 s


class ScriptInfo:
    """What each Studio's plugin last reported, kept apart per place.

    With several Studios open, every one runs the plugin; keying by the place id it
    reports keeps one Studio's script from showing up next to another's place.
    Entries are tuples: (name, path, class, lines, updated_at, collaborators, place_name).
    """

    def __init__(self):
        self.entries: dict = {}   # place id (int; None from plugins before 1.0.5) -> tuple
        self.lock = threading.Lock()

    def update(self, place_id, entry):
        with self.lock:
            self.entries[place_id] = entry

    def snapshot(self, place_id=None):
        """Info for the place being shown (falls back sensibly for local/new places)."""
        now = time.time()
        with self.lock:
            live = {k: v for k, v in self.entries.items() if now - v[4] < STALE_AFTER}
        if place_id in live:
            return live[place_id]
        if 0 in live:  # local file or a new place that isn't saved to Roblox yet
            return live[0]
        if None in live and len(live) == 1:  # an older plugin that doesn't say its place
            return live[None]
        return _EMPTY

    def last_seen(self) -> float:
        with self.lock:
            return max((v[4] for v in self.entries.values()), default=0.0)

    def clear(self):
        with self.lock:
            self.entries.clear()


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
            name = str(data.get("name", ""))[:100]
            path = str(data.get("path", ""))[:300]
            cls = str(data.get("class", ""))[:40]
            lines = data.get("lines")
            lines = int(lines) if isinstance(lines, (int, float)) and lines >= 0 else None
            n = data.get("collaborators")
            n = int(n) if isinstance(n, (int, float)) and 0 <= n < 1000 else 0
            pid = data.get("placeId")
            pid = int(pid) if isinstance(pid, (int, float)) and pid >= 0 else None
            prev = info.snapshot(pid)
            if (name, path, cls) != prev[:3]:
                log.info("Focused script: %s (%s lines)", path or "(none)",
                         "?" if lines is None else lines)
            info.update(pid, (name, path, cls, lines, time.time(), n,
                              str(data.get("place") or "")[:100]))
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
