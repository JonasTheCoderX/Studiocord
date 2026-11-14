"""Builds the standalone Studiocord program with PyInstaller (no Python needed to run it).

    pip install pyinstaller
    python build.py            -> dist/Studiocord-Installer-Linux   (on Linux)
                               -> dist/studiocord.exe               (on Windows; then
                                  installer/studiocord.iss makes Studiocord-Setup.exe)
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

WINDOWS = sys.platform == "win32"
name = "studiocord" if WINDOWS else "Studiocord-Installer-Linux"

cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--clean", "--noconfirm",
       "--name", name,
       "--add-data", f"{ROOT / 'plugin' / 'Studiocord.lua'}:plugin",
       "--add-data", f"{ROOT / 'config.example.toml'}:.",
       *sys.argv[1:]]
if WINDOWS:
    cmd += ["--noconsole", "--icon", str(ROOT / "installer" / "studiocord.ico")]
cmd.append(str(ROOT / "studiocord_entry.py"))
print(" ".join(cmd), flush=True)
sys.exit(subprocess.call(cmd))
