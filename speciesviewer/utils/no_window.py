"""subprocess.run without a console window flashing up.

ONE JOB: the packaged SpeciesViewer.exe has no console, so on Windows every
console child (exiftool) gets a NEW console window of its own — a black flash
for each colour, rating or species written. Under F5 in VS Code the child
inherits the terminal and nothing shows, which is why it went unseen until the
0.5.0 test on a clean machine (2026-10-22). Elsewhere this is plain
subprocess.run.
"""
import subprocess
import sys

NO_WINDOW = ({"creationflags": subprocess.CREATE_NO_WINDOW}
             if sys.platform == "win32" else {})


def run(*args, **kwargs):
    """subprocess.run, with CREATE_NO_WINDOW added on Windows."""
    if NO_WINDOW:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | NO_WINDOW["creationflags"]
    return subprocess.run(*args, **kwargs)
