"""Turns Roblox Studio log lines into a StudioState.

Studio writes its log to <logs dir>/<version>_<timestamp>_Studio_<id>_last.log.
Every line looks like:
    2026-11-13T20:46:31.961Z,6.961985,00f0,6,Info [FLog::RobloxDocManager] Setting current doc to type 0
"""

import os
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

_OPEN_PLACE = re.compile(r"\[FLog::StudioKeyEvents\] open place \(identifier = (.+?)\) \[start\]")
_PLACE_ID = re.compile(r"\[FLog::CloseDataModel\] Setting place ID (\d+)")
_UNIVERSE = re.compile(r"\[telemetryLog\] universeid: (\d+)")
_GAME_STATE = re.compile(r"Setting StudioGameStateType to StudioGameStateType_(\w+)")
_DOC_TYPE = re.compile(r"\[FLog::RobloxDocManager\] Setting current doc to type (-?\d+)")
_TC_CONNECTED = "team create connect (connection accepted)"
_TC_DISCONNECTED = "Handling team create disconnection event"
_EXITING = "About to exit the application"
# Studio logs when its window gains/loses focus (one process per Studio window).
_FOCUS_IN = "[FLog::StudioApplicationState] State: Qt::ApplicationActive"
_FOCUS_OUT = "[FLog::StudioApplicationState] State: Qt::ApplicationInactive"
_TIMESTAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)Z,")

# RobloxDocManager doc types
DOC_NONE, DOC_PLACE, DOC_SCRIPT = -1, 0, 2


@dataclass
class StudioState:
    place_ref: str | None = None      # numeric place id, or a file path for local files
    place_id: int | None = None       # set when opened from the cloud
    local_name: str | None = None     # set when opened from a .rbxl file
    universe_id: int | None = None
    published: bool = True            # False for a new place made from a template
    game_state: str = "Null"          # Edit, PlayClient, PlayServer, Null ...
    doc_type: int = DOC_NONE
    team_create: bool = False
    place_opened_at: float | None = None  # unix time
    exiting: bool = False
    focused: bool = False             # this Studio's window has focus right now
    focused_at: float = 0.0           # when it last got focus (log time, unix)

    @property
    def has_place(self):
        return self.place_ref is not None


def _log_time(line):
    """Parse the UTC timestamp at the start of a log line (unix seconds) or None."""
    m = _TIMESTAMP.match(line)
    if not m:
        return None
    from datetime import datetime, timezone
    try:
        return datetime.fromisoformat(m.group(1)).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _place_name_from_path(ref):
    # Wine paths look like Z:/home/... ; strip folder and extension.
    name = PureWindowsPath(ref).name or PurePosixPath(ref).name
    return os.path.splitext(name)[0] or ref


def apply_line(state: StudioState, line: str) -> bool:
    """Update state from one log line. Returns True if anything changed."""
    if "[FLog::" not in line and "[telemetryLog]" not in line and "[DFLog::" not in line:
        return False

    if m := _OPEN_PLACE.search(line):
        ref = m.group(1).strip()
        new = StudioState(place_ref=ref, game_state="Edit", doc_type=DOC_PLACE,
                          place_opened_at=_log_time(line))
        if ref.isdigit():
            new.place_id = int(ref)
        else:
            new.local_name = _place_name_from_path(ref)
        new.focused, new.focused_at, new.exiting = state.focused, state.focused_at, state.exiting
        state.__dict__.update(new.__dict__)
        return True

    if m := _PLACE_ID.search(line):
        pid = int(m.group(1))
        if not state.has_place:
            return False
        if pid == 0:
            # Studio sets 0 right after opening a template ("Baseplate" etc.):
            # it's a new, unsaved place that only borrows the template's id.
            changed = state.published
            state.published = False
            return changed
        if state.local_name is not None:
            return False  # local .rbxl file: keep showing its file name
        if state.place_id != pid:
            state.place_id, state.universe_id, state.published = pid, None, True
            return True
        if not state.published:
            state.published = True
            return True
        return False

    if m := _UNIVERSE.search(line):
        uid = int(m.group(1))
        if state.has_place and state.universe_id is None and uid > 0:
            state.universe_id = uid
            return True
        return False

    if m := _GAME_STATE.search(line):
        gs = m.group(1)
        if gs == "Null":
            # Place was closed -> back to the start page.
            changed = state.has_place or state.game_state != "Null"
            keep = (state.exiting, state.focused, state.focused_at)
            state.__dict__.update(StudioState().__dict__)
            state.exiting, state.focused, state.focused_at = keep
            return changed
        if gs != state.game_state:
            state.game_state = gs
            return True
        return False

    if m := _DOC_TYPE.search(line):
        dt = int(m.group(1))
        if dt != state.doc_type:
            state.doc_type = dt
            return True
        return False

    if _TC_CONNECTED in line:
        changed = not state.team_create
        state.team_create = True
        return changed
    if _TC_DISCONNECTED in line:
        changed = state.team_create
        state.team_create = False
        return changed
    if _FOCUS_IN in line:
        state.focused, state.focused_at = True, _log_time(line) or state.focused_at
        return True
    if _FOCUS_OUT in line:
        changed = state.focused
        state.focused = False
        return changed
    if _EXITING in line:
        state.exiting = True
        return True
    return False


class LogFollower:
    """Follows one log file like `tail -f`, starting from the beginning."""

    def __init__(self, path):
        self.path = path
        self.f = open(path, "r", encoding="utf-8", errors="replace")
        self.buf = ""

    def read_lines(self):
        chunk = self.f.read()
        if not chunk:
            return []
        self.buf += chunk
        *lines, self.buf = self.buf.split("\n")
        return lines

    def close(self):
        self.f.close()
