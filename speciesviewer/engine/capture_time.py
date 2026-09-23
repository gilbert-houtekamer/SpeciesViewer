"""ONE job: say when a file was captured, from its exiftool metadata.

INVARIANT: the value returned is the camera's/phone's own LOCAL clock at the
moment of capture, or nothing. It is never a file-system date, never an export
date, and never a placeholder — a caller that gets None is free to fall back to
file mtime, but it is never handed a date that only looks real.

Why this exists as one function with one tag order, rather than a branch at
each call site: a movie's capture time lives in a different tag than a photo's,
the tag a phone fills is not the tag a camera fills, and one of those tags
lies. Measured on this library (3,798 movies under CurrentPictures):

  EXIF:DateTimeOriginal   stills. Authoritative whenever present.

  QuickTime:CreationDate  what an iPhone writes, WITH an explicit UTC offset
                          ("2026:08:30 08:24:19-04:00"). This is the real
                          capture moment. Cameras do not write this tag at all.

  QuickTime:CreateDate    what a camera writes, and it writes it in local time
                          (verified: an OM-1 movie at 16:02:14 sits between its
                          own stills, whose DateTimeOriginal reads 16:05:48 at
                          offset -04:00). An iPhone ALSO writes it, but in UTC
                          — and on 101 movies here it is not the capture date
                          at all but the iCloud EXPORT date, off by as much as
                          three months (IMG_2003.MOV: CreateDate 2020:05:02,
                          actual capture 2020:02:10). So it is the LAST resort,
                          right for every camera and only a guess for a phone.

Plus the placeholder guard: 14 movies here carry "0000:00:00 00:00:00", which
strptime rejects outright — written to the CSV it becomes a crash in whichever
step next parses a date (see sv6Freeze.getDateFromPictures). Anything that does
not parse as a real date is treated as absent.

The offset is deliberately NOT carried into the returned value. Every consumer
of EXIF:DateTimeOriginal in this codebase wants the local wall clock — the day
folder, the chronological sort, the blog's day grouping — and that is what the
naive form gives them, in the one format data_files.csv has always held. The
offset stays available in the file itself for anyone who ever needs absolute
time; a sidecar keeps it (see sv0ReadSDCard's XMP note), a movie has no sidecar.
"""
from datetime import datetime

# The exiftool arguments that make the tags below available. Callers pass THIS
# rather than their own list, so the tag set and the preference order can never
# drift apart: asking for fewer tags than capture_datetime() reads would
# silently demote a file to its fallback.
EXIFTOOL_ARGS = [
    "-DateTimeOriginal",
    "-QuickTime:CreationDate",
    "-QuickTime:CreateDate",
]

# Preference order — see the module docstring for why each one sits here.
_TAGS = (
    "EXIF:DateTimeOriginal",
    "QuickTime:CreationDate",
    "QuickTime:CreateDate",
)

_FORMAT = "%Y:%m:%d %H:%M:%S"
# Below this, a date is a placeholder rather than a photograph. Photography
# starts in the 1820s; no file in this library predates digital.
_MIN_YEAR = 1900


def _parse(value):
    """A real local datetime from one tag value, or None.

    Takes the first 19 characters, which is the convention throughout: the
    date may carry a trailing UTC offset ("…08:24:19-04:00") and every reader
    here is expected to see "…08:24:19".
    """
    if not value:
        return None
    text = str(value).strip()[:19]
    try:
        dt = datetime.strptime(text, _FORMAT)
    except ValueError:
        return None                       # "0000:00:00 00:00:00", "", junk
    return dt if dt.year >= _MIN_YEAR else None


def capture_datetime(meta):
    """The capture moment as a naive local datetime, or None if the file does
    not state one. `meta` is one exiftool record read with -G (group-prefixed
    keys), e.g. a row from exifcache or ExifToolHelper.get_metadata()."""
    for tag in _TAGS:
        dt = _parse(meta.get(tag))
        if dt is not None:
            return dt
    return None


def capture_datetime_str(meta):
    """capture_datetime() in the form data_files.csv stores,
    "YYYY:MM:DD HH:MM:SS", or "" when the file states no capture time."""
    dt = capture_datetime(meta)
    return dt.strftime(_FORMAT) if dt else ""
