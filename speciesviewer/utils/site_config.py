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
    "Choose where SpeciesViewer publishes your website — on this computer, or "
    "on your web server: File ▸ Settings… ▸ Global (Website and "
    "publishing).")

# Where the website goes (docs/plans/website_optional_plan.md). The user must
# choose; "" = not chosen yet.
LOCAL, SERVER = "local", "server"
LOCAL_PORT_DEFAULT = 8765


def publish_target() -> str:
    """LOCAL, SERVER, or "" when not chosen. A setup from before the choice
    existed, with its server fields filled, counts as SERVER."""
    chosen = _get("publish_target").lower()
    if chosen in (LOCAL, SERVER):
        return chosen
    if sftp_host() and sftp_user() and sftp_remote_root():
        return SERVER
    return ""


def is_local() -> bool:
    return publish_target() == LOCAL


def local_root():
    """The local website: per master, in app-data (fast local disk, each
    master its own site, never in Dropbox)."""
    from utils.masters import ACTIVE_DIR
    return ACTIVE_DIR / "website"


def local_port() -> int:
    try:
        return int(_get("local_port") or LOCAL_PORT_DEFAULT)
    except ValueError:
        return LOCAL_PORT_DEFAULT


def _get(key: str) -> str:
    return _config.get(_SECTION, key, fallback="").strip()


def publishing_set_up() -> bool:
    """True when the website has a known destination: this computer, or a
    server with server, account and website folder filled in."""
    target = publish_target()
    if target == LOCAL:
        return True
    return target == SERVER and bool(sftp_host() and sftp_user() and sftp_remote_root())


def sftp_host() -> str:
    return _get("sftp_host")


def sftp_user() -> str:
    return _get("sftp_user")


def sftp_remote_root() -> str:
    return _get("sftp_remote_root").rstrip("/")


def site_base() -> str:
    """The site root, e.g. 'https://example.org' — no trailing slash. For the
    local website it is the local server's address. A bare host in the config
    still yields a real URL; "" when not set."""
    if is_local():
        # "localhost" reads better than 127.0.0.1; the server stays bound to
        # 127.0.0.1, and browsers fall back to it from IPv6 by themselves.
        return f"http://localhost:{local_port()}"
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
