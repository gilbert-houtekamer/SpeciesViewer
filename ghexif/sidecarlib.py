"""Transfer crop and exposure between DxO DOP and Lightroom XMP sidecar files."""

import os
import re
import xml.etree.ElementTree as ET
import logging

logger = logging.getLogger("speciesviewer")

# XMP namespaces
NS = {
    'x': 'adobe:ns:meta/',
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
    'crs': 'http://ns.adobe.com/camera-raw-settings/1.0/',
}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


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
    """Read crop and exposure from XMP crs: attributes."""
    result = {"exposure": None, "crop": None, "angle": None, "mtime": os.path.getmtime(xmp_path)}

    tree = ET.parse(xmp_path)
    desc = tree.find('.//rdf:Description', NS)
    if desc is None:
        return result

    # Exposure — can be attribute or child element
    exp = desc.get(f'{{{NS["crs"]}}}Exposure2012')
    if exp is None:
        exp_el = desc.find(f'{{{NS["crs"]}}}Exposure2012')
        if exp_el is not None:
            exp = exp_el.text
    if exp is not None:
        result["exposure"] = float(exp)

    # Crop — can be attribute or child element
    has_crop = desc.get(f'{{{NS["crs"]}}}HasCrop')
    if has_crop is None:
        hc_el = desc.find(f'{{{NS["crs"]}}}HasCrop')
        if hc_el is not None:
            has_crop = hc_el.text
    if has_crop == "True":
        def _crs_val(name, default):
            v = desc.get(f'{{{NS["crs"]}}}{name}')
            if v is None:
                el = desc.find(f'{{{NS["crs"]}}}{name}')
                v = el.text if el is not None else None
            return float(v) if v is not None else default

        top = _crs_val('CropTop', 0)
        left = _crs_val('CropLeft', 0)
        bottom = _crs_val('CropBottom', 1)
        right = _crs_val('CropRight', 1)
        result["crop"] = {"left": left, "top": top, "width": right - left, "height": bottom - top}

        angle_val = _crs_val('CropAngle', None)
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
    """Surgically update exposure, crop, and/or angle in an XMP file."""
    tree = ET.parse(xmp_path)
    desc = tree.find('.//rdf:Description', NS)
    if desc is None:
        logger.warning(f"No rdf:Description found in {xmp_path}")
        return False

    crs = f'{{{NS["crs"]}}}'
    changed = False

    if exposure is not None:
        desc.set(f'{crs}Exposure2012', f'{exposure:.2f}')
        changed = True

    if crop is not None:
        xc = dop_crop_to_xmp(crop)
        desc.set(f'{crs}CropTop', str(xc["top"]))
        desc.set(f'{crs}CropLeft', str(xc["left"]))
        desc.set(f'{crs}CropBottom', str(xc["bottom"]))
        desc.set(f'{crs}CropRight', str(xc["right"]))
        # DxO angle is positive clockwise, LR angle is negative clockwise
        desc.set(f'{crs}CropAngle', str(-angle if angle else 0))
        desc.set(f'{crs}CropConstrainToWarp', '0')
        desc.set(f'{crs}CropConstrainToUnitSquare', '1')
        desc.set(f'{crs}HasCrop', 'True')
        changed = True

    if changed:
        tree.write(xmp_path, xml_declaration=False, encoding="unicode")

    return changed


def write_dop_edits(dop_path, exposure=None, crop=None, angle=None):
    """Surgically update exposure, crop, and/or angle in a DOP file's Overrides section."""
    with open(dop_path, "r", encoding="utf-8") as f:
        text = f.read()

    # Find or create Overrides block
    overrides_match = re.search(r'(Overrides\s*=\s*\{)(.*?)(\}\s*,)', text, re.DOTALL)
    if not overrides_match:
        logger.warning(f"No Overrides section found in {dop_path}")
        return False

    new_entries = []

    if exposure is not None:
        new_entries.append(f'ExposureBias = {exposure},')
        new_entries.append('ExposureActive = true,')

    if crop is not None:
        new_entries.append('CropAuto = false,')
        new_entries.append('CropActive = true,')
        new_entries.append(f'CropRect = {{\n{crop["left"]},\n{crop["top"]},\n{crop["width"]},\n{crop["height"]},\n}}\n,')

    if angle is not None:
        # LR angle is negative clockwise, DxO is positive clockwise
        dop_angle = -angle
        new_entries.append(f'KeystoningHorizon = {dop_angle},')
        new_entries.append('KeystoningHorizonActive = true,')
        new_entries.append('KeystoningHorizonAuto = false,')

    if not new_entries:
        return False

    new_block = overrides_match.group(1) + '\n' + '\n'.join(new_entries) + '\n' + overrides_match.group(3)
    text = text[:overrides_match.start()] + new_block + text[overrides_match.end():]

    with open(dop_path, "w", encoding="utf-8") as f:
        f.write(text)

    return True


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
    """
    dop_path = raw_path + ".dop"
    base = os.path.splitext(raw_path)[0]
    xmp_path = base + ".XMP"
    if not os.path.exists(xmp_path):
        xmp_path = base + ".xmp"

    if not os.path.exists(dop_path) or not os.path.exists(xmp_path):
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
