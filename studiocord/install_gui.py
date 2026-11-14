"""Installer window for the standalone Linux build (Qt / PySide6)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QVBoxLayout)

from . import WATERMARK, __version__, installer
from .tray import make_icon


class InstallerWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Install Studiocord")
        self.setWindowIcon(make_icon())
        self.setMinimumWidth(460)
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(make_icon().pixmap(72, 72))
        logo.setAlignment(Qt.AlignTop)
        top.addWidget(logo)
        text = QVBoxLayout()
        title = QLabel(f"<h2 style='margin:0'>Studiocord {__version__}</h2>")
        update = installer.is_installed()
        blurb = QLabel(
            ("Studiocord is already installed. Installing again updates it to this version."
             if update else
             "Shows what you're doing in Roblox Studio on your Discord profile: the place, "
             "the script you're editing, playtesting and Team Create.")
            + "<br><br>It runs in the background with an icon in your system tray. "
              "You don't need Python or anything else.")
        blurb.setWordWrap(True)
        text.addWidget(title)
        text.addWidget(blurb)
        top.addLayout(text, 1)
        root.addLayout(top)

        self.autostart = QCheckBox("Start Studiocord when I log in")
        self.autostart.setChecked(True)
        root.addWidget(self.autostart)

        self.result = QLabel()
        self.result.setWordWrap(True)
        self.result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.result.hide()
        root.addWidget(self.result)

        buttons = QHBoxLayout()
        self.uninstall_btn = QPushButton("Uninstall")
        self.uninstall_btn.setVisible(update)
        self.uninstall_btn.clicked.connect(self._uninstall)
        buttons.addWidget(self.uninstall_btn)
        buttons.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.install_btn = QPushButton("Update" if update else "Install")
        self.install_btn.setDefault(True)
        self.install_btn.clicked.connect(self._install)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.install_btn)
        root.addLayout(buttons)

        footer = QLabel(WATERMARK)
        footer.setStyleSheet("color: gray; font-size: 9pt;")
        footer.setAlignment(Qt.AlignRight)
        root.addWidget(footer)

    def _busy(self, busy):
        for w in (self.install_btn, self.cancel_btn, self.uninstall_btn, self.autostart):
            w.setEnabled(not busy)
        QApplication.setOverrideCursor(Qt.WaitCursor) if busy else QApplication.restoreOverrideCursor()
        QApplication.processEvents()

    def _done(self, msg):
        self.result.setText(msg)
        self.result.show()
        self.autostart.hide()
        self.uninstall_btn.hide()
        self.cancel_btn.hide()
        self.install_btn.setText("Close")
        self.install_btn.setEnabled(True)
        self.install_btn.clicked.disconnect()
        self.install_btn.clicked.connect(self.accept)
        self.adjustSize()

    def _install(self):
        self._busy(True)
        try:
            msg = installer.install(autostart=self.autostart.isChecked())
        except (installer.InstallError, OSError) as e:
            self._busy(False)
            QMessageBox.critical(self, "Studiocord", f"Installing failed:\n{e}")
            return
        self._busy(False)
        self.setWindowTitle("Studiocord installed")
        self._done(msg)

    def _uninstall(self):
        if QMessageBox.question(self, "Studiocord", "Remove Studiocord from this computer?\n"
                                "Your settings are kept.") != QMessageBox.Yes:
            return
        self._busy(True)
        msg = installer.uninstall()
        self._busy(False)
        self.setWindowTitle("Studiocord uninstalled")
        self._done(msg)


def run_installer_gui() -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Studiocord")
    app.setWindowIcon(make_icon())
    w = InstallerWindow()
    w.show()
    app.exec()
    from .tray import _hard_exit
    _hard_exit(0)
