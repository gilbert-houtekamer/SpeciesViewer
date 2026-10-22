"""One SpeciesViewer at a time (per Windows/macOS user).

ONE JOB: a second start — an impatient double-click while the splash is still
loading started four on the laptop (0.5.0 release test, 2026-10-22), all
writing the same log and settings — brings the running one to the front and
quits. A restart into another master (gui/master_switch.py, ENV_RESTARTING)
instead waits until the old one has closed.

Uses a QLocalServer: a named pipe on Windows, a socket file elsewhere. It
disappears with the process, so a crash never leaves a stale lock behind.
"""
import getpass
import os
import time

from PyQt6.QtNetwork import QLocalServer, QLocalSocket

ENV_RESTARTING = "SV_RESTARTING"
_server = None          # kept alive for the whole run
_window = None


def _name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"SpeciesViewer-{user}"


def _other_running(name: str) -> bool:
    sock = QLocalSocket()
    sock.connectToServer(name)
    if not sock.waitForConnected(300):
        return False
    sock.write(b"show")
    sock.flush()
    sock.waitForBytesWritten(300)
    sock.disconnectFromServer()
    return True


def claim() -> bool:
    """True when this process may run. False: another one is running (and has
    been asked to come to the front) — the caller exits. Call after
    QApplication exists, before the splash."""
    global _server
    name = _name()
    if os.environ.pop(ENV_RESTARTING, ""):
        deadline = time.monotonic() + 20      # the old one saves and closes
        while _other_running(name) and time.monotonic() < deadline:
            time.sleep(0.3)
    elif _other_running(name):
        return False
    QLocalServer.removeServer(name)           # a dead socket file (not Windows)
    _server = QLocalServer()
    _server.newConnection.connect(_bring_to_front)
    _server.listen(name)
    return True


def set_window(window) -> None:
    """The window a second start brings to the front."""
    global _window
    _window = window


def _bring_to_front() -> None:
    while _server is not None and _server.hasPendingConnections():
        conn = _server.nextPendingConnection()
        conn.disconnectFromServer()
    w = _window
    if w is None:
        return
    try:
        if w.isMinimized():
            w.showNormal()
        w.show()
        w.raise_()
        w.activateWindow()
    except RuntimeError:        # window already deleted (closing)
        pass
