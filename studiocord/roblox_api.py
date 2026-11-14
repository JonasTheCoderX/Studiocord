"""Looks up place name + icon from Roblox's public (no-login) web APIs, in the background."""

import json
import logging
import threading
import urllib.request

log = logging.getLogger("roblox")

from . import WATERMARK, __version__

_UA = {"User-Agent": f"Studiocord/{__version__} ({WATERMARK})"}


def _get(url):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.load(r)


def _is_placeholder(name: str) -> bool:
    """Roblox's stand-ins like "[TITLE UNAVAILABLE]" or "[ Content Deleted ]"."""
    return name.startswith("[") and name.endswith("]")


class PlaceInfo:
    def __init__(self, name=None, icon_url=None, universe_id=None, public_url=None):
        self.name, self.icon_url = name, icon_url
        self.universe_id, self.public_url = universe_id, public_url


class PlaceLookup:
    """Non-blocking cache: get() returns PlaceInfo once fetched, else None (and starts a fetch)."""

    def __init__(self):
        self._cache: dict[int, PlaceInfo] = {}
        self._pending: set[int] = set()
        self._lock = threading.Lock()
        self.on_update = lambda: None

    def get(self, place_id: int, universe_id: int | None = None):
        with self._lock:
            if place_id in self._cache:
                return self._cache[place_id]
            if place_id in self._pending:
                return None
            self._pending.add(place_id)
        threading.Thread(target=self._fetch, args=(place_id, universe_id), daemon=True).start()
        return None

    def _fetch(self, place_id, universe_id):
        info = PlaceInfo()
        try:
            if not universe_id:
                universe_id = _get(f"https://apis.roblox.com/universes/v1/places/{place_id}/universe").get("universeId")
            info.universe_id = universe_id
            if universe_id:
                games = _get(f"https://games.roblox.com/v1/games?universeIds={universe_id}").get("data") or []
                if games:
                    name = (games[0].get("name") or "").strip()
                    root = games[0].get("rootPlaceId") or 0
                    # Games that aren't public come back as "[TITLE UNAVAILABLE]" with
                    # rootPlaceId 0: no usable name, and no page to link to.
                    if name and not _is_placeholder(name):
                        info.name = name
                    if root and not _is_placeholder(name):
                        info.public_url = f"https://www.roblox.com/games/{root}"
                icons = _get("https://thumbnails.roblox.com/v1/games/icons?universeIds="
                             f"{universe_id}&size=512x512&format=Png&isCircular=false").get("data") or []
                if icons and icons[0].get("state") == "Completed":
                    info.icon_url = icons[0].get("imageUrl")
            log.info("Place %s -> %r (universe %s)", place_id, info.name, universe_id)
        except Exception as e:  # network down, private place, API change ...
            log.warning("Lookup for place %s failed: %s", place_id, e)
        with self._lock:
            self._cache[place_id] = info
            self._pending.discard(place_id)
        self.on_update()
