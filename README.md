<p align="center"><img src="installer/studiocord.png" width="96" alt=""></p>

# Studiocord

Discord Rich Presence for Roblox Studio, on **Windows** and on **Linux** (Studio via
[Vinegar](https://vinegarhq.org)).

It runs in the background and checks for Roblox Studio every 2 seconds. While Studio
runs, it reads Studio's own log files and shows what you're doing:

| In Studio                     | On Discord                                              |
|-------------------------------|---------------------------------------------------------|
| Start page, no place open     | On the start page                                       |
| Place open, 3D view           | Editing *Place name* · Building                         |
| Script tab focused            | Editing *Place name* · Editing *ScriptName* (*n* lines) |
| Play / Run test               | Editing *Place name* · Playtesting                      |
| Team Create connected         | … · Team Create                                         |
| Local `.rbxl` file            | Editing *file name*                                     |
| New place from a template     | Editing a new place (Baseplate)                         |
| Studio closed                 | presence cleared                                        |

Published places also get their game icon and a "View on Roblox" button. Names and icons
come from Roblox's public web APIs, with no login.

## Install

Download the latest version from **[Releases](../../releases/latest)**. You don't need Python.

### Windows

Run **`Studiocord-Setup.exe`** and follow the wizard. It installs for your user only, so it
doesn't need admin rights. You choose whether Studiocord starts with Windows, and it starts
when setup finishes. To uninstall, use Settings → Apps.

Windows SmartScreen may warn about the installer because it isn't code-signed. Click
*More info* → *Run anyway*.

### Linux

Download **`Studiocord-Installer-Linux`**, make it executable and open it:

```sh
chmod +x Studiocord-Installer-Linux
./Studiocord-Installer-Linux
```

An installer window opens. Click **Install**: Studiocord copies itself to
`~/.local/share/studiocord/`, adds itself to your app menu and (if you leave the box
ticked) to the programs that start at login, then starts. You can delete the downloaded
file afterwards. To update, open a newer installer the same way. To uninstall, open the
installer again and click **Uninstall**, or run `~/.local/share/studiocord/studiocord --uninstall`.
Without a desktop, `--install` installs from the terminal instead.

On Linux, Studiocord works with Roblox Studio running through Vinegar. If Vinegar's own
"Discord Rich Presence" option works for you, turn one of them off so they don't fight.
Vinegar 1.9.4's built-in presence doesn't work with newer Kombucha (Wine) builds, which
is why Studiocord reads the log files directly.

## Tray icon and settings

Studiocord sits in the system tray. Its corner dot shows the status, like on Discord:

| Dot    | Meaning                                                   |
|--------|-----------------------------------------------------------|
| green  | showing your status on Discord                            |
| grey   | waiting for Roblox Studio, or paused                      |
| yellow | Studio is running, but Discord wasn't found               |
| red    | Discord is running, but Studiocord can't reach it         |

Right-click it to see what's going on (Roblox Studio, Discord, the Studio plugin, what's
shown), to **pause** it, or to **quit**. Click it, or open Studiocord from the Start/app
menu, for the **settings window**: what to show on Discord, start at login, and a live
status. On desktops without a tray (e.g. GNOME without the AppIndicator extension), the
settings window opens instead.

## Script names

Studio doesn't log which script you're editing, so Studiocord puts a small helper plugin,
`Studiocord.lua`, into Studio's local Plugins folder. The plugin sends the focused script's
name, path and line count to Studiocord on `127.0.0.1:47231`, and only there. The first
time, Studio asks whether the plugin may make HTTP requests to 127.0.0.1: click **Allow**.
Without the plugin, the status just says "Editing a script".

Studiocord checks every 15 seconds that the plugin is still there and up to date, and puts
it back if it was deleted or Studio was reinstalled. Studio loads a newly added plugin
right away, but an updated plugin only takes effect after restarting Studio.

## Discord application

Studiocord shows up as its own Discord application (the name after "Playing"). To use
your own instead, create one at https://discord.com/developers/applications, put its
Application ID into Settings → Advanced, and upload these images under
*Rich Presence → Art Assets* (at least 512×512, names exactly as listed):

| Name           | Used for                                                      |
|----------------|---------------------------------------------------------------|
| `studio`       | big image when there's no game icon (start page, local files) |
| `place`        | small image while building                                    |
| `playtest`     | small image while playtesting                                 |
| `script`       | small image while editing a Script                            |
| `localscript`  | small image while editing a LocalScript                       |
| `modulescript` | small image while editing a ModuleScript                      |

## Settings file

Everything in the settings window is stored in `config.toml`
(`%APPDATA%\Studiocord` on Windows, `~/.config/studiocord` on Linux). See
[`config.example.toml`](config.example.toml).

## Running from source

Needs Python 3.11 or newer. The tray icon and windows also need
`pip install PySide6-Essentials`; without it, Studiocord runs without them.

```sh
python3 -m studiocord              # run (with tray icon if PySide6 is installed)
python3 -m studiocord --diagnose   # show what it finds (logs, Studio, Discord, plugin folder)
python3 -m studiocord --dry-run    # live, but print the status instead of sending it
python3 -m studiocord --replay <Studio log file>   # replay an old Studio session
./install.sh                       # Linux: run this checkout as a systemd user service
```

Building the standalone programs: `pip install pyinstaller PySide6-Essentials && python build.py`. On Windows,
then run `iscc installer\studiocord.iss` (Inno Setup 6).

Releases come from GitHub Actions ("Build & release"). Running it by hand with *publish*
ticked builds both programs and publishes them as a **pre-release** for the version in
`studiocord/__init__.py`, replacing the previous pre-release of that version. Make it a
full release with `gh release edit v1.0.0 --prerelease=false --latest`.

## Layout

- `studiocord/studio_log.py` turns Studio log lines into a state (place, script, playtest, Team Create).
- `studiocord/system.py` holds everything that differs between Windows and Linux: folders, finding Studio, Discord's endpoints.
- `studiocord/discord_ipc.py` is a tiny Discord IPC client (Unix socket or Windows named pipe).
- `studiocord/script_server.py` is the localhost endpoint the plugin talks to.
- `studiocord/roblox_api.py` looks up place names and icons in the background.
- `studiocord/tray.py` is the tray icon and settings window.
- `studiocord/settings.py` loads and saves `config.toml`.
- `studiocord/installer.py` and `install_gui.py` are the Linux build's installer (logic and window).
- `studiocord/__main__.py` runs the main loop.
- `plugin/Studiocord.lua` is the Studio plugin that reports the focused script.
- `installer/` holds the Windows installer script (Inno Setup), the icon and the wizard images (`make_icon.py` draws them).

## License

MIT, see [LICENSE](LICENSE).

---

<sub>Studiocord by JonasTheCoderX</sub>
