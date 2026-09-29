"""Is DxO PhotoLab running right now? One job: answer that, cheaply, on Windows
and macOS.

Why SpeciesViewer needs to know (tested live 2026-09-29, see
docs/plans/photolab_sidecar_fight.md): any .dop SV writes wakes a running
PhotoLab, which then writes its own database out over the .xmp. When that
database is behind — a colour set in SV that PhotoLab has not read yet — the
colour is replaced by PhotoLab's old one. So while PhotoLab runs, SV holds its
.dop edits back and hands them over at "Open in PhotoLab".

A failed check answers False ("not running"): that is the old behaviour, and
it keeps SV usable on a machine without PhotoLab or without tasklist/pgrep.
"""
import logging
import subprocess
import sys

logger = logging.getLogger(__name__)

_WIN_IMAGE = "DxO.PhotoLab.exe"
_MAC_PATTERN = "DxO PhotoLab"


def photolab_running() -> bool:
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {_WIN_IMAGE}", "/NH", "/FO", "CSV"],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            return _WIN_IMAGE.lower() in out.lower()
        if sys.platform == "darwin":
            return subprocess.run(["pgrep", "-f", _MAC_PATTERN],
                                  capture_output=True, timeout=5).returncode == 0
    except Exception as e:
        logger.warning(f"photolab_running: could not check ({e}); assuming not running")
    return False
