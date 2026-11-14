"""Minimal Discord Rich Presence client over the local IPC socket (no dependencies).

Protocol: each frame is <opcode:u32le><length:u32le><json>. Opcode 0 = handshake,
1 = frame (commands), 2 = close.
"""

import json
import logging
import os
import socket
import struct
import uuid

log = logging.getLogger("discord")

OP_HANDSHAKE, OP_FRAME, OP_CLOSE = 0, 1, 2

# Where various Discord builds put their socket, relative to $XDG_RUNTIME_DIR.
_SOCKET_DIRS = [
    "",                                          # native Discord / Vesktop native
    "app/com.discordapp.Discord",                # Flatpak Discord
    ".flatpak/com.discordapp.Discord/xdg-run",
    ".flatpak/dev.vencord.Vesktop/xdg-run",      # Flatpak Vesktop
    "snap.discord",                              # Snap Discord
]


class DiscordError(Exception):
    pass


def _candidate_sockets():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    for d in _SOCKET_DIRS:
        for i in range(10):
            path = os.path.join(base, d, f"discord-ipc-{i}")
            if os.path.exists(path):
                yield path


class DiscordIPC:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.sock = None
        self.user = None

    @property
    def connected(self):
        return self.sock is not None

    def connect(self):
        """Try every known socket; raise DiscordError if Discord isn't reachable."""
        last_err = None
        for path in _candidate_sockets():
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(5)
            try:
                s.connect(path)
                self.sock = s
                self._send(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
                op, data = self._recv()
                if op == OP_CLOSE or data.get("evt") != "READY":
                    raise DiscordError(f"handshake rejected: {data}")
                self.user = data.get("data", {}).get("user", {})
                log.info("Connected to Discord via %s as %s", path,
                         self.user.get("username", "?"))
                return
            except (OSError, DiscordError, ValueError) as e:
                last_err = e
                self._drop()
        raise DiscordError(f"Discord not reachable ({last_err or 'no socket found'})")

    def set_activity(self, activity: dict | None):
        """Set (or clear, with None) the presence. Raises DiscordError on failure."""
        args = {"pid": os.getpid()}
        if activity is not None:
            args["activity"] = activity
        self._send(OP_FRAME, {"cmd": "SET_ACTIVITY", "args": args,
                              "nonce": str(uuid.uuid4())})
        op, data = self._recv()
        if op == OP_CLOSE:
            self._drop()
            raise DiscordError(f"Discord closed the connection: {data}")
        if data.get("evt") == "ERROR":
            raise DiscordError(f"SET_ACTIVITY failed: {data.get('data')}")

    def close(self):
        if self.sock:
            try:
                self._send(OP_CLOSE, {})
            except DiscordError:
                pass
        self._drop()

    # --- internals ---------------------------------------------------------
    def _drop(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None

    def _send(self, op, payload):
        data = json.dumps(payload).encode()
        try:
            self.sock.sendall(struct.pack("<II", op, len(data)) + data)
        except (OSError, AttributeError) as e:
            self._drop()
            raise DiscordError(f"send failed: {e}") from e

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise DiscordError("connection closed by Discord")
            buf += chunk
        return buf

    def _recv(self):
        try:
            op, length = struct.unpack("<II", self._recv_exact(8))
            return op, json.loads(self._recv_exact(length) or b"{}")
        except (OSError, AttributeError, struct.error) as e:
            self._drop()
            raise DiscordError(f"receive failed: {e}") from e
