"""Studiocord: Discord Rich Presence for Roblox Studio running under Vinegar.

Runs forever in the background. While Roblox Studio runs, it follows Studio's own
log files and shows what you're doing on Discord. When Studio closes, it clears it.

    python3 -m studiocord               run (normally via the systemd user service)
    python3 -m studiocord --dry-run     print presence instead of sending it to Discord
    python3 -m studiocord --replay LOG  replay a Studio log file and print each presence
"""

import argparse
import glob
import logging
import os
import signal
import sys
import time
import tomllib
from pathlib import Path

from .discord_ipc import DiscordError, DiscordIPC
from .roblox_api import PlaceLookup
from . import WATERMARK, __version__, script_server
from .studio_log import DOC_SCRIPT, LogFollower, StudioState, apply_line

log = logging.getLogger("studiocord")

CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "studiocord" / "config.toml"

DEFAULTS = {
    # Discord application whose name/icon is shown ("Playing <name>").
    # Default: Vinegar's own "Roblox Studio" app, which has the "place"/"script" images.
    "client_id": "1159891020956323923",
    "logs_dir": "~/.var/app/org.vinegarhq.Vinegar/data/vinegar/appdata/Roblox/logs",
    "show_place_name": True,     # False = just "Editing a place", no name or icon
    "show_button": True,         # "View on Roblox" button for published places
    "show_team_create": True,
    "show_script_name": True,
    "show_line_count": True,     # "Editing Main (120 lines)"    # needs the Studio plugin (install.sh puts it in place)
    "plugin_port": 47231,
    "auto_install_plugin": True, # put the plugin back if it is missing/outdated
    "vinegar_prefixes": "~/.var/app/org.vinegarhq.Vinegar/data/vinegar/prefixes",
    "show_idle": True,           # show presence on the start page (no place open)
    "poll_seconds": 1.0,
}

MIN_SEND_INTERVAL = 4.0   # Discord allows ~5 updates per 20 s
RECONNECT_INTERVAL = 15.0


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "rb") as f:
            cfg.update(tomllib.load(f))
    cfg["logs_dir"] = os.path.expanduser(cfg["logs_dir"])
    return cfg


# --- Studio plugin upkeep --------------------------------------------------------

PLUGIN_SRC = Path(__file__).resolve().parent.parent / "plugin" / "Studiocord.lua"
LEGACY_PLUGIN_NAMES = ["Studio" + "RPC.lua"]


def ensure_plugin(cfg):
    """Copy the helper plugin into every Vinegar prefix that lacks it (or has an old copy).

    Covers Studio/Vinegar reinstalls (new prefix) and the file being deleted by hand.
    Returns the list of paths that were (re)installed.
    """
    try:
        src = PLUGIN_SRC.read_bytes()
    except OSError as e:
        log.warning("Plugin source missing (%s); can't install it", e)
        return []
    installed = []
    prefixes = os.path.expanduser(cfg["vinegar_prefixes"])
    # The prefix's AppData/Local exists as soon as Vinegar has set Wine up.
    for local in glob.glob(os.path.join(prefixes, "*", "drive_c", "users", "*", "AppData", "Local")):
        if os.path.basename(os.path.dirname(os.path.dirname(local))) == "Public":
            continue
        dst = Path(local) / "Roblox" / "Plugins" / "Studiocord.lua"
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


# --- Studio process detection ----------------------------------------------

def _boot_time():
    with open("/proc/stat") as f:
        for line in f:
            if line.startswith("btime "):
                return int(line.split()[1])
    return 0


_BOOT = _boot_time()
_TICKS = os.sysconf("SC_CLK_TCK")


def studio_start_times():
    """Unix start times of all running RobloxStudioBeta.exe processes."""
    starts = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().lower()
            if b"robloxstudiobeta.exe" not in cmd or b"crashhandler" in cmd:
                continue
            # Skip the bwrap/vinegar wrappers; the Wine process is the real one.
            if cmd.startswith((b"/usr/bin/bwrap", b"vinegar")):
                continue
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            starts.append(_BOOT + int(fields[19]) / _TICKS)
        except (OSError, IndexError, ValueError):
            continue
    return starts


# --- presence building -------------------------------------------------------

def _clip(s, n=128):
    s = s.strip()
    if len(s) < 2:
        s += "  "
    return s if len(s) <= n else s[: n - 1] + "…"


def build_activity(state: StudioState, lookup: PlaceLookup, cfg, studio_started: float,
                   script: tuple | None = None):
    """Returns the Discord activity dict for the current Studio state (or None to hide)."""
    if not state.has_place:
        if not cfg["show_idle"]:
            return None
        return {"details": "On the start page",
                "timestamps": {"start": int(studio_started)}}

    info = lookup.get(state.place_id, state.universe_id) if state.place_id else None
    if state.local_name:
        name = state.local_name
    elif not state.published:
        base = info.name if info and info.name else None
        name = f"a new place ({base})" if base else "a new place"
    else:
        name = info.name if info and info.name else None

    if cfg["show_place_name"] and name:
        details = f"Editing {name}"
    else:
        details = "Editing a place"

    if state.game_state.startswith("Play"):
        status, small, small_text = "Playtesting", "place", "Playtesting"
    elif state.doc_type == DOC_SCRIPT:
        status, small, small_text = "Editing a script", "script", "Scripting"
        if cfg["show_script_name"] and script and script[0]:
            name_, path_, cls_, lines_ = script[:4]
            status = f"Editing {name_}"
            if cfg["show_line_count"] and lines_ is not None:
                status += f" ({lines_:,} line{'' if lines_ == 1 else 's'})"
            small_text = f"{path_} ({cls_})" if cls_ else path_
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
    if cfg["show_place_name"] and info and info.icon_url and state.published:
        act["assets"]["large_image"] = info.icon_url
        # Hover text of the place icon; the watermark only shows up there.
        act["assets"]["large_text"] = _clip(f"{name or 'Roblox Studio'} · {WATERMARK}")
    if cfg["show_button"] and cfg["show_place_name"] and state.published and info and info.public_url:
        act["buttons"] = [{"label": "View on Roblox", "url": info.public_url}]
    return act


def describe(act):
    if act is None:
        return "(no presence)"
    s = f"{act.get('details')} | {act.get('state', '')}"
    if "large_image" in act.get("assets", {}):
        s += " [icon]"
    if act.get("buttons"):
        s += f" [button → {act['buttons'][0]['url']}]"
    return s


# --- main loop -----------------------------------------------------------------

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

    def stop(self, *_):
        self.running = False

    def _sync_logs(self, earliest_start):
        """Follow every Studio log written since Studio started."""
        pattern = os.path.join(self.cfg["logs_dir"], "*_Studio_*_last.log")
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
            except DiscordError as e:
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
            self.discord.close()
            self.sent = object()

    def run(self):
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info("%s v%s - watching for Roblox Studio (logs: %s)", WATERMARK, __version__,
                 self.cfg["logs_dir"])
        if self.cfg["show_script_name"]:
            script_server.start(self.script, self.cfg["plugin_port"])
        studio_was_running = False
        last_proc_scan, starts = 0.0, []
        last_plugin_check = 0.0
        while self.running:
            now = time.time()
            if self.cfg["auto_install_plugin"] and self.cfg["show_script_name"] \
                    and now - last_plugin_check >= 15:
                ensure_plugin(self.cfg)
                last_plugin_check = now
            if now - last_proc_scan >= 2:
                starts, last_proc_scan = studio_start_times(), now
            if starts:
                if not studio_was_running:
                    log.info("Roblox Studio started")
                    studio_was_running = True
                self._sync_logs(min(starts))
                state = self._update_states()
                act = (build_activity(state, self.lookup, self.cfg, min(starts),
                                      self.script.snapshot()) if state else None)
                self._send(act)
            else:
                if studio_was_running:
                    log.info("Roblox Studio closed")
                    studio_was_running = False
                    self._drop_logs()
                    with self.script.lock:
                        self.script.name = self.script.path = self.script.cls = ""
                        self.script.lines = None
                    self.last_send = 0  # clear right away
                self._send(None)
            time.sleep(self.cfg["poll_seconds"])
        if self.discord.connected:
            try:
                self.discord.set_activity(None)
            except DiscordError:
                pass
            self.discord.close()
        log.info("Stopped")


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


def main():
    ap = argparse.ArgumentParser(prog="studiocord", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print presence, don't touch Discord")
    ap.add_argument("--replay", metavar="LOG", help="replay a Studio log file and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--version", action="version", version=f"{WATERMARK}, v{__version__}")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    cfg = load_config()
    if args.replay:
        replay(args.replay, cfg)
        return
    Daemon(cfg, dry_run=args.dry_run).run()


if __name__ == "__main__":
    main()
