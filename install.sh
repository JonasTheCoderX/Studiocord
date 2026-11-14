#!/bin/sh
# Studiocord by JonasTheCoderX
# Installs Studiocord as a systemd user service that starts at login.
set -e
cd "$(dirname "$0")"
mkdir -p ~/.config/systemd/user ~/.config/studiocord
sed "s|@INSTALL_DIR@|$(pwd)|" studiocord.service > ~/.config/systemd/user/studiocord.service
[ -f ~/.config/studiocord/config.toml ] || cp config.example.toml ~/.config/studiocord/config.toml
# Studio helper plugin (sends the focused script's name to the daemon)
# (Studio's %LOCALAPPDATA%\Roblox\Plugins inside Vinegar's Wine prefix)
for PLUGINS in ~/.var/app/org.vinegarhq.Vinegar/data/vinegar/prefixes/*/drive_c/users/*/AppData/Local/Roblox/Plugins; do
    mkdir -p "$PLUGINS" && cp plugin/Studiocord.lua "$PLUGINS/" && echo "Plugin -> $PLUGINS"
done
systemctl --user daemon-reload
systemctl --user enable studiocord
systemctl --user restart studiocord
echo "Installed. Logs: journalctl --user -u studiocord -f"
