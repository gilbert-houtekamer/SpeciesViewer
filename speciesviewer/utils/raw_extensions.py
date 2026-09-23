"""The one table of picture file extensions. Every "is this a RAW?" test imports
from here; nothing else in the tree may define its own.

WHY THIS EXISTS. On 2026-08-23 an audit counted **39 literal RAW-extension sets in
25 files with 20 distinct values** (docs/plans/raw_extension_table.md). They did not
agree, and the disagreement was not decorative:

  * `.cr3` was missing from five live paths — preview_provider, rating_provider,
    rating_editor, image_viewer, species_editor — so every Canon mirrorless body since
    2018 was half-invisible. preview_provider defined BOTH lists: a CR3 was a RAW for
    the PhotoLab path and not a RAW for the preview path, in the same module.
  * `.nrw` was in 1 set of 39. A card of 3863 files reported 2413 pictures and left
    218 Coolpix raws behind, silently, because one list did not know the extension.
  * `.ori` was in none of them at all.

The lesson is not "we forgot an extension". It is that a literal set of extensions is
a fact about the world that drifts the moment it is written down twice.

THE INVARIANT: this module is the only place in the tree where a RAW extension appears
as a literal. `tools/svr_extension_audit.py` checks that and fails if it stops being
true. If you are about to type `{".nef", ".orf", ...}` somewhere else, import from here
instead — and if the set you need is genuinely a new concept, add it here and name it.

CLASSIFICATION IS NOT RETENTION. `.dng` is a RAW: a vendor-independent raw format, and
what DxO PureRAW emits. That it is separately dropped by the master's file policy
("keep .dop, drop .dng") is a decision about what to KEEP, made elsewhere. Do not
express a retention policy by leaving an extension out of a classification set — that
is how the `.nrw` incident happened.

Extensions are stored dotted and lower-case. Callers should not care: every helper
takes a Path or a str, with or without a leading dot, in any case.
"""

from pathlib import Path

# --- camera raw ------------------------------------------------------------------
# Decisions 2026-08-23 (user):
#   .dng  IN  — a raw format in its own right (vendor-independent; PureRAW output).
#   .ori  IN  — OM System's uncompressed sibling of .orf. Was in NO list before.
#   .nrw  IN  — Nikon Coolpix. Was in 1 list of 39; that is what cost 218 pictures.
#   .raw  IN  — approved 2026-08-23 so the delete phase stays a pure widening.
#   .pef / .srw  OUT — Pentax and Samsung. Nobody here shoots either, and the ch.21
#                      per-body checklist must not claim support never tested. Adding
#                      one back is now a one-line change to one file.
RAW = frozenset({
    ".arw",   # Sony
    ".cr2",   # Canon, DSLR era
    ".cr3",   # Canon, mirrorless — R5, R5 Mark II, R7 ...
    ".dng",   # vendor-independent, incl. DxO PureRAW output
    ".nef",   # Nikon
    ".nrw",   # Nikon Coolpix
    ".orf",   # Olympus / OM System
    ".ori",   # OM System, uncompressed sibling of .orf
    ".raf",   # Fujifilm
    ".raw",   # generic (older Panasonic/Leica). Kept ONLY because
              # engine.trip_delete_scan listed it: dropping it would have
              # NARROWED a delete scanner's idea of what a picture is, which
              # is the direction that cost 218 files once already. There are
              # 0 on disk today; the cost of keeping it is nil.
    ".rw2",   # Panasonic
})

# --- HEIC ------------------------------------------------------------------------
# Deliberately NOT part of RAW, and deliberately its own name rather than being folded
# into JPEG. PhotoLab 9 does accept HEIC as input, but treats it essentially as a JPG.
# Whether HEIC is a "raw" for our purposes — specifically whether it has anywhere
# durable to store a rating — was the open question in backlog ch.7. ANSWERED
# 2026-09-23: yes. engine.sidecarlib.sidecar_opts(), the recipe already used for
# every RAW, works on a HEIC unchanged — verified on an iPhone 16 Pro file, which
# produced a .xmp carrying DateTimeOriginal WITH its UTC offset, GPS and Model,
# and round-tripped Rating / Label / Subject / Description through exiftool. So a
# HEIC groups and is annotated exactly like a RAW (see RAW_OR_HEIC, and
# sv0ReadSDCard.SIDECAR_EXTENSIONS), while staying OUT of the RAW-only paths that
# assume a develop step: it has no .dop and SV must never invent one.
HEIC = frozenset({".heic", ".heif"})

JPEG = frozenset({".jpg", ".jpeg"})

# Rendered formats that are viewable but are never a camera original.
OTHER_IMAGE = frozenset({".png", ".tif", ".tiff"})

# --- derived sets ----------------------------------------------------------------

#: An "original" — the file a rating, colour label and crop belong to.
RAW_OR_HEIC = RAW | HEIC

#: What we will hand to DxO PhotoLab as a raw.
#: UNVERIFIED: .nrw is excluded because no pre-existing list included it, so this
#: preserves today's behaviour exactly rather than guessing new behaviour. PhotoLab's
#: actual Coolpix support has not been tested — there are real .nrw files in the
#: master to settle it with.
DXO_RAW = RAW - {".nrw"}

#: A photo a CAMERA produced — the originals plus their JPGs, but never a
#: screenshot or a scan. The distinction is real: a .png Apple screenshot carries
#: an auto "Screenshot" headline that must not be queued for translation.
CAMERA_PHOTO = RAW_OR_HEIC | JPEG

#: The metadata files that ride ALONGSIDE a picture. Not pictures themselves, but
#: part of one: a delete that takes the RAW must take these too, or it leaves
#: orphans behind. engine.trip_delete_scan is why this is named here -- its
#: PICTURE_EXTS includes them deliberately, and a mechanical substitution of
#: PICTURE would have silently dropped them (caught by dry-run, 2026-08-24).
SIDECAR = frozenset({".xmp", ".dop"})

#: Anything we can display.
PICTURE = RAW_OR_HEIC | JPEG | OTHER_IMAGE

# --- video -------------------------------------------------------------------
# Deliberately its own set, not folded into PICTURE: a video needs a frame grab
# or a media player, never a plain image decode, so every caller that branches
# on "can I just open this as an image" needs to know the difference. Kept to
# what sv0ReadSDCard.py actually ingests off a card today rather than every
# container format that exists.
VIDEO = frozenset({".mov", ".mp4"})


def bare(exts) -> frozenset:
    """The same set without the leading dots.

    The tree genuinely holds extensions both ways and neither is wrong: the CSV's
    `WindowsExtension` column is bare ("nef"), `Path.suffix` is dotted (".nef"). That
    is a fact about the data, so it is converted HERE, once, rather than being a second
    hand-maintained table that can drift from the first.

        _DXO_RAW_EXTS = bare(DXO_RAW)     # {"nef", "orf", ...}
    """
    return frozenset(e.lstrip(".") for e in exts)


def ext_of(p) -> str:
    """Normalised, dotted, lower-case extension of `p`.

    Accepts what the tree actually holds: a Path, a filename, a bare extension with
    or without its dot, in any case. Returns "" for anything with no extension.

        ext_of(Path("DSC_0001.NEF")) -> ".nef"
        ext_of("DSC_0001.NEF")       -> ".nef"
        ext_of("NEF")                -> ".nef"
        ext_of(".NEF")               -> ".nef"
    """
    if isinstance(p, Path):
        return p.suffix.lower()
    s = str(p or "").strip()
    if not s:
        return ""
    if "." in s:
        s = s[s.rfind("."):]
        return s.lower() if len(s) > 1 else ""
    return "." + s.lower()


def is_raw(p) -> bool:
    """True for a camera raw. HEIC is NOT a raw — see the module docstring."""
    return ext_of(p) in RAW


def is_heic(p) -> bool:
    return ext_of(p) in HEIC


def is_raw_or_heic(p) -> bool:
    """True for an "original": the file a rating and crop belong to."""
    return ext_of(p) in RAW_OR_HEIC


def is_dxo_raw(p) -> bool:
    """True for a raw we will hand to PhotoLab."""
    return ext_of(p) in DXO_RAW


def is_jpeg(p) -> bool:
    return ext_of(p) in JPEG


def is_picture(p) -> bool:
    """True for anything we can display, raw or rendered."""
    return ext_of(p) in PICTURE


def is_video(p) -> bool:
    return ext_of(p) in VIDEO
