"""Show the local website in the browser (docs/plans/website_optional_plan.md).

ONE JOB: serve the open master's local website folder on
http://127.0.0.1:<port>/ while SpeciesViewer runs, so its pages — which link
from the site root (/nature/…, /trips/<trip>/…) — work exactly as on the web.

Only on 127.0.0.1: nothing outside this computer can reach it. A daemon thread:
it ends with the app. Started at launch and when the user switches to "this
computer" in Global Settings; harmless to call again.
"""

from __future__ import annotations

import logging
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("speciesviewer")
_server: ThreadingHTTPServer | None = None


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):      # one line per request is noise
        pass


def ensure_started() -> bool:
    """Start the server when the website goes to this computer. True when it
    is running (or already was); False when not local or the port is taken."""
    global _server
    from utils import site_config
    if not site_config.is_local():
        return False
    if _server is not None:
        return True
    root = site_config.local_root()
    root.mkdir(parents=True, exist_ok=True)
    port = site_config.local_port()
    try:
        _server = ThreadingHTTPServer(
            ("127.0.0.1", port), partial(_QuietHandler, directory=str(root)))
    except OSError as e:
        logger.warning(f"Local website: port {port} is in use ({e}). Another "
                       "SpeciesViewer may be running; pages open there instead.")
        return False
    threading.Thread(target=_server.serve_forever, name="LocalWebsite",
                     daemon=True).start()
    logger.info(f"Local website: {site_config.site_base()}/ shows {root}")
    return True
