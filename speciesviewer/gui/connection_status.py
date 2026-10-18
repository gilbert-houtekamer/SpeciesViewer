# connection_status.py: Help → Connection status — a status-lamp view of the
# online services SpeciesViewer uses (publish server, species lookup, place
# names, website). Each is probed independently so the user can tell "the
# website is slow" apart from "I'm offline" (see engine.connectivity).

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

import engine.connectivity as connectivity

_LAMP = {"checking": "#9e9e9e", "ok": "#2e7d32", "slow": "#f39c12", "down": "#c0392b"}


class _ProbeThread(QThread):
    """Runs the (parallel, blocking) probes off the GUI thread."""
    done = pyqtSignal(dict)

    def run(self):
        try:
            self.done.emit(connectivity.check_all())
        except Exception:
            self.done.emit({})


class ConnectionStatusDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Connection status")
        self._probe = None

        # the chestnut-colored woodpecker, tapping for a signal, on the left
        # (Gilbert's Firefly set, 2026-10-18); the dialog's content beside it
        from gui.wait_picture import picture_label
        outer = QHBoxLayout(self)
        pic = picture_label("woodpecker", 160, self.devicePixelRatioF())
        if pic is not None:
            outer.addWidget(pic, alignment=Qt.AlignmentFlag.AlignTop)
        lay = QVBoxLayout()
        outer.addLayout(lay, 1)
        self.setMinimumWidth(480 + (176 if pic is not None else 0))
        intro = QLabel(
            "SpeciesViewer uses a few online services. Each is checked on its "
            "own — a problem with one doesn't mean the others are down. "
            "Publishing needs only the publish server.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        lay.addLayout(grid)

        self._rows = {}
        eps = connectivity.endpoints()
        for r, key in enumerate(eps):
            label, host, port, note = eps[key]
            lamp = QLabel("●")
            lamp.setStyleSheet(f"color:{_LAMP['checking']}; font-size:16px;")
            name = QLabel(f"<b>{label}</b><br>"
                          f"<span style='color:#888;font-size:11px'>{note}</span>")
            name.setTextFormat(Qt.TextFormat.RichText)
            name.setWordWrap(True)
            name.setToolTip(f"{host}:{port}")
            status = QLabel("checking…")
            status.setStyleSheet("color:#888;")
            grid.addWidget(lamp, r, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(name, r, 1)
            grid.addWidget(status, r, 2, Qt.AlignmentFlag.AlignTop)
            self._rows[key] = (lamp, status)

        lay.addStretch(1)           # intro and lamps stay together at the top
        btns = QHBoxLayout()
        self._recheck = QPushButton("Recheck")
        self._recheck.clicked.connect(self._start)
        btns.addWidget(self._recheck)
        btns.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        lay.addLayout(btns)

        self._start()
        # as high as the content needs — the word-wrapped intro made Qt guess
        # too tall, leaving an empty band above the buttons (Gilbert, 2026-10-18)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._fit_height)

    def _fit_height(self):
        """Once shown (the width is final then): height = what the content needs."""
        h = self.layout().heightForWidth(self.width())
        if h > 0:
            self.resize(self.width(), h)

    def _start(self):
        self._recheck.setEnabled(False)
        for lamp, status in self._rows.values():
            lamp.setStyleSheet(f"color:{_LAMP['checking']}; font-size:16px;")
            status.setText("checking…")
            status.setStyleSheet("color:#888;")
        self._probe = _ProbeThread(self)
        self._probe.done.connect(self._show)
        self._probe.start()

    def _show(self, results: dict):
        for key, (lamp, status) in self._rows.items():
            r = results.get(key)
            if not r:
                lamp.setStyleSheet(f"color:{_LAMP['down']}; font-size:16px;")
                status.setText("check failed")
                status.setStyleSheet(f"color:{_LAMP['down']};")
                continue
            if r["ok"]:
                state = "slow" if r["slow"] else "ok"
                status.setText(f"{r['ms']} ms{' · slow' if r['slow'] else ''}")
                status.setStyleSheet(f"color:{_LAMP[state]};")
                status.setToolTip("")
            else:
                state = "down"
                status.setText("unreachable")
                status.setStyleSheet(f"color:{_LAMP['down']};")
                status.setToolTip(r.get("error", ""))
            lamp.setStyleSheet(f"color:{_LAMP[state]}; font-size:16px;")
        self._recheck.setEnabled(True)
