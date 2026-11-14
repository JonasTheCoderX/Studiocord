"""Studiocord: Discord Rich Presence for Roblox Studio (Windows, and Linux via Vinegar).

Runs in the background with a tray icon. While Roblox Studio runs, it follows Studio's
own log files and shows what you're doing on Discord. When Studio closes, it clears it.

    studiocord                 run (tray icon if a desktop is available)
    studiocord --headless      run without tray icon / windows
    studiocord --dry-run       print presence instead of sending it to Discord
    studiocord --replay LOG    replay a Studio log file and print each presence
    studiocord --diagnose      show what Studiocord can find on this computer
    studiocord --install / --uninstall   (standalone Linux build)
"""

import argparse
import glob
import hashlib
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass

from . import WATERMARK, __version__, script_server, settings, system
from .discord_ipc import DiscordError, DiscordIPC
from .roblox_api import PlaceLookup
from .studio_log import DOC_SCRIPT, LogFollower, StudioState, apply_line

log = logging.getLogger("studiocord")

CONFIG_PATH = system.config_path()
MIN_SEND_INTERVAL = 4.0   # Discord allows ~5 updates per 20 s
RECONNECT_INTERVAL = 15.0
PLUGIN_STALE_AFTER = 60   # the plugin checks in at least every 20 s


def load_config():
    return settings.load()


# --- Studio plugin upkeep --------------------------------------------------------

PLUGIN_SRC = system.resource("plugin", "Studiocord.lua")
LEGACY_PLUGIN_NAMES = ["Studio" + "RPC.lua"]


def ensure_plugin(cfg):
    """Copy the helper plugin into Studio's Plugins folder(s) if missing or outdated.

    Covers Studio/Vinegar reinstalls and the file being deleted by hand.
    Returns the list of paths that were (re)installed.
    """
    try:
        src = PLUGIN_SRC.read_bytes()
    except OSError as e:
        log.warning("Plugin source missing (%s); can't install it", e)
        return []
    installed = []
    for plugins in system.plugin_dirs():
        dst = plugins / "Studiocord.lua"
        for old_name in LEGACY_PLUGIN_NAMES:  # from before the rename
            try:
                (dst.parent / old_name).unlink()
                log.info("Removed old plugin %s", dst.parent / old_name)
            except FileNotFoundError:
                pass
            except OSError as e:
                log.warning("Couldn't remove old plugin %s: %s", old_name, e)
        try:
            if dst.exists() and dst.read_bytes() == src:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_suffix(".lua.tmp")
            tmp.write_bytes(src)
            os.replace(tmp, dst)
            installed.append(str(dst))
            log.info("Installed Studio plugin -> %s", dst)
        except OSError as e:
            log.warning("Couldn't install plugin to %s: %s", dst, e)
    return installed


# --- presence building -------------------------------------------------------

def _clip(s, n=128):
    s = s.strip()
    if len(s) < 2:
        s += "  "
    return s if len(s) <= n else s[: n - 1] + "…"


# Rich Presence image names in the Discord application (README lists them).
SCRIPT_ICONS = {"Script": "script", "LocalScript": "localscript", "ModuleScript": "modulescript"}
DEFAULT_LARGE = "studio"


def build_activity(state: StudioState, lookup: PlaceLookup, cfg, studio_started: float,
                   script: tuple | None = None):
    """Returns the Discord activity dict for the current Studio state (or None to hide)."""
    if not state.has_place:
        if not cfg["show_idle"]:
            return None
        return {"details": "On the start page",
                "timestamps": {"start": int(studio_started)},
                "assets": {"large_image": DEFAULT_LARGE, "large_text": WATERMARK}}

    info = lookup.get(state.place_id, state.universe_id) if state.place_id else None
    if state.local_name:
        name = state.local_name
    elif not state.published:
        base = info.name if info and info.name else None  # the template's name
        name = f"a new place ({base})" if base else "a new place"
    else:
        # Public title if Roblox shares it, else the name Studio shows (private games).
        studio_name = script[6] if script and len(script) > 6 else ""
        if studio_name in ("Place1", "Place", "Game"):  # Studio's defaults, not a real name
            studio_name = ""
        name = (info.name if info and info.name else None) or studio_name or None

    if cfg["show_place_name"] and name:
        details = f"Editing {name}"
    else:
        details = "Editing a place"

    if state.game_state.startswith("Play"):
        status, small, small_text = "Playtesting", "playtest", "Playtesting"
    elif state.doc_type == DOC_SCRIPT:
        status, small, small_text = "Editing a script", "script", "Scripting"
        if cfg["show_script_name"] and script and script[0]:
            name_, path_, cls_, lines_ = script[:4]
            status = f"Editing {name_}"
            if cfg["show_line_count"] and lines_ is not None:
                status += f" ({lines_:,} line{'' if lines_ == 1 else 's'})"
            small_text = f"{path_} ({cls_})" if cls_ else path_
            small = SCRIPT_ICONS.get(cls_, "script")
    else:
        status, small, small_text = "Building", "place", "Building"
    if cfg["show_team_create"] and state.team_create:
        status += " · Team Create"

    act = {
        "details": _clip(details),
        "state": _clip(status),
        "timestamps": {"start": int(state.place_opened_at or studio_started)},
        "assets": {"small_image": small, "small_text": small_text},
    }
    # Big image: the game's icon when we have one, otherwise Roblox Studio's.
    # Its hover text is the only place the watermark shows up.
    if cfg["show_place_name"] and info and info.icon_url and state.published:
        act["assets"]["large_image"] = info.icon_url
        act["assets"]["large_text"] = _clip(f"{name or 'Roblox Studio'} · {WATERMARK}")
    else:
        act["assets"]["large_image"] = DEFAULT_LARGE
        act["assets"]["large_text"] = _clip(f"{name} · {WATERMARK}" if cfg["show_place_name"] and name
                                            else WATERMARK)
    # Team Create session as a Discord party: "3 of 3", and collaborators who also run
    # Studiocord share the party id, so Discord shows their avatars.
    collaborators = script[5] if script and len(script) > 5 else 0
    if cfg.get("show_party", True) and state.team_create and state.place_id and collaborators > 1:
        pid = hashlib.sha256(f"studiocord-tc-{state.place_id}".encode()).hexdigest()[:32]
        act["party"] = {"id": pid, "size": [collaborators, collaborators]}
    if cfg["show_button"] and cfg["show_place_name"] and state.published and info and info.public_url:
        act["buttons"] = [{"label": "View on Roblox", "url": info.public_url}]
    return act


def describe(act):
    if act is None:
        return "(no presence)"
    s = f"{act.get('details')} | {act.get('state', '')}"
    large = act.get("assets", {}).get("large_image")
    if large:
        s += " [icon]" if large.startswith("http") else f" [{large}]"
    small = act.get("assets", {}).get("small_image")
    if small:
        s += f" ({small})"
    if act.get("party"):
        s += " [party {0} of {1}]".format(*act["party"]["size"])
    if act.get("buttons"):
        s += f" [button → {act['buttons'][0]['url']}]"
    return s


def short_text(act):
    """Presence as a person would read it on Discord."""
    if not isinstance(act, dict):
        return ""
    return " · ".join(x for x in (act.get("details"), act.get("state", "").strip()) if x)


# --- main loop -----------------------------------------------------------------

@dataclass
class Status:
    studio_running: bool = False
    discord: str = "unknown"        # "connected", "detected", "not detected", "dry run"
    discord_user: str = ""
    discord_error: str = ""
    plugin_seen: bool = False
    plugin_folders: int = 0
    presence: str = ""              # what Discord currently shows ("" = nothing)
    paused: bool = False

    def key(self):
        return (self.studio_running, self.discord, self.discord_error, self.plugin_seen,
                self.plugin_folders, self.presence, self.paused)


class Daemon:
    def __init__(self, cfg, dry_run=False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.discord = DiscordIPC(cfg["client_id"])
        self.lookup = PlaceLookup()
        self.script = script_server.ScriptInfo()
        self.followers: dict[str, tuple[LogFollower, StudioState, float]] = {}
        self.sent = object()          # last activity sent (sentinel = unknown)
        self.last_send = 0.0
        self.last_connect_try = 0.0
        self.running = True
        self.paused = False
        self.status = Status()
        self.on_change = lambda: None   # called (from the loop's thread) when status changes
        self.commands = {}              # extra /app/<cmd> handlers (the tray adds some)
        self._lock = threading.Lock()
        self._pending_cfg = None

    def stop(self, *_):
        self.running = False

    def set_paused(self, paused: bool):
        self.paused = paused
        self.last_send = 0  # apply right away

    def apply_config(self, cfg):
        """Take new settings (from the settings window); applied on the next tick."""
        with self._lock:
            self._pending_cfg = dict(cfg)

    def _take_pending_config(self):
        with self._lock:
            new, self._pending_cfg = self._pending_cfg, None
        if new is None:
            return
        old = self.cfg
        self.cfg = new
        if new["client_id"] != old["client_id"]:
            if self.discord.connected:
                try:
                    self.discord.set_activity(None)
                except DiscordError:
                    pass
            self.discord.close()
            self.discord = DiscordIPC(new["client_id"])
            self.last_connect_try = 0
        if settings.logs_dir(new) != settings.logs_dir(old):
            self._drop_logs()
        self.sent, self.last_send = object(), 0  # re-send with the new settings
        log.info("Settings updated")

    def _sync_logs(self, earliest_start):
        """Follow every Studio log written since Studio started."""
        pattern = os.path.join(settings.logs_dir(self.cfg), "*_Studio_*_last.log")
        for path in glob.glob(pattern):
            if path in self.followers:
                continue
            try:
                if os.path.getmtime(path) < earliest_start - 60:
                    continue
                self.followers[path] = (LogFollower(path), StudioState(), 0.0)
                log.info("Following %s", os.path.basename(path))
            except OSError:
                continue

    def _update_states(self):
        """Read new lines. Returns the state that changed most recently."""
        now = time.time()
        for path, (fol, st, changed_at) in list(self.followers.items()):
            try:
                lines = fol.read_lines()
            except OSError:
                lines = []
            for line in lines:
                if apply_line(st, line):
                    changed_at = now
            self.followers[path] = (fol, st, changed_at)
        alive = [(c, st) for f, st, c in self.followers.values() if not st.exiting]
        if not alive:
            return None
        # Prefer an instance with a place open, then the most recently active one.
        return max(alive, key=lambda t: (t[1].has_place, t[0]))[1]

    def _drop_logs(self):
        for fol, _, _ in self.followers.values():
            fol.close()
        self.followers.clear()

    def _send(self, act):
        if act == self.sent:
            return
        now = time.time()
        if now - self.last_send < MIN_SEND_INTERVAL:
            return  # coalesce; we'll send the latest state on a later tick
        if self.dry_run:
            print(time.strftime("%H:%M:%S"), describe(act), flush=True)
            self.sent, self.last_send = act, now
            return
        if not self.discord.connected:
            if act is None:
                self.sent = None
                return
            if now - self.last_connect_try < RECONNECT_INTERVAL:
                return
            self.last_connect_try = now
            try:
                self.discord.connect()
                self.status.discord_error = ""
            except DiscordError as e:
                self.status.discord_error = str(e)
                log.info("%s; retrying in %ds", e, RECONNECT_INTERVAL)
                return
        try:
            self.discord.set_activity(act)
            self.sent, self.last_send = act, now
            log.info("Presence: %s", describe(act))
            if act is None:
                self.discord.close()  # nothing to show: let go of Discord entirely
        except DiscordError as e:
            log.warning("Discord update failed: %s", e)
            self.status.discord_error = str(e)
            self.discord.close()
            self.sent = object()

    def _update_status(self, studio_running, discord_found):
        st = self.status
        before = st.key()
        st.studio_running = studio_running
        st.paused = self.paused
        if self.dry_run:
            st.discord = "dry run"
        elif self.discord.connected:
            st.discord = "connected"
            st.discord_user = (self.discord.user or {}).get("username", "")
            st.discord_error = ""
        else:
            st.discord = "detected" if discord_found else "not detected"
        st.plugin_seen = time.time() - self.script.snapshot()[4] < PLUGIN_STALE_AFTER
        st.presence = short_text(self.sent)
        if st.key() != before:
            try:
                self.on_change()
            except Exception:  # never let the UI break the loop
                log.exception("status callback failed")

    def run(self, install_signals=True):
        """The main loop. Returns False if another Studiocord is already running."""
        if install_signals:
            signal.signal(signal.SIGTERM, self.stop)
            signal.signal(signal.SIGINT, self.stop)
        if script_server.port_in_use(self.cfg["plugin_port"]):
            # Our port doubles as a single-instance lock (autostart + manual start).
            log.info("Studiocord is already running; exiting")
            self.running = False
            return False
        log.info("%s v%s - watching for Roblox Studio (logs: %s)", WATERMARK, __version__,
                 settings.logs_dir(self.cfg))
        script_server.start(self.script, self.cfg["plugin_port"],
                            {"quit": self.stop, **self.commands})
        studio_was_running = False
        last_scan, starts, discord_found = 0.0, [], False
        last_plugin_check = 0.0
        while self.running:
            self._take_pending_config()
            now = time.time()
            if now - last_plugin_check >= 15:
                if self.cfg["auto_install_plugin"] and self.cfg["show_script_name"]:
                    ensure_plugin(self.cfg)
                self.status.plugin_folders = len(system.plugin_dirs())
                last_plugin_check = now
            if now - last_scan >= 2:
                starts, last_scan = system.studio_start_times(), now
                discord_found = system.discord_running()
            if starts:
                if not studio_was_running:
                    log.info("Roblox Studio started")
                    studio_was_running = True
                self._sync_logs(min(starts))
                state = self._update_states()
                act = (build_activity(state, self.lookup, self.cfg, min(starts),
                                      self.script.snapshot()) if state else None)
                self._send(None if self.paused else act)
            else:
                if studio_was_running:
                    log.info("Roblox Studio closed")
                    studio_was_running = False
                    self._drop_logs()
                    self.script.clear()
                    self.last_send = 0  # clear right away
                self._send(None)
            self._update_status(bool(starts), discord_found)
            time.sleep(self.cfg["poll_seconds"])
        if self.discord.connected:
            try:
                self.discord.set_activity(None)
            except DiscordError:
                pass
            self.discord.close()
        log.info("Stopped")
        return True


def replay(path, cfg):
    """Print how presence would change over a whole log (for testing)."""
    st, lookup = StudioState(), PlaceLookup()
    last = None
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    for line in lines:
        if apply_line(st, line):
            act = build_activity(st, lookup, cfg, 0)
            d = describe(act)
            if d != last:
                print(line[11:19], d)
                last = d
    # second pass now that lookups had time to finish
    time.sleep(3)
    print("final (with lookups):", describe(build_activity(st, lookup, cfg, 0)))


def _gui_available():
    try:
        import PySide6.QtWidgets  # noqa: F401
        return True
    except ImportError:
        return False


def diagnose(cfg):
    """Print what Studiocord sees; handy for bug reports (and the CI smoke test)."""
    logs = settings.logs_dir(cfg)
    print(f"{WATERMARK} v{__version__} on {sys.platform}, frozen={system.FROZEN}")
    print("config:", CONFIG_PATH, "(exists)" if CONFIG_PATH.exists() else "(defaults)")
    print("logs dir:", logs, "(exists)" if os.path.isdir(logs) else "(missing)")
    print("studio logs:", len(glob.glob(os.path.join(logs, "*_Studio_*_last.log"))))
    print("studio processes:", len(system.studio_start_times()))
    print("plugin source:", PLUGIN_SRC, "(ok)" if PLUGIN_SRC.exists() else "(MISSING)")
    print("plugin folders:", [str(p) for p in system.plugin_dirs()] or "none found")
    print("discord running:", system.discord_running(), system.discord_endpoints())
    print("display:", system.has_display(), "| gui toolkit:", _gui_available())
    print("log file:", system.log_file() or "stdout")
    print("ssl cert file:", os.environ.get("SSL_CERT_FILE") or "(Python default)")
    ok, detail = system.https_works()
    print("https:", "ok" if ok else "FAILED", detail)
    return 0 if PLUGIN_SRC.exists() and ok else 1


def _setup_logging(verbose):
    handlers = []
    if sys.stdout is not None:  # windowed Windows builds have no console
        handlers.append(logging.StreamHandler(sys.stdout))
    path = system.log_file()
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.handlers.RotatingFileHandler(
                path, maxBytes=1_000_000, backupCount=1, encoding="utf-8"))
        except OSError:
            pass
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers or [logging.NullHandler()])


def main():
    system.fix_ssl_certificates()  # before anything talks HTTPS
    # If Qt or ctypes ever crash hard, record where. Windowed builds have no stderr,
    # so they write it next to the log file instead.
    import faulthandler
    if sys.stderr is not None:
        faulthandler.enable()
    elif system.log_file() is not None:
        try:
            crash = system.log_file().with_name("crash.log")
            crash.parent.mkdir(parents=True, exist_ok=True)
            faulthandler.enable(open(crash, "a", encoding="utf-8"))  # noqa: SIM115 - kept open
        except OSError:
            pass
    ap = argparse.ArgumentParser(prog="studiocord", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--headless", action="store_true", help="no tray icon or windows")
    ap.add_argument("--autostart", action="store_true", help="started at login: don't open windows")
    ap.add_argument("--dry-run", action="store_true", help="print presence, don't touch Discord")
    ap.add_argument("--replay", metavar="LOG", help="replay a Studio log file and exit")
    ap.add_argument("--diagnose", action="store_true", help="show what Studiocord finds, then exit")
    ap.add_argument("--quit", action="store_true", help="ask a running Studiocord to quit")
    ap.add_argument("--install", action="store_true", help="(Linux build) install and start")
    ap.add_argument("--uninstall", action="store_true", help="(Linux build) remove Studiocord")
    ap.add_argument("--gui-selftest", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"{WATERMARK}, v{__version__}")
    args = ap.parse_args()

    gui = _gui_available() and system.has_display() and not args.headless

    if not system.IS_WINDOWS:
        from . import installer
        # The downloaded Linux build is its own installer.
        wants_installer = system.FROZEN and len(sys.argv) == 1 and not installer.is_installed_copy()
        if args.uninstall:
            sys.exit(installer.uninstall_cli())
        if args.install or wants_installer:
            if gui and not args.install:
                from .install_gui import run_installer_gui
                sys.exit(run_installer_gui())
            sys.exit(installer.install_cli())

    _setup_logging(args.verbose)
    cfg = load_config()
    if args.quit:
        sent = script_server.send_command("quit", cfg["plugin_port"])
        for _ in range(40):  # wait until it's gone (it clears the Discord status first)
            if not script_server.port_in_use(cfg["plugin_port"]):
                break
            time.sleep(0.25)
        sys.exit(0 if sent else 1)
    if args.diagnose:
        sys.exit(diagnose(cfg))
    if args.replay:
        replay(args.replay, cfg)
        return
    if args.gui_selftest:
        from .tray import selftest
        sys.exit(selftest())

    if script_server.port_in_use(cfg["plugin_port"]):
        # Already running: bring up its settings instead of starting a second copy.
        if gui and not args.autostart:
            script_server.send_command("show-settings", cfg["plugin_port"])
        log.info("Studiocord is already running")
        return

    daemon = Daemon(cfg, dry_run=args.dry_run)
    if gui:
        from .tray import run_tray
        sys.exit(run_tray(daemon, show_settings=not args.autostart))
    daemon.run()


if __name__ == "__main__":
    main()
