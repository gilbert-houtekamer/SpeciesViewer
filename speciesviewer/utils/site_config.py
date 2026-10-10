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

Nothing personal is built in: an empty value means "not set up". Uploading
then stops with NOT_SET_UP_MSG instead of guessing a server, and the
OpenStreetMap User-Agent falls back to the project's own address.
"""

from utils.config import _config
from utils.version import __version__

_SECTION = "websettings"

# Sent to OpenStreetMap when no contact e-mail is set: identifies the project,
# never a person.
PROJECT_URL = "https://github.com/gilbert-houtekamer/SpeciesViewer"

NOT_SET_UP_MSG = (
    "Publishing is not set up on this computer yet. Fill in the upload server, "
    "account and website folder in File ▸ Settings ▸ Global Settings "
    "(Website and publishing).")


def _get(key: str) -> str:
    return _config.get(_SECTION, key, fallback="").strip()


def publishing_set_up() -> bool:
    """True when server, account and website folder are all filled in — the
    minimum for an upload to go to a known place."""
    return bool(sftp_host() and sftp_user() and sftp_remote_root())


def sftp_host() -> str:
    return _get("sftp_host")


def sftp_user() -> str:
    return _get("sftp_user")


def sftp_remote_root() -> str:
    return _get("sftp_remote_root").rstrip("/")


def site_base() -> str:
    """The public site root, e.g. 'https://example.org' — no trailing slash.
    A bare host in the config still yields a real URL; "" when not set."""
    base = _get("base_url").rstrip("/")
    if base and not base.startswith("http"):
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
    return f"SpeciesViewer/{__version__} ({contact_email() or PROJECT_URL})"
