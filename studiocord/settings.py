"""Loading and saving Studiocord's settings (config.toml)."""

import json
import os
import tomllib

from . import system

DEFAULTS = {
    # Discord application whose name/icon is shown ("Playing <name>").
    # Default: Vinegar's own "Roblox Studio" app, which has the "place"/"script" images.
    "client_id": "1159891020956323923",
    "logs_dir": "",              # empty = Studio's default for this OS
    "show_place_name": True,     # False = just "Editing a place", no name or icon
    "show_button": True,         # "View on Roblox" button for published places
    "show_team_create": True,
    "show_script_name": True,    # needs the Studio plugin
    "show_line_count": True,     # "Editing Main (120 lines)"; needs the Studio plugin
    "show_idle": True,           # show presence on the start page (no place open)
    "auto_install_plugin": True, # put the plugin back if it is missing/outdated
    "plugin_port": 47231,
    "poll_seconds": 1.0,
}

# Comments written above each key when saving, so the file stays readable.
_COMMENTS = {
    "client_id": "Discord application shown as \"Playing <name>\". Default: Vinegar's \"Roblox Studio\" app.",
    "logs_dir": "Where Roblox Studio writes its logs. Empty = the usual place on this system.",
    "show_place_name": "false = \"Editing a place\", no name/icon/button",
    "show_button": "\"View on Roblox\" button for published places",
    "show_team_create": "append \"· Team Create\" when connected",
    "show_script_name": "\"Editing <ScriptName>\" (uses the Studiocord Studio plugin)",
    "show_line_count": "\"Editing Main (120 lines)\"",
    "show_idle": "show \"On the start page\" when no place is open",
    "auto_install_plugin": "put the Studio plugin back if it's deleted or Studio is reinstalled",
    "plugin_port": "must match the URL in the Studio plugin",
    "poll_seconds": "how often to check Studio's log",
}


def path():
    return system.config_path()


def load() -> dict:
    cfg = dict(DEFAULTS)
    p = path()
    if p.exists():
        try:
            with open(p, "rb") as f:
                cfg.update({k: v for k, v in tomllib.load(f).items() if k in DEFAULTS})
        except (OSError, tomllib.TOMLDecodeError):
            pass  # a broken file shouldn't stop Studiocord; defaults it is
    return cfg


def logs_dir(cfg) -> str:
    return os.path.expanduser(cfg.get("logs_dir") or system.default_logs_dir())


def _toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v), ensure_ascii=False)  # JSON strings are valid TOML strings


def save(cfg: dict):
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Studiocord settings. You can also change these from the tray icon > Settings.", ""]
    for key in DEFAULTS:
        if key in _COMMENTS:
            lines.append(f"# {_COMMENTS[key]}")
        lines.append(f"{key} = {_toml_value(cfg.get(key, DEFAULTS[key]))}")
        lines.append("")
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    os.replace(tmp, p)
