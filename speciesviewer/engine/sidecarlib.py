"""Transfer crop and exposure between DxO DOP and Lightroom XMP sidecar files."""

import os
import re
import xml.etree.ElementTree as ET
import logging
from datetime import datetime, timezone

logger = logging.getLogger("speciesviewer")

# XMP namespaces
NS = {
    'x': 'adobe:ns:meta/',
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'crs': 'http://ns.adobe.com/camera-raw-settings/1.0/',
}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


def _dop_now():
    """PhotoLab's timestamp format: ISO 8601 in UTC with 7 fractional digits and a
    trailing Z (.NET 100-ns ticks), e.g. "2026-09-24T18:50:08.7117131Z". UTC, not
    local — the Z means it, and a local stamp would read two hours in the future."""
    dt = datetime.now(timezone.utc)
    return f"{dt:%Y-%m-%dT%H:%M:%S}.{dt.microsecond:06d}0Z"


def _touch_dop_stamps(text):
    """Declare, inside the .dop, that we just changed it — WITHOUT this, PhotoLab
    ignores what we wrote.

    PhotoLab is database-first: the .dop is an interchange file, its catalogue is
    the live store. Before adopting item-level metadata from a sidecar (ColorLabel,
    Rating — the fields beside `Name`) it asks whether the sidecar's record is newer
    than its own, and it asks the FILE, not the filesystem. We were changing the
    value and leaving `ModificationDate` at whatever PhotoLab last wrote, so PL
    compared it against its database, found them equal, concluded there was nothing
    to import, and dropped the item — the crop travelling with it.

    Confirmed live (2026-09-24): adding an hour to the stamp by hand made PhotoLab
    pick up a colour it had ignored, and an explicit "Load corrections from DOP",
    which bypasses the check, showed the colour was written correctly all along.

    Two stamps, both true when we write: `Date` at Sidecar level (when this file was
    written) and the item's `ModificationDate` (when this record changed). Only the
    FIRST of each is touched, matching every other writer here — a .dop holds one
    item in practice (0 of 7068 real files carry a second ColorLabel or Rating), and
    if one ever held two, advancing the stamp of an item we did not edit would be a
    lie. `CreationDate` is the picture's and is never touched."""
    now = _dop_now()
    text, _ = re.subn(r'^(\s*Date\s*=\s*)"[^"]*"', rf'\g<1>"{now}"', text,
                      count=1, flags=re.M)
    text, _ = re.subn(r'(\bModificationDate\s*=\s*)"[^"]*"', rf'\g<1>"{now}"', text,
                      count=1)
    return text


def read_dop_overrides(dop_path):
    """Read crop and exposure from DOP Overrides section."""
    with open(dop_path, "r", encoding="utf-8") as f:
        text = f.read()

    result = {"exposure": None, "crop": None, "angle": None, "mtime": os.path.getmtime(dop_path)}

    # Find Overrides block — find opening brace, then match balanced braces
    start = text.find('Overrides')
    if start == -1:
        return result
    brace_start = text.index('{', start)
    depth = 0
    brace_end = brace_start
    for i in range(brace_start, len(text)):
        if text[i] == '{': depth += 1
        elif text[i] == '}': depth -= 1
        if depth == 0:
            brace_end = i
            break
    block = text[brace_start+1:brace_end]

    # Exposure
    em = re.search(r'ExposureBias\s*=\s*(-?[\d.]+)', block)
    ea = re.search(r'ExposureActive\s*=\s*(true|false)', block)
    if em and ea and ea.group(1) == "true":
        result["exposure"] = float(em.group(1))

    # Crop
    ca = re.search(r'CropActive\s*=\s*(true|false)', block)
    cm = re.search(r'CropRect\s*=\s*\{\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)', block)
    if ca and ca.group(1) == "true" and cm:
        left, top, width, height = [float(cm.group(i)) for i in range(1, 5)]
        result["crop"] = {"left": left, "top": top, "width": width, "height": height}

    # Horizon angle (DxO uses positive = clockwise correction)
    ha = re.search(r'KeystoningHorizonActive\s*=\s*(true|false)', block)
    hv = re.search(r'KeystoningHorizon\s*=\s*(-?[\d.]+)', block)
    if ha and ha.group(1) == "true" and hv:
        result["angle"] = float(hv.group(1))

    return result


def read_xmp_edits(xmp_path):
    """Read crop and exposure from the XMP crs: fields.

    ⚠ A PhotoLab / exiftool XMP splits its metadata across MANY rdf:Description
    blocks (one per namespace group) — the crs: crop is often NOT in the first.
    Reading only the first block (as this did) silently missed the crop, so the
    grid showed a stale cached thumbnail while the large view opened uncropped.
    Search EVERY rdf:Description, and accept the value as an attribute OR a child
    element (PhotoLab writes elements, our own writer writes attributes)."""
    result = {"exposure": None, "crop": None, "angle": None, "mtime": os.path.getmtime(xmp_path)}

    tree = ET.parse(xmp_path)
    descs = tree.findall('.//rdf:Description', NS)
    if not descs:
        return result

    crs = f'{{{NS["crs"]}}}'

    def _crs_val(name, default=None):
        """First value found for crs:<name> across all Description blocks, taken
        as an attribute or a child element; `default` if nowhere."""
        for d in descs:
            v = d.get(crs + name)
            if v is not None:
                return v
            el = d.find(crs + name)
            if el is not None and el.text is not None:
                return el.text
        return default

    exp = _crs_val('Exposure2012')
    if exp is not None:
        try:
            result["exposure"] = float(exp)
        except ValueError:
            pass

    if _crs_val('HasCrop') == "True":
        def _f(name, default):
            v = _crs_val(name)
            try:
                return float(v) if v is not None else default
            except ValueError:
                return default

        top = _f('CropTop', 0)
        left = _f('CropLeft', 0)
        bottom = _f('CropBottom', 1)
        right = _f('CropRight', 1)
        result["crop"] = {"left": left, "top": top, "width": right - left, "height": bottom - top}

        angle_val = _f('CropAngle', None)
        if angle_val is not None and angle_val != 0.0:
            result["angle"] = angle_val

    return result


def dop_crop_to_xmp(crop):
    """Convert DOP crop {left,top,width,height} to XMP {Top,Left,Bottom,Right}."""
    return {
        "top": crop["top"],
        "left": crop["left"],
        "bottom": crop["top"] + crop["height"],
        "right": crop["left"] + crop["width"],
    }


def xmp_crop_to_dop(crop):
    """Convert XMP-style crop to DOP {left,top,width,height}. Input already normalized."""
    return {
        "left": crop["left"],
        "top": crop["top"],
        "width": crop["width"],
        "height": crop["height"],
    }


def write_xmp_edits(xmp_path, exposure=None, crop=None, angle=None):
    """Surgically update exposure, crop and/or angle in an XMP sidecar, THROUGH
    exiftool.

    This used to parse the file with ElementTree and write the tree back, which
    quietly destroyed the sidecar. ElementTree knows nothing about XMP's
    `<?xpacket?>` wrapper, so the rewrite dropped it — and took the `xmp:Label`
    already in the file with it. A wrapper-less sidecar is one exiftool can then
    REFUSE to write ("0 image files updated ... 1 files weren't updated due to
    errors"), so every later colour or rating on that picture went nowhere while
    the CSV recorded it happily. Measured on one trip: 39 sidecars left in that
    state and 184 pictures claiming a colour their .xmp did not have.

    exiftool is the tool that understands this format. It keeps the wrapper, keeps
    every tag it was not asked to change, and reports honestly when it cannot
    write — which the caller now believes (see engine.exif_writer._reports_success).
    One writer per file, as everywhere else in the app.
    """
    args = []
    if exposure is not None:
        args.append(f"-XMP-crs:Exposure2012={exposure:.2f}")

    if crop is not None:
        xc = dop_crop_to_xmp(crop)
        args += [
            f"-XMP-crs:CropTop={xc['top']}",
            f"-XMP-crs:CropLeft={xc['left']}",
            f"-XMP-crs:CropBottom={xc['bottom']}",
            f"-XMP-crs:CropRight={xc['right']}",
            # DxO angle is positive clockwise, LR angle is negative clockwise
            f"-XMP-crs:CropAngle={-angle if angle else 0}",
            "-XMP-crs:CropConstrainToWarp=0",
            "-XMP-crs:CropConstrainToUnitSquare=1",
            "-XMP-crs:HasCrop=True",
        ]

    if not args:
        return False
    # Never invent a sidecar here: exiftool would happily create one, but which
    # pictures get a sidecar is decided by ensure_xmp/ensure_xmps, not by a crop.
    if not os.path.exists(xmp_path):
        logger.warning(f"No sidecar to patch at {xmp_path}")
        return False

    from engine.exif_writer import exiftool_write
    try:
        exiftool_write(xmp_path, args)
    except Exception as e:
        logger.warning(f"Could not write crop/exposure to {xmp_path}: {e}")
        return False
    return True


def _dop_block_span(text, start):
    """Return (open_idx, close_idx) of the brace block whose opening '{' is at or
    after `start`, matched with balanced-brace counting. None if unbalanced."""
    open_idx = text.index('{', start)
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return open_idx, i
    return None


def _strip_dop_keys(inner, keys):
    """Remove `Key = value,` entries (scalar OR braced value) for each key in
    `keys` from an Overrides inner-body string, so repeated writes don't duplicate
    them. Everything else (e.g. GridWarpingGrids) is preserved verbatim."""
    for k in keys:
        # Braced value first: `k = { ...balanced... } ,`
        m = re.search(rf'\b{re.escape(k)}\s*=\s*\{{', inner)
        if m:
            span = _dop_block_span(inner, m.start())
            if span:
                end = span[1] + 1
                tail = re.match(r'\s*,', inner[end:])   # eat the trailing comma
                if tail:
                    end += tail.end()
                inner = inner[:m.start()] + inner[end:]
                continue
        # Scalar value: `k = value,`
        inner = re.sub(rf'\b{re.escape(k)}\s*=\s*[^,\n{{}}]*,\s*', '', inner)
    return inner


def write_dop_edits(dop_path, exposure=None, crop=None, angle=None, denoise=None):
    """Surgically MERGE exposure / crop / angle / denoise into a DOP's Overrides block.

    `denoise` selects the DxO denoise method (see utils.photolab_denoise):
      None      -> leave denoise untouched;
      ""        -> Standard: write `NoiseActive = true` only (base method stays
                   "standard");
      "<code>"  -> write `NoiseActive = true` + `NoiseRemovalMethod = "<code>"`
                   (e.g. "DeepRaw2RGBv7" = DeepPRIME XD3 in PL9).
    Empty string and None are distinct — hence the `is not None` test below.

    The Overrides block is located by balanced-brace matching (a PhotoLab recipe
    always seeds it with a non-empty GridWarpingGrids block, so the old non-greedy
    regex truncated it at the first inner '},' and corrupted the file). We strip
    only the keys we manage and append fresh ones, leaving everything else intact,
    so the write is idempotent and never mangles a PhotoLab-authored recipe."""
    # newline="" preserves DxO's LF endings; text mode would CRLF-ify every line.
    with open(dop_path, "r", encoding="utf-8", newline="") as f:
        text = f.read()

    key = re.search(r'Overrides\s*=\s*\{', text)
    if not key:
        logger.warning(f"No Overrides section found in {dop_path}")
        return False
    span = _dop_block_span(text, key.start())
    if not span:
        logger.warning(f"Unbalanced Overrides block in {dop_path}; not writing")
        return False
    open_idx, close_idx = span
    inner = text[open_idx + 1:close_idx]

    new_entries = []
    managed = set()

    if exposure is not None:
        new_entries.append(f'ExposureBias = {exposure},')
        new_entries.append('ExposureActive = true,')
        managed |= {'ExposureBias', 'ExposureActive'}

    if crop is not None:
        new_entries.append('CropAuto = false,')
        new_entries.append('CropActive = true,')
        new_entries.append(f'CropRect = {{\n{crop["left"]},\n{crop["top"]},\n{crop["width"]},\n{crop["height"]},\n}}\n,')
        managed |= {'CropAuto', 'CropActive', 'CropRect'}

    if angle is not None:
        # LR angle is negative clockwise, DxO is positive clockwise
        dop_angle = -angle
        new_entries.append(f'KeystoningHorizon = {dop_angle},')
        new_entries.append('KeystoningHorizonActive = true,')
        new_entries.append('KeystoningHorizonAuto = false,')
        managed |= {'KeystoningHorizon', 'KeystoningHorizonActive', 'KeystoningHorizonAuto'}

    if denoise is not None:
        # Denoise lives in Overrides too: NoiseActive turns it on, and a non-empty
        # code names the method (Standard leaves the base "standard" method, so it
        # writes NoiseActive only). Strip any prior copy so switching methods — or
        # back to Standard — replaces cleanly rather than stacking.
        new_entries.append('NoiseActive = true,')
        if denoise:
            new_entries.append(f'NoiseRemovalMethod = "{denoise}",')
        managed |= {'NoiseActive', 'NoiseRemovalMethod'}

    if not new_entries:
        return False

    # Drop any prior copies of the keys we set (idempotent), keep the rest.
    inner = _strip_dop_keys(inner, managed).rstrip()
    sep = '\n' if inner else ''
    new_inner = inner + sep + '\n' + '\n'.join(new_entries) + '\n'
    text = text[:open_idx] + '{' + new_inner + '}' + text[close_idx + 1:]
    text = _touch_dop_stamps(text)

    with open(dop_path, "w", encoding="utf-8", newline="") as f:
        f.write(text)

    return True


def write_dop_metadata(dop_path, color=None, rating=None):
    """Surgically set the Item-level ColorLabel and/or Rating in a DxO .dop —
    PhotoLab's own colour label and star rating. Unlike the crop (which lives in
    the item's Settings→Overrides block), these sit at the picture-item level,
    beside `Name`. This is the native channel PhotoLab reads, so it avoids the
    .xmp round-trip where PL ignores/clobbers our colour label.

    color: a DxO colour name ("Red"/"Green"/"Yellow"/"Blue"/"Purple"), or ""/None
           value "None" to clear (case-insensitive in; normalised to DxO's
           capitalised vocabulary). rating: int 0-5.
    Returns True if the file changed.

    Each PL-authored .dop carries exactly one `ColorLabel` and one `Rating`, so a
    single anchored replace is unambiguous; if a field is somehow absent (a
    from-scratch recipe) it's inserted at item scope.

    newline="" preserves DxO's LF line endings byte-for-byte (text-mode write
    would CRLF-ify every line on Windows, rewriting the whole file)."""
    with open(dop_path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    changed = False

    if color is not None:
        dxo_color = (color or "None").strip().capitalize()  # DxO clears to "None"
        if re.search(r'ColorLabel\s*=\s*"[^"]*"', text):
            text, n = re.subn(r'ColorLabel\s*=\s*"[^"]*"',
                              f'ColorLabel = "{dxo_color}"', text, count=1)
            changed = changed or bool(n)
        else:
            m = re.search(r'\n(\s*)Name\s*=\s*"', text)  # insert just before Name
            if m:
                text = text[:m.start()] + f'\n{m.group(1)}ColorLabel = "{dxo_color}",' + text[m.start():]
                changed = True

    if rating is not None:
        r = int(rating)
        if re.search(r'\bRating\s*=\s*-?\d+', text):
            text, n = re.subn(r'\bRating\s*=\s*-?\d+', f'Rating = {r}', text, count=1)
            changed = changed or bool(n)
        else:
            m = re.search(r'\n(\s*)Settings\s*=\s*\{', text)  # insert just before Settings
            if m:
                text = text[:m.start()] + f'\n{m.group(1)}Rating = {r},' + text[m.start():]
                changed = True

    if changed:
        text = _touch_dop_stamps(text)
        with open(dop_path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    return changed


_XMP_LABEL_RE = re.compile(r'<xmp:Label>([^<]*)</xmp:Label>|xmp:Label="([^"]*)"')
_XMP_RATING_RE = re.compile(r'<xmp:Rating>([^<]*)</xmp:Rating>|xmp:Rating="([^"]*)"')


def read_xmp_metadata(xmp_path):
    """Colour label and star rating from an .xmp — the counterpart of
    read_dop_metadata, and since 2026-09-29 the place SV writes them (never the
    .dop; see utils/sidecar_policy.py). Returns {"color": str|None, "rating":
    int|None}: None when the field is absent. Accepts both spellings in use —
    PhotoLab writes elements, exiftool attributes. A plain read: never raises,
    never writes (an .xmp is only ever written through exiftool)."""
    result = {"color": None, "rating": None}
    try:
        with open(xmp_path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return result
    m = _XMP_LABEL_RE.search(text)
    if m:
        raw = (m.group(1) if m.group(1) is not None else m.group(2) or "").strip()
        result["color"] = "" if raw.lower() in ("", "none") else raw.capitalize()
    m = _XMP_RATING_RE.search(text)
    if m:
        try:
            result["rating"] = int(float((m.group(1) if m.group(1) is not None
                                          else m.group(2) or "").strip()))
        except ValueError:
            pass
    return result


def read_dop_metadata(dop_path):
    """Read the Item-level ColorLabel / Rating from a DxO .dop — the inverse of
    write_dop_metadata. This is PhotoLab's native colour/rating store and, under
    the default policy, SpeciesViewer's source of truth for these fields (the
    .xmp channel is off). Returns {"color": str|None, "rating": int|None}: a
    field is None when absent; DxO's "None" colour maps to "" (cleared).

    Colour is normalised to SpeciesViewer's capitalised vocabulary; unknown/blank
    is returned as "". Never raises — a malformed/missing .dop yields all-None."""
    result = {"color": None, "rating": None}
    try:
        with open(dop_path, "r", encoding="utf-8", newline="") as f:
            text = f.read()
    except OSError:
        return result
    m = re.search(r'ColorLabel\s*=\s*"([^"]*)"', text)
    if m:
        raw = m.group(1).strip()
        result["color"] = "" if raw.lower() in ("", "none") else raw.capitalize()
    m = re.search(r'\bRating\s*=\s*(-?\d+)', text)
    if m:
        result["rating"] = int(m.group(1))
    return result


def push_metadata_to_dop(raw_path, color=None, rating=None):
    """Convenience: ensure the RAW's .dop exists, then set its Item-level
    ColorLabel / Rating so PhotoLab picks them up on 'import sidecars'. This is
    the PL-friendly channel for colour/rating (the .xmp is not — PL clobbers it).

    `raw_path` is the source RAW (the .dop lives at '<raw>.dop'). A JPG source
    has no meaningful Item-level colour/rating recipe of its own here, but PL does
    read '<jpg>.dop', so it's written too. Returns True if the .dop changed.
    Policy gating (whether to call this at all) is the caller's job."""
    if not raw_path:
        return False
    dop = ensure_dop(raw_path)
    if not dop:
        return False
    try:
        return write_dop_metadata(dop, color=color, rating=rating)
    except Exception as e:
        logger.warning(f"push_metadata_to_dop failed for {dop}: {e}")
        return False


# ── Sidecar creation (seed a suggestion before PhotoLab/Lightroom touch it) ──────
#
# The surgical writers above edit an *existing* sidecar. To suggest a crop on a
# RAW straight off the card — before DxO or Lightroom have created their recipe —
# we must first lay down a minimal, valid sidecar. In our flow the .xmp usually
# already exists (SpeciesViewer writes it for GPS/rating/species), so it's the
# .dop that typically has to be created.

_XMP_SKELETON = (
    "<x:xmpmeta xmlns:x='adobe:ns:meta/' x:xmptk='SpeciesViewer'>\n"
    "<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>\n"
    " <rdf:Description rdf:about=''\n"
    "  xmlns:crs='http://ns.adobe.com/camera-raw-settings/1.0/'>\n"
    "  <crs:RawFileName>{name}</crs:RawFileName>\n"
    " </rdf:Description>\n"
    "</rdf:RDF>\n"
    "</x:xmpmeta>\n"
)

# The from-scratch DxO recipe skeleton that used to live here has been REMOVED,
# not merely disabled: PhotoLab owns the master UUID, so a .dop it did not author
# is always taken as a virtual copy. Writing one is never the right answer, and a
# dormant skeleton is an invitation to try again. See ensure_dop below.


def ensure_dop(raw_path):
    """Return the RAW's EXISTING .dop path, or None. NEVER creates one.

    ⚠ This function used to create a minimal .dop when none existed, and the
    skeleton's own comment called that "the one unverified assumption in this
    path". It was then verified, and it is false: PhotoLab assigns every RAW a
    random v4 UUID in its OWN catalog at import and matches .dop items by that
    UUID, so a .dop we author carries a UUID PL never registered and PL adds it
    as a VIRTUAL COPY rather than reading it. The UUIDs cannot be computed, and
    the format has no master/virtual flag — so a from-scratch .dop is not merely
    unreliable, it is impossible. (Empty Base + schema "19.5" was also implicated
    in a PL crash.) A single afternoon of culling with the .xmp channel off left
    647 such files behind, holding real colour work in a form PhotoLab misreads.

    The supported route is the other way round: PhotoLab authors the .dop
    (select all → write sidecars), and SpeciesViewer then EDITS that recipe with
    the surgical writers above, which never touch the UUIDs. Callers must treat
    None as "this picture has no PhotoLab recipe yet" and fall back to the .xmp,
    which is exactly what write_metadata does.
    """
    dop_path = str(raw_path) + ".dop"
    return dop_path if os.path.exists(dop_path) else None


def sidecar_opts(out_pattern: str = "%d%f.xmp") -> list:
    """The exiftool options that create one XMP sidecar per RAW.

    THE one recipe. It used to live in batch/sv0ReadSDCard.py, because ingest was
    the only thing that ever authored a sidecar, while ensure_xmp below kept a
    near-copy that had quietly drifted: it lacked the DateTimeOriginal-with-offset
    expression, so a sidecar created here was blind to the camera's UTC offset and
    the tracklog geotag had to guess it. Two hand-maintained copies of a fact about
    the world is the same mistake the raw-extension table exists to prevent, so
    there is now one, HERE (engine is the layer batch imports, not the reverse),
    and sv0ReadSDCard imports it.

    ``out_pattern`` is exiftool's -o target; the default writes beside the RAW.
    """
    return ["-tagsfromfile", "@", "-xmp:all<all",
            "-XMP-exif:SubjectDistance<Olympus:FocusDistance#",
            "-XMP-exif:DateTimeOriginal<${DateTimeOriginal}${OffsetTimeOriginal}",
            "-o", out_pattern]


def existing_xmp(raw_path):
    """The RAW's sidecar path if one is already on disk, else None. Creates nothing."""
    base = os.path.splitext(str(raw_path))[0]
    for existing in (base + ".xmp", base + ".XMP"):
        if os.path.exists(existing):
            return existing
    return None


def ensure_xmps(raw_paths):
    """Create a `.xmp` sidecar for every RAW in `raw_paths` that lacks one.

    Batched sibling of ensure_xmp, and for the same reason the culling grid is
    batched: an exiftool run costs ~270 ms almost entirely in spawning the
    process, so twenty-two pictures one at a time is six seconds of startup for a
    write that takes no time. exiftool takes any number of files per run.

    Returns {str(raw_path): str(xmp_path)} for every RAW that HAS a sidecar
    afterwards — created now or already there. A RAW missing from the result is
    one whose sidecar could not be created; the caller decides what that means.
    """
    paths = [str(p) for p in raw_paths]
    out, todo = {}, []
    for rp in paths:
        found = existing_xmp(rp)
        if found:
            out[rp] = found
        else:
            todo.append(rp)
    if todo:
        try:
            from engine.exif_writer import run_exiftool_files
            run_exiftool_files(sidecar_opts(), todo)
        except Exception as e:
            logger.warning(f"ensure_xmps: could not create {len(todo)} sidecar(s): {e}")
        for rp in todo:
            found = existing_xmp(rp)
            if found:
                out[rp] = found
            else:
                logger.warning(f"ensure_xmps: no sidecar for {rp} after exiftool ran")
    return out


def ensure_xmp(raw_path):
    """Return the RAW's `.xmp` sidecar path, CREATING a minimal one if missing.

    Unlike the .dop (see ensure_dop — a from-scratch .dop is impossible because
    PhotoLab matches by a UUID we cannot compute), an .xmp is always safe to
    author: every tool that reads .xmp treats a fresh sidecar as authoritative.
    When none exists we create it exactly the way ingest does — same recipe, one
    function (sidecar_opts): exiftool copies the RAW's own metadata into
    `<stem>.xmp` — so write_xmp_edits below has a real rdf:Description to patch.
    Returns the path, or None if creation failed.
    """
    return ensure_xmps([raw_path]).get(str(raw_path))


def _hold_dop_for_photolab(raw_path, res) -> bool:
    """True when the .dop write must wait because PhotoLab is running.

    Any .dop write wakes a running PhotoLab, which then writes its own database
    out over the .xmp — replacing a colour it has not read yet with its old one
    (test T6, docs/plans/photolab_sidecar_fight.md). So while it runs, crop and
    exposure go to the .xmp only; handover_to_photolab() brings the .dop up to
    date at "Open in PhotoLab". Only a RAW that already has a .dop is held —
    without one there is nothing to write anyway (we never create a .dop)."""
    if not os.path.exists(str(raw_path) + ".dop"):
        return False
    from utils.photolab_process import photolab_running
    if not photolab_running():
        return False
    res["dop_held"] = True
    logger.info(f"PhotoLab is running: {os.path.basename(str(raw_path))} edit goes "
                f"to the .xmp now, to the .dop at Open in PhotoLab")
    return True


def _edits_differ(a, b, tol=1e-4):
    if a is None or b is None:
        return (a is None) != (b is None)
    if isinstance(a, dict):
        return any(abs(float(a[k]) - float(b.get(k, -9))) > tol
                   for k in ("left", "top", "width", "height"))
    return abs(float(a) - float(b)) > tol


def handover_to_photolab(raw_paths):
    """Bring each RAW's .dop up to date with the crop / exposure / angle in its
    .xmp — the edits held back while PhotoLab was running. One direction only,
    .xmp -> .dop: while SV is the editor the .xmp is the truth for these, and a
    .dop PhotoLab has just rewritten (after reading our colours) looks NEWER, so
    sync_sidecars()'s newest-wins rule would put the old crop back.

    Only fields that differ are written. A RAW whose .xmp carries an edit but
    that has NO .dop yet is reported as waiting, not written: PhotoLab creates a
    picture's recipe only when it processes it (scrolling through a new day's
    thumbnails), and we never create one ourselves. Seen live 2026-09-29: a crop
    on a picture PhotoLab had not reached yet looked like a failed handover.

    Returns {"done": [(raw, [fields])], "waiting": [(raw, [fields])]}."""
    done, waiting = [], []
    for raw in raw_paths:
        raw = str(raw)
        dop = raw + ".dop"
        base = os.path.splitext(raw)[0]
        xmp = next((p for p in (base + ".xmp", base + ".XMP") if os.path.exists(p)), None)
        if not xmp:
            continue
        try:
            x = read_xmp_edits(xmp)
        except Exception as e:
            logger.warning(f"handover_to_photolab: cannot read {os.path.basename(xmp)}: {e}")
            continue
        if not os.path.exists(dop):
            fields = [f for f, v in (("crop", x["crop"]), ("exposure", x["exposure"]),
                                     ("angle", x["angle"])) if v not in (None, 0.0)]
            if fields:
                waiting.append((raw, fields))
            continue
        try:
            d = read_dop_overrides(dop)
        except Exception as e:
            logger.warning(f"handover_to_photolab: cannot read {os.path.basename(dop)}: {e}")
            continue
        crop = x["crop"] if x["crop"] and _edits_differ(x["crop"], d["crop"]) else None
        exposure = (x["exposure"] if x["exposure"] is not None
                    and _edits_differ(x["exposure"], d["exposure"]) else None)
        # xmp angle is Lightroom's sign, the .dop's is DxO's (negated)
        angle = (x["angle"] if x["angle"] is not None and _edits_differ(
                 x["angle"], -d["angle"] if d["angle"] is not None else None) else None)
        if crop is None and exposure is None and angle is None:
            continue
        try:
            # A crop carries the configured denoise with it, as in suggest_crop.
            denoise = None
            if crop:
                from utils.photolab_denoise import denoise_override_for_crop
                denoise = denoise_override_for_crop()
            if write_dop_edits(dop, exposure=exposure,
                               crop=xmp_crop_to_dop(crop) if crop else None,
                               angle=angle, denoise=denoise):
                done.append((raw, [f for f, v in (("crop", crop), ("exposure", exposure),
                                                  ("angle", angle)) if v is not None]))
        except Exception as e:
            logger.warning(f"handover_to_photolab: write failed for {dop}: {e}")
    return {"done": done, "waiting": waiting}


def suggest_crop(raw_path, crop, angle=None):
    """Write a suggested crop to the DxO .dop (and, for a RAW source, the
    Lightroom .xmp) so the next edit in PhotoLab or Lightroom opens with it.
    Creates a sidecar when it's missing (so a fresh off-the-card RAW still gets
    the suggestion). `crop` is the normalised {left,top,width,height} (0-1).

    For a JPG/JPEG source only the .dop is written: PhotoLab reads <jpg>.dop, but
    a `<stem>.xmp` sidecar is not a JPG convention (Lightroom stores JPG metadata
    in-file) and would just be clutter the pipeline then picks up. Returns a dict
    describing what was written/created.

    Which sidecars are written is gated by the Sidecar Behaviour settings: the
    .dop is PhotoLab's crop recipe (on by default), while the .xmp crop is off by
    default because PhotoLab watches the .xmp and, on our write, re-derives from
    its own reading and discards the .dop crop (see utils/sidecar_policy.py)."""
    from utils.sidecar_policy import crop_to_dop_enabled, crop_to_xmp_enabled
    res = {"dop": False, "xmp": False, "dop_created": False, "xmp_created": False,
           "dop_held": False}

    if crop_to_dop_enabled() and not _hold_dop_for_photolab(raw_path, res):
        dop_before = os.path.exists(str(raw_path) + ".dop")
        dop_path = ensure_dop(raw_path)
        res["dop_created"] = bool(dop_path) and not dop_before
        if dop_path:
            try:
                # Preparing a JPG (this is the crop) is the one moment we also stamp
                # the configured DxO denoise method into the .dop; returns None when
                # the feature is off, so the write is unchanged then.
                from utils.photolab_denoise import denoise_override_for_crop
                res["dop"] = write_dop_edits(
                    dop_path, crop=crop, angle=angle,
                    denoise=denoise_override_for_crop())
            except Exception as e:
                logger.warning(f"suggest_crop: DOP write failed for {dop_path}: {e}")

    if os.path.splitext(str(raw_path))[1].lower() in (".jpg", ".jpeg"):
        return res   # JPG source: .dop only, no spurious .xmp sidecar

    if not crop_to_xmp_enabled():
        return res   # crop-to-XMP disabled (default): don't poke PhotoLab's monitor

    xmp_before = os.path.exists(os.path.splitext(str(raw_path))[0] + ".xmp")
    xmp_path = ensure_xmp(raw_path)
    res["xmp_created"] = bool(xmp_path) and not xmp_before
    if xmp_path:
        try:
            res["xmp"] = write_xmp_edits(xmp_path, crop=crop, angle=angle)
        except Exception as e:
            logger.warning(f"suggest_crop: XMP write failed for {xmp_path}: {e}")

    return res


def suggest_exposure(raw_path, exposure):
    """Write a suggested exposure adjustment (EV, Lightroom's Exposure2012
    scale) to the DxO .dop (when PhotoLab has already authored one — see
    ensure_dop, never created) and the Lightroom .xmp, so the next edit in
    either app opens with it. Mirrors suggest_crop exactly — see there and
    utils/sidecar_policy for why both channels are written unconditionally.
    `exposure` is a float EV; 0.0 clears back to no adjustment. Returns a dict
    describing what was written/created."""
    res = {"dop": False, "xmp": False, "dop_created": False, "xmp_created": False,
           "dop_held": False}

    dop_path = None
    if not _hold_dop_for_photolab(raw_path, res):
        dop_before = os.path.exists(str(raw_path) + ".dop")
        dop_path = ensure_dop(raw_path)
        res["dop_created"] = bool(dop_path) and not dop_before
    if dop_path:
        try:
            res["dop"] = write_dop_edits(dop_path, exposure=exposure)
        except Exception as e:
            logger.warning(f"suggest_exposure: DOP write failed for {dop_path}: {e}")

    if os.path.splitext(str(raw_path))[1].lower() in (".jpg", ".jpeg"):
        return res   # JPG source: .dop only, no <stem>.xmp convention

    xmp_before = os.path.exists(os.path.splitext(str(raw_path))[0] + ".xmp")
    xmp_path = ensure_xmp(raw_path)
    res["xmp_created"] = bool(xmp_path) and not xmp_before
    if xmp_path:
        try:
            res["xmp"] = write_xmp_edits(xmp_path, exposure=exposure)
        except Exception as e:
            logger.warning(f"suggest_exposure: XMP write failed for {xmp_path}: {e}")

    return res


def _has_edits(data):
    """True if the sidecar has any non-default crop, exposure, or angle."""
    if data["exposure"] is not None and data["exposure"] != 0.0:
        return True
    if data["crop"] is not None:
        return True
    if data["angle"] is not None:
        return True
    return False


def sync_sidecars(raw_path):
    """Sync crop/exposure between DOP and XMP sidecars.

    Logic (crop and exposure only — keywords/GPS are handled by DxO):
      - Only one has edits  -> sync from that one to the other
      - Both have edits     -> sync from the newer file (timestamp)
      - Neither has edits   -> nothing to do

    Returns a string describing what was done, or None if nothing changed.
    Returns "NO_DOP" if XMP has edits but no DOP file exists.
    """
    dop_path = raw_path + ".dop"
    base = os.path.splitext(raw_path)[0]
    xmp_path = base + ".XMP"
    if not os.path.exists(xmp_path):
        xmp_path = base + ".xmp"

    has_dop = os.path.exists(dop_path)
    has_xmp = os.path.exists(xmp_path)

    if not has_xmp:
        return None

    if not has_dop:
        xmp = read_xmp_edits(xmp_path)
        if _has_edits(xmp):
            return "NO_DOP"
        return None

    dop = read_dop_overrides(dop_path)
    xmp = read_xmp_edits(xmp_path)

    dop_has = _has_edits(dop)
    xmp_has = _has_edits(xmp)

    if not dop_has and not xmp_has:
        return None

    # Decide direction: who is the source?
    if dop_has and not xmp_has:
        direction = "DOP->XMP"
    elif xmp_has and not dop_has:
        direction = "XMP->DOP"
    elif dop["mtime"] > xmp["mtime"]:
        direction = "DOP->XMP"
    else:
        direction = "XMP->DOP"

    if direction == "DOP->XMP":
        exposure = dop["exposure"]
        crop = dop["crop"]
        angle = dop["angle"]
        write_xmp_edits(xmp_path, exposure=exposure, crop=crop, angle=angle)
    else:
        exposure = xmp["exposure"]
        crop = xmp_crop_to_dop(xmp["crop"]) if xmp["crop"] else None
        angle = xmp["angle"]
        write_dop_edits(dop_path, exposure=exposure, crop=crop, angle=angle)

    parts = []
    if exposure is not None: parts.append(f"exposure={exposure}")
    if crop is not None: parts.append("crop")
    if angle is not None: parts.append(f"angle={angle}")
    return f"{direction}: {', '.join(parts)}"


# ---- standalone test ----
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1:
        raw = sys.argv[1]
    else:
        raw = "D:/CurrentPictures/2026/2026 Kreta/2026-03-16/2026 Kreta-N3160346.ORF"

    print(f"RAW: {raw}")
    dop_path = raw + ".dop"
    base = os.path.splitext(raw)[0]
    xmp_path = base + ".XMP" if os.path.exists(base + ".XMP") else base + ".xmp"

    if os.path.exists(dop_path):
        dop = read_dop_overrides(dop_path)
        print(f"DOP: exposure={dop['exposure']}, crop={dop['crop']}, angle={dop['angle']}")
    else:
        dop = None
        print(f"DOP: not found")

    if os.path.exists(xmp_path):
        xmp = read_xmp_edits(xmp_path)
        print(f"XMP: exposure={xmp['exposure']}, crop={xmp['crop']}, angle={xmp['angle']}")
    else:
        xmp = None
        print(f"XMP: not found")

    if dop and xmp:
        print(f"DOP mtime: {dop['mtime']:.0f}, XMP mtime: {xmp['mtime']:.0f}")
        newer = "DOP" if dop["mtime"] > xmp["mtime"] else "XMP"
        print(f"Newer: {newer}")

    if "--sync" in sys.argv:
        result = sync_sidecars(raw)
        print(f"Sync: {result}")
    else:
        print("(dry run — add --sync to apply)")
