# Studiocord

Discord Rich Presence for Roblox Studio running under [Vinegar](https://vinegarhq.org).

It runs in the background and checks for Roblox Studio every 2 seconds. While Studio
runs, it reads Studio's own log files and shows what you're doing:

| In Studio                     | On Discord                                         |
|-------------------------------|----------------------------------------------------|
| Start page, no place open     | On the start page                                  |
| Place open, 3D view           | Editing *Place name* · Building                    |
| Script tab focused            | Editing *Place name* · Editing *ScriptName* (*n* lines) |
| Play / Run test               | Editing *Place name* · Playtesting                 |
| Team Create connected         | … · Team Create                                    |
| Local `.rbxl` file            | Editing *file name*                                |
| New place from a template     | Editing a new place (Baseplate)                    |
| Studio closed                 | presence cleared                                   |

Published places also get their game icon and a "View on Roblox" button. Names and icons
come from Roblox's public web APIs, with no login. Studio doesn't log script names, so a small
helper plugin (`plugin/Studiocord.lua`, installed by `install.sh`) posts the focused
script's name and path to the daemon on `127.0.0.1:47231`. The first time, Studio asks
whether the plugin may make HTTP requests to 127.0.0.1; allow it. Without the plugin
it falls back to "Editing a script".

The service checks every 15 s that the plugin is still installed and up to date, and
copies it back if it was deleted, Vinegar/Studio was reinstalled (new Wine prefix), or
`plugin/Studiocord.lua` changed. Studio loads a newly added plugin live, but an
updated plugin only takes effect after restarting Studio.

**Why not Vinegar's built-in RPC?** Vinegar 1.9.4 reads Studio's output through Wine's
debug channel. Newer Kombucha (Wine) builds print it on a different channel, so Vinegar
sees nothing. This tool reads the log files directly instead.

## Install

```sh
./install.sh
```

No dependencies beyond Python 3.11+ (uses only the standard library).

## Use

```sh
systemctl --user status studiocord        # is it running?
journalctl --user -u studiocord -f        # live log
systemctl --user restart studiocord       # after editing the config
systemctl --user disable --now studiocord # turn it off
```

Settings: `~/.config/studiocord/config.toml` (see `config.example.toml`).

Testing without Discord:

```sh
python3 -m studiocord --replay ~/.var/app/org.vinegarhq.Vinegar/data/vinegar/appdata/Roblox/logs/<file>_last.log
python3 -m studiocord --dry-run    # live, but prints instead of sending
```

## Layout

- `studiocord/studio_log.py` turns Studio log lines into a state (place, script, playtest, Team Create).
- `studiocord/discord_ipc.py` is a tiny Discord IPC client (native, Flatpak and Vesktop sockets).
- `studiocord/script_server.py` is the localhost endpoint the plugin talks to.
- `plugin/Studiocord.lua` is the Studio plugin that reports the focused script.
- `studiocord/roblox_api.py` looks up place names and icons in the background.
- `studiocord/__main__.py` runs the main loop: Studio detection, picking the right log, building the presence.

If you have Vinegar's own "Discord Rich Presence" option on, turn it off so the two don't fight.

## License

MIT, see [LICENSE](LICENSE).

---

<sub>Studiocord by JonasTheCoderX</sub>
