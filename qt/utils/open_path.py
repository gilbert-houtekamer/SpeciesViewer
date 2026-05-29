"""Cross-platform helpers to open a file/folder in the OS file manager and to
launch an external application with a file argument.

Centralises the platform branching the app needs as it moves Windows → Mac
(see PORTABILITY_AUDIT.md section A). Keep new "open in Explorer / Finder" and
"launch DxO PhotoLab" calls going through here rather than hardcoding
`explorer`/`os.startfile`.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("speciesviewer")


def open_path(path) -> bool:
    """Open a file or folder in the OS file manager. Returns True on launch."""
    p = str(path)
    try:
        if sys.platform.startswith("win"):
            import os
            os.startfile(p)  # noqa: cross-platform branch
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
        return True
    except Exception as e:
        logger.warning(f"open_path failed for {p}: {e}")
        return False


def reveal_path(path) -> bool:
    """Reveal a file in the file manager (select it inside its folder). Falls
    back to opening the containing folder when select isn't supported."""
    p = Path(path)
    try:
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", str(p)])
            return True
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)])
            return True
    except Exception as e:
        logger.warning(f"reveal_path failed for {p}: {e}")
    # Linux (and any failure above): open the containing directory.
    target = p.parent if p.exists() or p.suffix else p
    return open_path(target)


def launch_app_with_file(app_path, file_path) -> bool:
    """Launch an external app (e.g. DxO PhotoLab) on a file. On macOS a `.app`
    bundle is opened via `open -a`; elsewhere the executable is run directly."""
    app = str(app_path)
    target = str(file_path)
    try:
        if sys.platform == "darwin" and app.endswith(".app"):
            subprocess.Popen(["open", "-a", app, target])
        else:
            subprocess.Popen([app, target])
        return True
    except Exception as e:
        logger.warning(f"launch_app_with_file failed ({app} on {target}): {e}")
        return False
