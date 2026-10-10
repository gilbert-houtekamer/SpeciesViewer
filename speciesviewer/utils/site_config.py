"""Where this installation publishes, and who it says it is — the one place.

ONE JOB: answer "which server, which account, which folder, which web address,
which contact" for every module that uploads, links to the website or talks to
OpenStreetMap. Nothing else in the tree may spell out a host, account or
address.

Values come from the machine config, section [websettings]:

    sftp_host         SFTP server the website is uploaded to
    sftp_user         account on that server
    sftp_remote_root  the website's folder on that server
    base_url          the public web address, e.g. https://example.org
    contact_email     sent to OpenStreetMap with every map/place request,
                      as its usage policy requires

The password stays where it was ([websettings] Sftppwd, passed to the batch
steps through svconfig.ini).

0.4 plan, step 1 of 3: the author's own values are still the fallback, so
nothing changes in behaviour yet. Step 3 removes them, after which an empty
value means "not configured" rather than somebody else's website.
"""

from utils.config import _config
from utils.version import __version__

_SECTION = "websettings"

_FALLBACK = {
    "sftp_host": "houtfp.ssh.transip.me",
    "sftp_user": "houtekamercom",
    "sftp_remote_root": "/data/sites/web/houtekamercom/www",
    "base_url": "https://houtekamer.com",
    "contact_email": "gilbert@houtekamer.com",
}


def _get(key: str) -> str:
    value = _config.get(_SECTION, key, fallback="").strip()
    return value or _FALLBACK[key]


def sftp_host() -> str:
    return _get("sftp_host")


def sftp_user() -> str:
    return _get("sftp_user")


def sftp_remote_root() -> str:
    return _get("sftp_remote_root").rstrip("/")


def site_base() -> str:
    """The public site root, e.g. 'https://example.org' — no trailing slash.
    A bare host in the config still yields a real URL."""
    base = _get("base_url").rstrip("/")
    if not base.startswith("http"):
        base = "https://" + base
    return base


def site_host() -> str:
    """Just the host of the public site, e.g. 'example.org' — for the
    connection probe and for display."""
    host = site_base().split("://", 1)[-1]
    return host.split("/", 1)[0]


def contact_email() -> str:
    return _get("contact_email")


def user_agent() -> str:
    """The User-Agent for OpenStreetMap tiles and Nominatim. Their policy asks
    for the application plus a contact, so each installation sends its own."""
    return f"SpeciesViewer/{__version__} ({contact_email()})"
