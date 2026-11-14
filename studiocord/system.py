"""Everything that differs between Windows and Linux (Vinegar) lives here."""

import glob
import os
import sys
import time
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
FROZEN = getattr(sys, "frozen", False)  # running from a PyInstaller build


def resource(*parts) -> Path:
    """Path to a file shipped with Studiocord (works from source and from a build)."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


# --- folders -----------------------------------------------------------------------

def _local_appdata():
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")


def config_path() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Studiocord" / "config.toml"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "studiocord" / "config.toml"


def log_file() -> Path | None:
    """Where to write our own log (None = stdout only, e.g. under systemd)."""
    if IS_WINDOWS:
        return _local_appdata() / "Studiocord" / "studiocord.log"
    if FROZEN:  # the standalone app always keeps a log file ("Open log" in the tray)
        state = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
        return state / "studiocord" / "studiocord.log"
    return None


def has_display() -> bool:
    return IS_WINDOWS or bool(os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"))


# --- start at login ----------------------------------------------------------------
# Only offered for the standalone builds: a source checkout is started however its
# owner set it up (e.g. install.sh's systemd service).

AUTOSTART_DESKTOP = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "studiocord.desktop"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_supported() -> bool:
    return FROZEN


def autostart_enabled() -> bool:
    if IS_WINDOWS:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                winreg.QueryValueEx(k, "Studiocord")
                return True
        except OSError:
            return False
    return AUTOSTART_DESKTOP.exists()


def set_autostart(enabled: bool):
    exe = sys.executable
    if IS_WINDOWS:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, "Studiocord", 0, winreg.REG_SZ, f'"{exe}" --autostart')
            else:
                try:
                    winreg.DeleteValue(k, "Studiocord")
                except FileNotFoundError:
                    pass
        return
    if enabled:
        AUTOSTART_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART_DESKTOP.write_text(
            "[Desktop Entry]\nType=Application\nName=Studiocord\n"
            "Comment=Discord Rich Presence for Roblox Studio\n"
            f'Exec="{exe}" --autostart\nIcon=studiocord\nX-GNOME-Autostart-enabled=true\n')
    else:
        AUTOSTART_DESKTOP.unlink(missing_ok=True)


def open_path(path):
    """Open a file or folder with the system's default app."""
    import subprocess
    if IS_WINDOWS:
        os.startfile(str(path))  # noqa: S606 - user-initiated
    else:
        subprocess.Popen(["xdg-open", str(path)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


VINEGAR = Path.home() / ".var" / "app" / "org.vinegarhq.Vinegar" / "data" / "vinegar"


def default_logs_dir() -> str:
    if IS_WINDOWS:
        return str(_local_appdata() / "Roblox" / "logs")
    return str(VINEGAR / "appdata" / "Roblox" / "logs")


def plugin_dirs() -> list[Path]:
    """Studio's local Plugins folder(s) that exist (or whose parent exists)."""
    if IS_WINDOWS:
        roblox = _local_appdata() / "Roblox"
        return [roblox / "Plugins"] if roblox.exists() else []
    # Vinegar: Studio's %LOCALAPPDATA%\Roblox\Plugins inside each Wine prefix. The
    # prefix's AppData/Local exists as soon as Vinegar has set Wine up.
    dirs = []
    pattern = str(VINEGAR / "prefixes" / "*" / "drive_c" / "users" / "*" / "AppData" / "Local")
    for local in glob.glob(pattern):
        if Path(local).parent.parent.name != "Public":
            dirs.append(Path(local) / "Roblox" / "Plugins")
    return dirs


# --- finding Roblox Studio ---------------------------------------------------------

def studio_start_times() -> list[float]:
    """Unix start times of all running RobloxStudioBeta.exe processes."""
    return _studio_windows() if IS_WINDOWS else _studio_linux()


def _studio_linux():
    starts = []
    try:
        boot = next(int(l.split()[1]) for l in open("/proc/stat") if l.startswith("btime "))
    except (OSError, StopIteration):
        boot = 0
    ticks = os.sysconf("SC_CLK_TCK")
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
            starts.append(boot + int(fields[19]) / ticks)
        except (OSError, IndexError, ValueError):
            continue
    return starts


def _studio_windows():
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    k32.CloseHandle.argtypes = [wintypes.HANDLE]

    TH32CS_SNAPPROCESS = 0x2
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    INVALID = wintypes.HANDLE(-1).value

    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID:
        return []
    pids = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == "robloxstudiobeta.exe":
                pids.append(entry.th32ProcessID)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)

    starts = []
    for pid in pids:
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            starts.append(time.time())  # can't read it; at least we know it runs
            continue
        try:
            created, t2, t3, t4 = (wintypes.FILETIME() for _ in range(4))
            if k32.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(t2),
                                   ctypes.byref(t3), ctypes.byref(t4)):
                ft = (created.dwHighDateTime << 32) | created.dwLowDateTime
                starts.append(ft / 10_000_000 - 11_644_473_600)  # FILETIME -> unix
            else:
                starts.append(time.time())
        finally:
            k32.CloseHandle(h)
    return starts


# --- Discord ----------------------------------------------------------------------

def discord_endpoints() -> list[str]:
    """Discord's local RPC endpoints that exist right now."""
    if IS_WINDOWS:
        found = []
        for i in range(10):
            p = rf"\\.\pipe\discord-ipc-{i}"
            if os.path.exists(p):
                found.append(p)
        # os.path.exists on pipes can be unreliable; still try the usual ones.
        return found or [rf"\\.\pipe\discord-ipc-{i}" for i in range(3)]
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    found = []
    for d in ["",                                       # native Discord / Vesktop
              "app/com.discordapp.Discord",             # Flatpak Discord
              ".flatpak/com.discordapp.Discord/xdg-run",
              ".flatpak/dev.vencord.Vesktop/xdg-run",   # Flatpak Vesktop
              "snap.discord"]:                          # Snap Discord
        for i in range(10):
            p = os.path.join(base, d, f"discord-ipc-{i}")
            if os.path.exists(p):
                found.append(p)
    return found


def discord_running() -> bool:
    """Is a Discord client listening for Rich Presence right now?"""
    if IS_WINDOWS:
        return any(os.path.exists(rf"\\.\pipe\discord-ipc-{i}") for i in range(10))
    import socket
    for path in discord_endpoints():
        # The socket file can outlive Discord, so actually knock on the door.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect(path)
                return True
            except OSError:
                continue
    return False


def child_env() -> dict:
    """Environment for starting another Studiocord build (installer, new version).

    A PyInstaller program passes its own temporary unpack folder on to programs it
    starts (_PYI*/_MEI* variables, Qt paths). That folder is deleted when this copy
    quits, and a child Studiocord then fails with "Failed to load Python DLL".
    PYINSTALLER_RESET_ENVIRONMENT tells the child to set itself up from scratch.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("_PYI", "_MEI", "QT_", "PYSIDE")) and k.upper() != "PYTHONPATH"}
    if not IS_WINDOWS:
        if "LD_LIBRARY_PATH_ORIG" in os.environ:
            env["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH_ORIG"]
        else:
            env.pop("LD_LIBRARY_PATH", None)
        env.pop("LD_LIBRARY_PATH_ORIG", None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env
