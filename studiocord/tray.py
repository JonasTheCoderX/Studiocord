"""Tray icon with live status, and the settings window (Qt / PySide6).

The presence loop (Daemon.run) runs in a background thread; Qt stays on the main
thread. The loop reports changes through Bridge signals, which Qt delivers safely
on the main thread.
"""

import logging
import os
import sys
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QMessageBox, QPushButton, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from . import WATERMARK, __version__, settings, system

log = logging.getLogger("tray")

# Discord's own status colours
GREEN, YELLOW, RED, GREY = "#23a55a", "#f0b232", "#f23f43", "#80848e"
BLURPLE = "#5865f2"


# --- status -> words / colour -------------------------------------------------------

def describe_status(st, cfg):
    """Returns (colour, headline, [detail lines]) for the tray and the settings window."""
    if st.studio_running:
        studio = "Roblox Studio: running"
    else:
        studio = "Roblox Studio: not running"

    if st.discord == "connected":
        discord = f"Discord: connected{f' as {st.discord_user}' if st.discord_user else ''}"
    elif st.discord == "detected":
        discord = "Discord: running"
    elif st.discord == "dry run":
        discord = "Discord: not used (dry run)"
    else:
        discord = "Discord: not detected"

    if not cfg["show_script_name"]:
        plugin = "Studio plugin: not used (script names off)"
    elif st.plugin_folders == 0:
        plugin = "Studio plugin: Studio's plugin folder not found"
    elif not st.studio_running:
        plugin = "Studio plugin: installed, waiting for Studio"
    elif st.plugin_seen:
        plugin = "Studio plugin: connected"
    else:
        plugin = "Studio plugin: not connected yet (allow its HTTP access in Studio)"

    if st.paused:
        colour, headline = GREY, "Paused: nothing is shown on Discord"
    elif not st.studio_running:
        colour, headline = GREY, "Waiting for Roblox Studio"
    elif st.discord == "not detected":
        colour, headline = YELLOW, "Discord not detected"
    elif st.presence:
        colour, headline = GREEN, f"Showing: {st.presence}"
    elif st.discord_error and st.discord != "connected":
        colour, headline = RED, "Can't reach Discord's Rich Presence"
    else:
        colour, headline = GREEN, "Running (nothing to show right now)"

    details = [studio, discord, plugin]
    if st.discord_error and st.studio_running and st.discord != "connected":
        details.append(f"Last error: {st.discord_error}")
    return colour, headline, details


def make_icon(colour: str | None = None) -> QIcon:
    """The app icon, with its corner dot recoloured to show the status."""
    base = QPixmap(str(system.resource("installer", "studiocord.png")))
    if base.isNull():  # missing resource: plain coloured square rather than nothing
        base = QPixmap(256, 256)
        base.fill(QColor(BLURPLE))
    if colour:
        s = base.width() / 1024  # the icon is drawn on a 1024 grid (installer/make_icon.py)
        p = QPainter(base)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(BLURPLE))
        p.drawEllipse(int(700 * s), int(700 * s), int(260 * s), int(260 * s))
        p.setBrush(QColor(colour))
        p.drawEllipse(int(740 * s), int(740 * s), int(180 * s), int(180 * s))
        p.end()
    return QIcon(base)


# --- settings window ---------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, daemon, parent=None):
        super().__init__(parent)
        self.daemon = daemon
        self.setWindowTitle("Studiocord settings")
        self.setWindowIcon(make_icon())
        self.setMinimumWidth(480)

        root = QVBoxLayout(self)

        # Live status
        box = QGroupBox("Status")
        v = QVBoxLayout(box)
        self.headline = QLabel()
        self.headline.setWordWrap(True)
        f = self.headline.font()
        f.setBold(True)
        self.headline.setFont(f)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(self.headline)
        v.addWidget(self.details)
        root.addWidget(box)

        # What to show
        box = QGroupBox("What to show on Discord")
        v = QVBoxLayout(box)
        self.checks = {}
        for key, text in [
            ("show_place_name", "The place's name and icon"),
            ("show_button", "A \"View on Roblox\" button (published places)"),
            ("show_script_name", "The script you're editing"),
            ("show_line_count", "The script's line count"),
            ("show_team_create", "When you're in Team Create"),
            ("show_idle", "A status while Studio is on its start page"),
        ]:
            cb = QCheckBox(text)
            self.checks[key] = cb
            v.addWidget(cb)
        self.checks["show_script_name"].toggled.connect(self._sync_enabled)
        self.checks["show_place_name"].toggled.connect(self._sync_enabled)
        root.addWidget(box)

        # General
        box = QGroupBox("General")
        v = QVBoxLayout(box)
        self.autostart = QCheckBox("Start Studiocord when I log in")
        if not system.autostart_supported():
            self.autostart.setEnabled(False)
            self.autostart.setToolTip("Only for the installed app; this copy runs from source.")
        self.checks["auto_install_plugin"] = QCheckBox(
            "Keep the Studio plugin installed (needed for script names)")
        v.addWidget(self.autostart)
        v.addWidget(self.checks["auto_install_plugin"])
        root.addWidget(box)

        # Advanced
        box = QGroupBox("Advanced")
        form = QFormLayout(box)
        self.client_id = QLineEdit()
        self.client_id.setPlaceholderText(settings.DEFAULTS["client_id"])
        self.client_id.setToolTip("Discord application ID; its name is shown as \"Playing …\".")
        form.addRow("Discord application ID", self.client_id)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        self.logs_dir = QLineEdit()
        self.logs_dir.setPlaceholderText(system.default_logs_dir())
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_logs)
        h.addWidget(self.logs_dir, 1)
        h.addWidget(browse)
        form.addRow("Studio logs folder", row)
        root.addWidget(box)

        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Close
                                   | QDialogButtonBox.RestoreDefaults)
        buttons.button(QDialogButtonBox.Save).clicked.connect(self._save)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.close)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            lambda: self._load(settings.DEFAULTS))
        links = QHBoxLayout()
        for text, fn in [("Open log", self._open_log), ("Open settings file", self._open_config)]:
            b = QPushButton(text)
            b.setFlat(True)
            b.clicked.connect(fn)
            links.addWidget(b)
        links.addStretch(1)
        links.addWidget(buttons)
        root.addLayout(links)

        footer = QLabel(f"{WATERMARK} · v{__version__}")
        footer.setStyleSheet("color: gray; font-size: 9pt;")
        footer.setAlignment(Qt.AlignRight)
        root.addWidget(footer)

        self._load(daemon.cfg)

    def _load(self, cfg):
        for key, cb in self.checks.items():
            cb.setChecked(bool(cfg.get(key, settings.DEFAULTS[key])))
        cid = cfg.get("client_id", "")
        self.client_id.setText("" if cid == settings.DEFAULTS["client_id"] else cid)
        self.logs_dir.setText(cfg.get("logs_dir", ""))
        self.autostart.setChecked(system.autostart_supported() and system.autostart_enabled())
        self._sync_enabled()

    def _sync_enabled(self):
        self.checks["show_line_count"].setEnabled(self.checks["show_script_name"].isChecked())
        self.checks["show_button"].setEnabled(self.checks["show_place_name"].isChecked())

    def _browse_logs(self):
        d = QFileDialog.getExistingDirectory(self, "Roblox Studio logs folder",
                                             self.logs_dir.text() or system.default_logs_dir())
        if d:
            self.logs_dir.setText(d)

    def _save(self):
        cfg = dict(self.daemon.cfg)
        for key, cb in self.checks.items():
            cfg[key] = cb.isChecked()
        cid = self.client_id.text().strip() or settings.DEFAULTS["client_id"]
        if not cid.isdigit():
            QMessageBox.warning(self, "Studiocord", "The Discord application ID must be a number.")
            return
        cfg["client_id"] = cid
        cfg["logs_dir"] = self.logs_dir.text().strip()
        try:
            settings.save(cfg)
        except OSError as e:
            QMessageBox.critical(self, "Studiocord", f"Couldn't save the settings:\n{e}")
            return
        if system.autostart_supported() and self.autostart.isChecked() != system.autostart_enabled():
            try:
                system.set_autostart(self.autostart.isChecked())
            except OSError as e:
                QMessageBox.warning(self, "Studiocord", f"Couldn't change \"start at login\":\n{e}")
        self.daemon.apply_config(cfg)
        self.close()

    def _open_log(self):
        path = system.log_file()
        if path and path.exists():
            system.open_path(path)
        else:
            QMessageBox.information(self, "Studiocord", "This copy logs to the terminal / system "
                                    "journal (journalctl --user -u studiocord).")

    def _open_config(self):
        path = settings.path()
        if not path.exists():
            settings.save(self.daemon.cfg)
        system.open_path(path)

    def refresh(self):
        colour, headline, details = describe_status(self.daemon.status, self.daemon.cfg)
        self.headline.setText(f'<span style="color:{colour}">●</span> {headline}')
        self.details.setText("\n".join(details))

    def showEvent(self, e):
        self.refresh()
        super().showEvent(e)


# --- tray --------------------------------------------------------------------------------

class Bridge(QObject):
    changed = Signal()
    show_settings = Signal()


class Tray:
    def __init__(self, app: QApplication, daemon):
        self.app = app
        self.daemon = daemon
        self.icons = {c: make_icon(c) for c in (GREEN, YELLOW, RED, GREY)}
        self.dialog = None

        self.bridge = Bridge()
        self.bridge.changed.connect(self.refresh)
        self.bridge.show_settings.connect(self.open_settings)
        daemon.on_change = self.bridge.changed.emit
        daemon.commands["show-settings"] = self.bridge.show_settings.emit

        self.tray = QSystemTrayIcon(self.icons[GREY])
        self.tray.setToolTip("Studiocord")
        menu = QMenu()
        title = menu.addAction(f"Studiocord {__version__}")
        title.setEnabled(False)
        menu.addSeparator()
        self.headline = menu.addAction("")
        self.headline.setEnabled(False)
        self.lines = [menu.addAction("") for _ in range(4)]
        for a in self.lines:
            a.setEnabled(False)
        menu.addSeparator()
        self.pause = QAction("Pause (hide from Discord)", menu, checkable=True)
        self.pause.toggled.connect(daemon.set_paused)
        menu.addAction(self.pause)
        menu.addAction("Settings…", self.open_settings)
        menu.addAction("Open log", self._open_log)
        menu.addSeparator()
        menu.addAction("Quit Studiocord", self.quit)
        self.menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        self.tray.show()

        # The plugin goes "stale" by time alone, so refresh now and then too.
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5000)
        self.refresh()

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_settings()

    def refresh(self):
        colour, headline, details = describe_status(self.daemon.status, self.daemon.cfg)
        self.tray.setIcon(self.icons[colour])
        self.tray.setToolTip(f"Studiocord\n{headline}")
        self.headline.setText(headline if len(headline) < 70 else headline[:69] + "…")
        for action, text in zip(self.lines, details + [""] * 4):
            action.setText(text)
            action.setVisible(bool(text))
        if self.pause.isChecked() != self.daemon.paused:
            self.pause.setChecked(self.daemon.paused)
        if self.dialog and self.dialog.isVisible():
            self.dialog.refresh()

    def open_settings(self):
        if self.dialog is None:
            self.dialog = SettingsDialog(self.daemon)
        else:
            self.dialog._load(self.daemon.cfg)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def _open_log(self):
        path = system.log_file()
        if path and path.exists():
            system.open_path(path)

    def quit(self):
        self.daemon.stop()
        self.tray.hide()
        self.app.quit()


def _hard_exit(code: int):
    """Exit without Python's normal teardown.

    Letting the interpreter destroy Qt objects in arbitrary order at exit can segfault
    (seen with PySide6 in the PyInstaller build), so flush everything and leave.
    """
    logging.shutdown()
    for f in (sys.stdout, sys.stderr):
        try:
            f and f.flush()
        except Exception:
            pass
    os._exit(code)


def _make_app():
    QApplication.setApplicationName("Studiocord")
    QApplication.setDesktopFileName("studiocord")  # Wayland: match our .desktop file / icon
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(make_icon())
    return app


def run_tray(daemon, show_settings=False) -> int:
    app = _make_app()
    tray = Tray(app, daemon)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("No system tray on this desktop; showing the settings window instead")
        show_settings = True

    thread = threading.Thread(target=daemon.run, kwargs={"install_signals": False},
                              name="presence", daemon=True)
    thread.start()

    # If the loop ends (quit from the installer, or another copy already runs), quit Qt.
    watchdog = QTimer()
    watchdog.timeout.connect(lambda: None if thread.is_alive() else app.quit())
    watchdog.start(1000)

    if show_settings:
        QTimer.singleShot(300, tray.open_settings)
    code = app.exec()
    daemon.stop()
    thread.join(timeout=5)  # lets the loop clear the Discord status
    tray.tray.hide()
    _hard_exit(code)


def selftest() -> int:
    """Build the tray and settings window without a screen (used by CI)."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from .__main__ import Daemon
    app = _make_app()
    daemon = Daemon(dict(settings.DEFAULTS))
    tray = Tray(app, daemon)
    tray.open_settings()
    tray.dialog.refresh()
    ok = not tray.dialog.grab().isNull() and not tray.icons[GREEN].isNull()
    print("gui selftest:", "ok" if ok else "FAILED", "| tray available:",
          QSystemTrayIcon.isSystemTrayAvailable())
    tray.tray.hide()
    _hard_exit(0 if ok else 1)
