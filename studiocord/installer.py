"""Self-installer for the standalone Linux build.

Running the downloaded `Studiocord-Installer-Linux` file copies it to
~/.local/share/studiocord/studiocord, registers it to start at login (systemd user
service, or an XDG autostart entry when systemd isn't available) and starts it.
`--uninstall` undoes all of that. (Windows uses the Inno Setup installer instead.)
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import WATERMARK, __version__
from .system import config_path, plugin_dirs, resource

INSTALL_DIR = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "studiocord"
INSTALLED_BIN = INSTALL_DIR / "studiocord"
UNIT = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user" / "studiocord.service"
AUTOSTART = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart" / "studiocord.desktop"

UNIT_TEXT = """[Unit]
Description=Studiocord - Discord Rich Presence for Roblox Studio (by JonasTheCoderX)
After=graphical-session.target

[Service]
Type=simple
ExecStart={bin}
Restart=on-failure
RestartSec=15

[Install]
WantedBy=default.target
"""

DESKTOP_TEXT = """[Desktop Entry]
Type=Application
Name=Studiocord
Comment=Discord Rich Presence for Roblox Studio
Exec={bin}
X-GNOME-Autostart-enabled=true
NoDisplay=true
"""


def is_installed_copy() -> bool:
    try:
        return Path(sys.executable).resolve() == INSTALLED_BIN.resolve()
    except OSError:
        return False


def _systemctl(*args, check=False):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, check=check)


def _has_systemd() -> bool:
    if not shutil.which("systemctl"):
        return False
    return _systemctl("show-environment").returncode == 0  # user manager reachable


def _notify(msg):
    print(msg)
    # Also show it when the installer was double-clicked without a terminal.
    if not sys.stdout.isatty() and shutil.which("notify-send"):
        subprocess.run(["notify-send", "Studiocord", msg], check=False)


def install():
    src = Path(sys.executable).resolve()
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    systemd = _has_systemd()
    if systemd:
        _systemctl("stop", "studiocord")  # an older version may be running

    tmp = INSTALLED_BIN.with_suffix(".new")
    shutil.copy2(src, tmp)
    tmp.chmod(0o755)
    os.replace(tmp, INSTALLED_BIN)

    cfg = config_path()
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(resource("config.example.toml"), cfg)
        except OSError:
            pass

    if systemd:
        UNIT.parent.mkdir(parents=True, exist_ok=True)
        UNIT.write_text(UNIT_TEXT.format(bin=INSTALLED_BIN))
        AUTOSTART.unlink(missing_ok=True)
        _systemctl("daemon-reload")
        _systemctl("enable", "studiocord")
        r = _systemctl("restart", "studiocord")
        if r.returncode != 0:
            _notify(f"Installed, but starting the service failed:\n{r.stderr.strip()}")
            return 1
        how = "systemd user service 'studiocord' (journalctl --user -u studiocord -f)"
    else:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text(DESKTOP_TEXT.format(bin=INSTALLED_BIN))
        subprocess.Popen([str(INSTALLED_BIN)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        how = f"autostart entry {AUTOSTART}"

    _notify(f"{WATERMARK} v{__version__} installed and running.\n"
            f"Program: {INSTALLED_BIN}\nStarts at login via {how}\n"
            f"Settings: {cfg}\nUninstall: {INSTALLED_BIN} --uninstall")
    return 0


def uninstall():
    if shutil.which("systemctl"):
        _systemctl("disable", "--now", "studiocord")
    UNIT.unlink(missing_ok=True)
    AUTOSTART.unlink(missing_ok=True)
    if shutil.which("systemctl"):
        _systemctl("daemon-reload")
    for d in plugin_dirs():
        (d / "Studiocord.lua").unlink(missing_ok=True)
    INSTALLED_BIN.unlink(missing_ok=True)
    try:
        INSTALL_DIR.rmdir()
    except OSError:
        pass
    _notify(f"Studiocord uninstalled. Your settings were kept in {config_path().parent}.")
    return 0
