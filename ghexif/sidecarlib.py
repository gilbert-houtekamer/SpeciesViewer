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

    result = {"exposure": None, "crop": None, "mtime": os.path.getmtime(dop_path)}

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
    m = True  # just to enter the block below
    if not m:
        return result

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

    return result


def read_xmp_edits(xmp_path):
    """Read crop and exposure from XMP crs: attributes."""
    result = {"exposure": None, "crop": None, "mtime": os.path.getmtime(xmp_path)}

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


def write_xmp_edits(xmp_path, exposure=None, crop=None):
    """Surgically update exposure and/or crop in an XMP file."""
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
        desc.set(f'{crs}CropAngle', '0')
        desc.set(f'{crs}CropConstrainToWarp', '0')
        desc.set(f'{crs}CropConstrainToUnitSquare', '1')
        desc.set(f'{crs}HasCrop', 'True')
        changed = True

    if changed:
        tree.write(xmp_path, xml_declaration=False, encoding="unicode")

    return changed


def write_dop_edits(dop_path, exposure=None, crop=None):
    """Surgically update exposure and/or crop in a DOP file's Overrides section."""
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

    if not new_entries:
        return False

    new_block = overrides_match.group(1) + '\n' + '\n'.join(new_entries) + '\n' + overrides_match.group(3)
    text = text[:overrides_match.start()] + new_block + text[overrides_match.end():]

    with open(dop_path, "w", encoding="utf-8") as f:
        f.write(text)

    return True


def sync_sidecars(raw_path):
    """Sync crop/exposure from the most recently edited sidecar to the other.

    Returns a string describing what was done, or None if nothing changed.
    """
    dop_path = raw_path + ".dop"
    # XMP can be .XMP or .xmp, same name as RAW but different extension
    base = os.path.splitext(raw_path)[0]
    xmp_path = base + ".XMP"
    if not os.path.exists(xmp_path):
        xmp_path = base + ".xmp"

    if not os.path.exists(dop_path) or not os.path.exists(xmp_path):
        return None

    dop = read_dop_overrides(dop_path)
    xmp = read_xmp_edits(xmp_path)

    if dop["mtime"] > xmp["mtime"]:
        # DOP is newer -> update XMP
        exposure = dop["exposure"]
        crop = dop["crop"]
        if exposure is None and crop is None:
            return None
        write_xmp_edits(xmp_path, exposure=exposure, crop=crop)
        parts = []
        if exposure is not None: parts.append(f"exposure={exposure}")
        if crop is not None: parts.append("crop")
        return f"DOP->XMP: {', '.join(parts)}"
    else:
        # XMP is newer -> update DOP
        exposure = xmp["exposure"]
        crop = xmp_crop_to_dop(xmp["crop"]) if xmp["crop"] else None
        if exposure is None and crop is None:
            return None
        write_dop_edits(dop_path, exposure=exposure, crop=crop)
        parts = []
        if exposure is not None: parts.append(f"exposure={exposure}")
        if crop is not None: parts.append("crop")
        return f"XMP->DOP: {', '.join(parts)}"


# ---- standalone test ----
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1:
        raw = sys.argv[1]
    else:
        raw = "D:/CurrentPictures/2026/2026 Kreta/2026-03-16/2026 Kreta-N3160346.ORF"

    print(f"RAW: {raw}")
    dop = read_dop_overrides(raw + ".dop")
    print(f"DOP: exposure={dop['exposure']}, crop={dop['crop']}")

    base = os.path.splitext(raw)[0]
    xmp_path = base + ".XMP" if os.path.exists(base + ".XMP") else base + ".xmp"
    xmp = read_xmp_edits(xmp_path)
    print(f"XMP: exposure={xmp['exposure']}, crop={xmp['crop']}")

    print(f"DOP mtime: {dop['mtime']:.0f}, XMP mtime: {xmp['mtime']:.0f}")
    newer = "DOP" if dop["mtime"] > xmp["mtime"] else "XMP"
    print(f"Newer: {newer}")

    if "--sync" in sys.argv:
        result = sync_sidecars(raw)
        print(f"Sync: {result}")
    else:
        print("(dry run — add --sync to apply)")
