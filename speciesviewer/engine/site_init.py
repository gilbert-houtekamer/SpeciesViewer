"""Put the starter pages on the website as soon as there is a website.

ONE JOB: for the master and the open trip and album, supply the site part's
skeleton (intro pages, placeholder pages, trip index, 'includes web') when the
site does not have it yet. Every repository builds that skeleton in its own
web/ folder when it is created; until now only the first PUBLISH copied it to
the site, so a freshly set-up website answered 404 on /nature/intro-nl.html
and the trip's intro (Gilbert, 2026-10-12).

Runs when the website is configured (Settings ▸ Save) and, for the website on
this computer, at every start — a new master is opened by a restart, and the
check is one folder listing per site part.

INVARIANT: never overwrites. A site part that already has its intro page is
left alone (sv1MakeReposity.ensure_skeleton), and the trip index is only
supplied where the site has none (publish_skeleton).
"""

from __future__ import annotations

import logging

from utils.config import _config

logger = logging.getLogger("speciesviewer")

# (config section, repository key, web-root key) for each site part.
_SITE_PARTS = (
    ("master", "masterbiotadatabase", "masterbiotawebroot"),
    ("trip", "tripbiotadatabase", "tripbiotawebroot"),
    ("virttrip", "virttripbiotadatabase", "virttripbiotawebroot"),
)


def site_parts() -> list[tuple[str, str]]:
    """(repository folder, web root) of every site part this master has open."""
    parts = []
    for section, repo_key, web_key in _SITE_PARTS:
        repo = _config.get(section, repo_key, fallback="").strip()
        web = _config.get(section, web_key, fallback="").strip()
        if repo and web:
            parts.append((repo, web))
    return parts


def initialize_site(password: str | None = None) -> int:
    """Supply every missing skeleton; returns how many site parts got one.
    Does nothing (0) when no website is set up. Never raises: a website that
    cannot be reached is logged, and the next publish supplies the pages."""
    from utils import site_config
    if not site_config.publishing_set_up():
        return 0
    parts = site_parts()
    if not parts:
        return 0
    if password is None:
        password = _config.get("websettings", "Sftppwd", fallback="").strip("'")
    from batch.sv1MakeReposity import ensure_skeleton
    from engine import sftplib
    supplied = 0
    try:
        sftp = sftplib.open_site(password=password)
        sftp.connect()
    except Exception as e:
        logger.warning(f"Website not reached, starter pages not checked: {e}. "
                       "Publishing supplies them later.")
        return 0
    try:
        for repo, web in parts:
            try:
                if ensure_skeleton(sftp, repo, web):
                    supplied += 1
            except Exception as e:
                logger.warning(f"Starter pages for {web} could not be supplied: {e}")
    finally:
        try:
            sftp.disconnect()
        except Exception:
            pass
    if supplied:
        logger.info(f"Website: starter pages supplied for {supplied} part(s) of the site.")
    return supplied
