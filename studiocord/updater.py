"""Checks GitHub Releases for a newer Studiocord and installs it.

Only public releases are visible (no login), so this finds nothing while the
repository is private.
"""

import json
import logging
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from . import WATERMARK, __version__, system

log = logging.getLogger("updater")

REPO = "JonasTheCoderX/Studiocord"
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
ASSET = "Studiocord-Setup.exe" if system.IS_WINDOWS else "Studiocord-Installer-Linux"
_UA = {"User-Agent": f"Studiocord/{__version__} ({WATERMARK})",
       "Accept": "application/vnd.github+json"}


@dataclass
class Release:
    version: tuple
    tag: str
    page_url: str
    prerelease: bool
    assets: dict = field(default_factory=dict)   # name -> download URL

    @property
    def name(self):
        return self.tag if self.tag.startswith("v") else f"v{self.tag}"

    @property
    def download_url(self):
        return self.assets.get(ASSET)


def parse_version(text: str):
    """'v1.2.3' / '1.2' -> (1, 2, 3) / (1, 2, 0); None if it isn't a version."""
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?$", text.strip())
    if not m:
        return None
    return tuple(int(x or 0) for x in m.groups())


CURRENT = parse_version(__version__) or (0, 0, 0)


def check(include_prereleases=False, timeout=10) -> Release | None:
    """Newest release that is newer than this copy, or None. Raises OSError/ValueError."""
    req = urllib.request.Request(RELEASES_API, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    best = None
    for rel in data:
        if rel.get("draft") or (rel.get("prerelease") and not include_prereleases):
            continue
        ver = parse_version(rel.get("tag_name", ""))
        if ver is None or ver <= CURRENT:
            continue
        if best is None or ver > best.version:
            best = Release(ver, rel["tag_name"], rel.get("html_url") or RELEASES_PAGE,
                           bool(rel.get("prerelease")),
                           {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])})
    return best


def can_self_update() -> bool:
    """Only the standalone builds can replace themselves."""
    return system.FROZEN


def download(release: Release, progress=lambda done, total: None) -> Path:
    url = release.download_url
    if not url:
        raise ValueError(f"{release.name} has no {ASSET} download")
    dest = Path(tempfile.mkdtemp(prefix="studiocord-update-")) / ASSET
    req = urllib.request.Request(url, headers={"User-Agent": _UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(256 * 1024):
            f.write(chunk)
            done += len(chunk)
            progress(done, total)
    if total and done != total:
        raise OSError(f"download incomplete ({done} of {total} bytes)")
    log.info("Downloaded %s to %s", release.name, dest)
    return dest


def run_installer(path: Path):
    """Start the downloaded installer. It stops this copy and starts the new one."""
    if system.IS_WINDOWS:
        # Silent setup: closes the running Studiocord, updates, starts it again.
        subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                         close_fds=True)
    else:
        from .installer import _clean_env
        path.chmod(0o755)
        subprocess.Popen([str(path), "--install"], start_new_session=True, env=_clean_env(),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, cwd=str(Path.home()))
    log.info("Started installer %s", path)


# --- "don't notify twice for the same version" ------------------------------------------

def _state_file():
    return system.config_path().parent / "state.json"


def already_notified(release: Release) -> bool:
    try:
        return json.loads(_state_file().read_text()).get("notified") == release.tag
    except (OSError, ValueError):
        return False


def mark_notified(release: Release):
    try:
        p = _state_file()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"notified": release.tag}))
    except OSError:
        pass
