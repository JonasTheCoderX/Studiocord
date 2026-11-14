"""Minimal Discord Rich Presence client over Discord's local IPC (no dependencies).

Linux: Unix socket ($XDG_RUNTIME_DIR/discord-ipc-N); Windows: named pipe
\\\\.\\pipe\\discord-ipc-N. Each frame is <opcode:u32le><length:u32le><json>.
Opcode 0 = handshake, 1 = frame (commands), 2 = close.
"""

import json
import logging
import os
import socket
import struct
import uuid

from .system import IS_WINDOWS, discord_endpoints

log = logging.getLogger("discord")

OP_HANDSHAKE, OP_FRAME, OP_CLOSE = 0, 1, 2


class DiscordError(Exception):
    pass


class _Pipe:
    """Windows named pipe with the bits of the socket API we use."""

    def __init__(self, path):
        self.f = open(path, "r+b", buffering=0)

    def sendall(self, data):
        self.f.write(data)
        self.f.flush()

    def recv(self, n):
        return self.f.read(n)

    def close(self):
        self.f.close()


def _open(path):
    if IS_WINDOWS:
        return _Pipe(path)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    try:
        s.connect(path)
    except OSError:
        s.close()
        raise
    return s


class DiscordIPC:
    def __init__(self, client_id: str):
        self.client_id = client_id
        self.sock = None
        self.user = None

    @property
    def connected(self):
        return self.sock is not None

    def connect(self):
        """Try every known endpoint; raise DiscordError if Discord isn't reachable."""
        last_err = None
        for path in discord_endpoints():
            try:
                self.sock = _open(path)
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
        raise DiscordError(f"Discord not reachable ({last_err or 'not running?'})")

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
        except (OSError, AttributeError, ValueError) as e:
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
        except (OSError, AttributeError, ValueError, struct.error) as e:
            self._drop()
            raise DiscordError(f"receive failed: {e}") from e
