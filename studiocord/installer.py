"""Install / uninstall logic for the standalone Linux build.

The downloaded `Studiocord-Installer-Linux` file is the app itself. Run from anywhere
except its install location, it acts as the installer (window via install_gui.py, or
text with --install): it copies itself to ~/.local/share/studiocord/studiocord, adds
an app-menu entry and a login autostart entry (the tray icon must run inside the
desktop session), and starts. (Windows uses the Inno Setup installer instead.)
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import WATERMARK, __version__, script_server, settings
from .system import AUTOSTART_DESKTOP, config_path, plugin_dirs, resource, set_autostart

DATA = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
INSTALL_DIR = DATA / "studiocord"
INSTALLED_BIN = INSTALL_DIR / "studiocord"
APP_DESKTOP = DATA / "applications" / "studiocord.desktop"
ICON = DATA / "icons" / "hicolor" / "256x256" / "apps" / "studiocord.png"
LEGACY_UNIT = CONFIG / "systemd" / "user" / "studiocord.service"  # older/source installs


class InstallError(Exception):
    pass


def is_installed_copy() -> bool:
    try:
        return Path(sys.executable).resolve() == INSTALLED_BIN.resolve()
    except OSError:
        return False


def is_installed() -> bool:
    return INSTALLED_BIN.exists()


def _clean_env():
    """Environment for starting the installed copy from this (frozen) process.

    Drops what PyInstaller and Qt set up for *this* process; it points into our own
    temporary folder, which is deleted when the installer exits.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("_PYI", "_MEI", "QT_", "PYSIDE")) and k not in ("PYTHONPATH",)}
    if "LD_LIBRARY_PATH_ORIG" in os.environ:
        env["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH_ORIG"]
    else:
        env.pop("LD_LIBRARY_PATH", None)
    env.pop("LD_LIBRARY_PATH_ORIG", None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def _systemctl(*args):
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                              timeout=20, env=_clean_env())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def stop_running(port=None) -> bool:
    """Stop a running Studiocord (installed app, older systemd service, source copy)."""
    port = port or settings.load()["plugin_port"]
    if LEGACY_UNIT.exists():
        _systemctl("disable", "--now", "studiocord")
    if script_server.port_in_use(port):
        script_server.send_command("quit", port)
        for _ in range(40):
            if not script_server.port_in_use(port):
                break
            time.sleep(0.25)
    return not script_server.port_in_use(port)


def start_installed(show_settings=True):
    args = [str(INSTALLED_BIN)] + ([] if show_settings else ["--autostart"])
    subprocess.Popen(args, start_new_session=True, env=_clean_env(), cwd=str(Path.home()),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install(autostart=True, start=True) -> str:
    """Install (or update) Studiocord for this user. Returns a summary; raises InstallError."""
    src = Path(sys.executable).resolve()
    if not getattr(sys, "frozen", False):
        raise InstallError("Only the standalone build can install itself "
                           "(from source, use install.sh).")
    if not stop_running():
        raise InstallError("A running Studiocord didn't stop. Quit it from its tray icon "
                           "and try again.")

    # The program itself
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INSTALLED_BIN.with_suffix(".new")
    shutil.copy2(src, tmp)
    tmp.chmod(0o755)
    os.replace(tmp, INSTALLED_BIN)

    # Icon + app-menu entry (so it can be reopened after "Quit")
    ICON.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(resource("installer", "studiocord.png"), ICON)
    except OSError:
        pass
    APP_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
    APP_DESKTOP.write_text(
        "[Desktop Entry]\nType=Application\nName=Studiocord\n"
        "Comment=Discord Rich Presence for Roblox Studio\n"
        f'Exec="{INSTALLED_BIN}"\nIcon=studiocord\nTerminal=false\n'
        "Categories=Development;Utility;\nKeywords=Roblox;Discord;Presence;RPC;\n"
        f'Actions=uninstall;\n\n[Desktop Action uninstall]\nName=Uninstall Studiocord\n'
        f'Exec="{INSTALLED_BIN}" --uninstall\n')

    # Older versions / source installs used a systemd service; the app replaces it.
    if LEGACY_UNIT.exists():
        LEGACY_UNIT.unlink(missing_ok=True)
        _systemctl("daemon-reload")

    # Start at login. set_autostart() uses sys.executable, which is the installer file
    # here, so write the entry for the installed copy ourselves.
    if autostart:
        AUTOSTART_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART_DESKTOP.write_text(
            "[Desktop Entry]\nType=Application\nName=Studiocord\n"
            "Comment=Discord Rich Presence for Roblox Studio\n"
            f'Exec="{INSTALLED_BIN}" --autostart\nIcon=studiocord\n'
            "X-GNOME-Autostart-enabled=true\n")
    else:
        set_autostart(False)

    cfg = config_path()
    if not cfg.exists():
        settings.save(settings.DEFAULTS)

    if start:
        start_installed(show_settings=False)

    return (f"{WATERMARK} v{__version__} is installed"
            f"{' and running' if start else ''}.\n\n"
            f"Look for its icon in the system tray. You can also find it in your app menu.\n\n"
            f"Program: {INSTALLED_BIN}\n"
            f"Starts at login: {'yes' if autostart else 'no'}\n"
            f"Settings: {cfg}")


def uninstall() -> str:
    stop_running()
    for p in (LEGACY_UNIT, AUTOSTART_DESKTOP, APP_DESKTOP, ICON):
        p.unlink(missing_ok=True)
    _systemctl("daemon-reload")
    for d in plugin_dirs():
        (d / "Studiocord.lua").unlink(missing_ok=True)
    INSTALLED_BIN.unlink(missing_ok=True)
    try:
        INSTALL_DIR.rmdir()
    except OSError:
        pass
    return f"Studiocord is uninstalled.\nYour settings were kept in {config_path().parent}."


def _notify(msg):
    print(msg)
    # Also show it when started without a terminal (e.g. double-clicked).
    if not sys.stdout or not sys.stdout.isatty():
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", "Studiocord", msg], check=False, env=_clean_env())


def install_cli() -> int:
    try:
        msg = install()
    except (InstallError, OSError) as e:
        _notify(f"Installing Studiocord failed: {e}")
        return 1
    _notify(msg + f"\nUninstall: {INSTALLED_BIN} --uninstall")
    return 0


def uninstall_cli() -> int:
    _notify(uninstall())
    return 0
