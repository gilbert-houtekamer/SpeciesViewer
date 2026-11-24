"""File ▸ Settings ▸ AI Models… — download the optional AI models.

ONE JOB: say whether the AI models are on this computer, and fetch them
(engine.ai_download) with a progress bar, on a worker thread. A stopped or
broken download carries on where it stopped the next time.

INVARIANT: optional — nothing here is needed for the rest of SpeciesViewer.
Only app-data `ai/` is written, and a model file only appears once it is whole
and checked (engine.ai_download).
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QProgressBar,
                             QPushButton, QVBoxLayout)

from gui import theme
from gui.widgets import wrapped

INTRO = (
    "With the AI models SpeciesViewer can suggest which pictures of a day could go "
    "and name the species in them (the AI… button in culling, and the Sharpness tab "
    "of the Editor). They are optional: everything else works without them.\n\n"
    "The download is about 3.2 GB and needs about 4 GB on the disk. After that "
    "everything runs on this computer, also without internet; nothing about your "
    "pictures is sent anywhere.")


def state() -> tuple[str, str]:
    """(mark, text) for the Setup checklist: "ok" or "optional"."""
    from engine import ai_models
    if not ai_models.runtime_available():
        return "optional", ("Not possible in this installation: the AI program "
                            "library (ONNX Runtime) is missing.")
    if ai_models.available():
        return "ok", f"Installed — {ai_models.ai_dir()}"
    if ai_models.missing_files() != list((ai_models.DETECTOR_FILE,) + ai_models.BIOCLIP_FILES):
        return "optional", "Partly downloaded — Download carries on where it stopped."
    return "optional", "Not installed — species suggestions and culling advice (3.2 GB)."


# Running downloads, held here and not by the dialog: closing the window (or the
# Settings window around this page) mid-download only asks the thread to stop;
# Qt must not destroy a QThread that is still running.
_running: list = []


class _Fetch(QThread):
    step = pyqtSignal(object, object, str)      # done, total (bytes; may pass 2 GB), file
    done = pyqtSignal(str)                      # "" = all good, else what went wrong

    def __init__(self):
        super().__init__()
        self.stop = False
        _running.append(self)
        self.finished.connect(lambda: _running.remove(self))

    def run(self):
        from engine import ai_download, ai_models
        try:
            self.step.emit(0, 0, "Reading the download list…")
            m = ai_download.manifest()
            self.step.emit(0, 0, "Checking what is already on this computer…")
            ai_download.download(m, progress=lambda d, t, n: self.step.emit(d, t, n),
                                 cancelled=lambda: self.stop)
            ai_models.reset()
            self.done.emit("")
        except ai_download.Cancelled:
            self.done.emit("Stopped. Download carries on where it stopped.")
        except Exception as e:      # network, disk, checksum — said in words
            self.done.emit(f"The download did not finish: {e}\n"
                           "Download carries on where it stopped.")


class AiModelsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI Models")
        lay = QVBoxLayout(self)
        lay.addWidget(wrapped(INTRO, self))
        self._state = wrapped("", self)
        self._state.setStyleSheet("font-weight: bold;")
        lay.addWidget(self._state)
        self._bar = QProgressBar()
        self._bar.setRange(0, 1000)
        self._bar.setTextVisible(False)
        self._bar.hide()
        lay.addWidget(self._bar)
        self._line = QLabel("")
        self._line.setStyleSheet(f"color: {theme.INK_FAINT};")
        lay.addWidget(self._line)
        row = QHBoxLayout()
        row.addStretch()
        self._go = QPushButton("Download")
        self._go.clicked.connect(self._start)
        self._stop = QPushButton("Stop")
        self._stop.clicked.connect(self._on_stop)
        self._stop.hide()
        self._close = QPushButton("Close")
        self._close.clicked.connect(self.reject)
        for b in (self._go, self._stop, self._close):
            row.addWidget(b)
        lay.addLayout(row)
        self._job = None
        self._t0 = 0.0
        self.resize(560, self.sizeHint().height())
        self._show_state()

    def _show_state(self):
        from engine import ai_models
        mark, text = state()
        self._state.setText(text)
        self._go.setVisible(ai_models.runtime_available() and mark != "ok")

    def _start(self):
        self._go.hide()
        self._stop.show()
        self._bar.show()
        self._bar.setValue(0)
        self._t0 = time.time()
        self._job = _Fetch()
        self._job.step.connect(self._on_step)
        self._job.done.connect(self._on_done)
        self._job.start()

    def _on_step(self, done, total, name):
        if not total:
            self._line.setText(name)
            return
        self._bar.setValue(int(1000 * done / total))
        secs = max(1e-3, time.time() - self._t0)
        speed = done / secs
        left = (total - done) / speed if speed > 0 else 0
        self._line.setText(f"{done / 1e9:.2f} of {total / 1e9:.2f} GB · {speed / 1e6:.1f} MB/s"
                           f" · about {left / 60:.0f} min to go · {name}")

    def _on_stop(self):
        if self._job:
            self._job.stop = True
            self._stop.setEnabled(False)
            self._line.setText("Stopping…")

    def _on_done(self, problem: str):
        self._job = None
        self._stop.hide()
        self._stop.setEnabled(True)
        self._bar.hide()
        self._line.setText(problem or f"Done in {(time.time() - self._t0) / 60:.0f} min. "
                                      "The AI is ready to use.")
        self._show_state()

    def stop_work(self):
        """Closing mid-download: stop it; the .part stays for next time."""
        if self._job is not None:
            self._job.stop = True

    def done(self, r):
        self.stop_work()
        super().done(r)


def show(parent) -> None:
    """The AI models tab of the Settings window."""
    from settings.settings_window import SettingsWindow
    SettingsWindow(parent, tab="AI models").exec()
