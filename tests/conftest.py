"""Test setup: an isolated, throw-away world BEFORE anything from SpeciesViewer
is imported.

Importing utils.config creates the settings folders (and would migrate files
from the repository root), so every per-user location — %LOCALAPPDATA% on
Windows, the home folder on macOS, XDG on Linux — points at a temporary folder
for the whole session. Your real settings are never read or written by a test.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SV = REPO / "speciesviewer"

_SANDBOX = Path(tempfile.mkdtemp(prefix="sv-tests-"))
for var in ("LOCALAPPDATA", "USERPROFILE", "HOME", "XDG_DATA_HOME"):
    os.environ[var] = str(_SANDBOX)
os.environ.pop("SV_MASTER", None)                 # no master chosen from outside
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # no windows on screen

# SpeciesViewer.py puts speciesviewer/ on sys.path; its packages are top-level.
for p in (str(SV), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SANDBOX, ignore_errors=True)


SANDBOX = _SANDBOX
