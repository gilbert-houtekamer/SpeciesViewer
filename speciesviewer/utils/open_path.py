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


def reveal_group(folder, query) -> bool:
    """Open the OS file manager in `folder`, pre-filtered to `query` (typically
    the UPN, e.g. "2015 juli nl dsc_6297") so only the picture's files show.

    Windows: launches Explorer with a `search-ms:` URI — the same search Explorer
    runs when you type the UPN into its search box from inside the folder.
    macOS/Linux: falls back to opening the folder unfiltered; Finder/xdg-open
    don't take a search query on the command line.
    """
    if sys.platform.startswith("win"):
        import os
        from urllib.parse import quote
        try:
            folder_q = quote(str(folder), safe="")
            query_q = quote(str(query), safe="")
            # Dispatch the URI via ShellExecuteEx (os.startfile) — passing it as
            # an explorer.exe argv made Windows split the URL on the `&` and try
            # to open the orphan `location:...` part as its own protocol, which
            # produced the "get an app for this 'location' link" dialog.
            url = f"search-ms:query={query_q}&crumb=location:{folder_q}"
            os.startfile(url)  # noqa: cross-platform branch
            return True
        except Exception as e:
            logger.warning(f"reveal_group failed for {folder!r}/{query!r}: {e}")
    return open_path(folder)


def open_in_browser(path_or_url) -> bool:
    """Open a local HTML file (or URL) in a web BROWSER — not whatever the OS
    has associated with .html. On Windows os.startfile/webbrowser honour the
    .html file association, which the user may have set to an editor (e.g.
    Notepad++); so launch a real browser executable directly. macOS/Linux open a
    browser by default, so just use webbrowser there."""
    import webbrowser
    s = str(path_or_url)
    url = s if "://" in s else Path(s).as_uri()
    if sys.platform.startswith("win"):
        import os
        rel = [r"Microsoft\Edge\Application\msedge.exe",
               r"Google\Chrome\Application\chrome.exe",
               r"Mozilla Firefox\firefox.exe"]
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(var)
            if not base:
                continue
            for r in rel:
                exe = os.path.join(base, r)
                if os.path.isfile(exe):
                    try:
                        subprocess.Popen([exe, url])
                        return True
                    except Exception as e:
                        logger.warning(f"open_in_browser: {exe} failed: {e}")
    try:
        return bool(webbrowser.open(url, new=2))
    except Exception as e:
        logger.warning(f"open_in_browser failed for {url}: {e}")
        return False


def launch_app_with_file(app_path, file_path) -> bool:
    """Launch an external app (e.g. DxO PhotoLab) on a file. On macOS a `.app`
    bundle is opened via `open -a`; elsewhere the executable is run directly.

    The launched process gets its working directory set to the file's folder —
    Explorer / Finder do the same when they dispatch a file to an app, and some
    apps (DxO PhotoLab populates its filmstrip from siblings) rely on that
    context. Without it DxO opens the single file in isolation.
    """
    app = str(app_path)
    target = Path(file_path)
    parent = str(target.parent) if target.parent != target else None
    try:
        if sys.platform == "darwin" and app.endswith(".app"):
            subprocess.Popen(["open", "-a", app, str(target)], cwd=parent)
        else:
            subprocess.Popen([app, str(target)], cwd=parent)
        return True
    except Exception as e:
        logger.warning(f"launch_app_with_file failed ({app} on {target}): {e}")
        return False
