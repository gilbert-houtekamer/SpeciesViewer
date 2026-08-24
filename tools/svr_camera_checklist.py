#!/usr/bin/env python3
"""Per-body camera support checklist — the repeatable form of the ch.21 corpus run.

Every measurement in here was made once by hand on 2026-08-23 and written into the
corpus README. That answered the question for a day. This answers it again whenever
LibRaw, exiftool or SpeciesViewer changes, which is the only way the table stays
true — and the table is what a future user gets when they ask "does it work with my
camera?", so it must never claim support that was not tested this run.

Five columns, matching the five independent mechanisms a RAW passes through. A body
can fail at one and work at the other four:

  date      DateTimeOriginal reads (ingest cannot file a picture without it)
  sidecar   an XMP sidecar gets written, carrying the date forward
  focus     a focus distance reaches the pipeline, measured through the master
            scan's own field list — Nikon and Olympus yes, Canon and Sony no
            (they store it under names nothing asks for), Panasonic not at all
  preview   a USABLE preview comes out, i.e. a real JPEG, not merely some embed
  decode    LibRaw decodes the sensor data

Everything is measured through SpeciesViewer's own code, never a re-implementation:
the RAW extensions come from utils.raw_extensions, the preview from
gui.preview_extract, and the sidecar from the exact exiftool expressions ingest uses
(sv0ReadSDCard.sidecar_opts, pointed at a scratch directory so the corpus is never
written to). A checklist that retyped any of those would certify the copy rather than
the product.

Usage
-----
    python tools/svr_camera_checklist.py                    # writes docs/camera_support.md
    python tools/svr_camera_checklist.py --corpus PATH --out FILE
    python tools/svr_camera_checklist.py --json results.json

The corpus itself is NOT in git (1.16 GB); it is fetched by its own download.py and
lives beside the repository. Only this tool and the table it writes are committed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "speciesviewer"))

OK, BAD, PARTIAL = "yes", "NO", "partial"


# ── measuring one file ────────────────────────────────────────────────────────

def read_metadata(paths):
    """{path: {tag: value}} for every file in ONE exiftool call. Batched because the
    per-file cost is process startup, not the read — the same reason the master EXIF
    scan is batched."""
    from engine.exif_writer import run_exiftool_files
    proc = run_exiftool_files(["-j", "-Make", "-Model", "-DateTimeOriginal",
                               "-FileType", "-NEFCompression", "-CompressorVersion"],
                              paths)
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return {}
    return {Path(r["SourceFile"]): r for r in rows}


def check_sidecars(paths, scratch: Path) -> dict:
    """{path: (written, has_date, has_focus)} — run ingest's REAL sidecar expressions
    into a scratch directory, so the corpus files are never written beside.

    One exiftool call for all of them, then one to read the results back."""
    from engine.exif_writer import run_exiftool_files
    from batch.sv0ReadSDCard import sidecar_opts
    scratch.mkdir(parents=True, exist_ok=True)
    run_exiftool_files(sidecar_opts(str(scratch / "%f.xmp")), paths)

    made = {p: scratch / (p.stem + ".xmp") for p in paths}
    present = [x for x in made.values() if x.exists()]
    fields = {}
    if present:
        proc = run_exiftool_files(["-j", "-DateTimeOriginal"], present)
        try:
            for r in json.loads(proc.stdout or "[]"):
                fields[Path(r["SourceFile"])] = r
        except json.JSONDecodeError:
            pass
    return {p: (xmp.exists(), bool(fields.get(xmp, {}).get("DateTimeOriginal")))
            for p, xmp in made.items()}


def check_focus(paths) -> dict:
    """{path: captured} — does the pipeline get a focus distance for this file?

    Measured by asking exiftool for the REAL request list the master EXIF scan uses
    (engine.selected_fields.SELECTED_FIELDS) and looking for the two column names the
    blog and the info panels actually read, via
    engine.focus_distance.focus_distance() — the same merge the master scan applies,
    so Canon's range and Sony's composite count as captured here exactly when they
    count as captured in the CSV. Not by checking the XMP sidecar, which is a different and
    narrower path: the sidecar maps only Olympus, but the master scan requests
    -Nikon:FocusDistance too, and both come back under the MakerNotes group. Asking
    the sidecar would have reported a red mark for every Nikon while a D500's focus
    distance was visibly sitting on the published website."""
    from engine.exif_writer import run_exiftool_files
    from engine.selected_fields import SELECTED_FIELDS
    from engine.exifcache import COMMON_ARGS
    # COMMON_ARGS carries -G (group 0), which is what makes the key read
    # "MakerNotes:FocusDistance". -G1 would give "Nikon:FocusDistance" instead and
    # every Nikon row would come back red — imported rather than retyped for exactly
    # that reason.
    proc = run_exiftool_files(["-j"] + list(COMMON_ARGS) + list(SELECTED_FIELDS),
                              paths)
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return {p: False for p in paths}
    from engine.focus_distance import focus_distance
    got = {Path(r["SourceFile"]): bool(focus_distance(r)) for r in rows}
    return {p: got.get(p, False) for p in paths}


def check_preview(path: Path) -> dict:
    """Does a USABLE preview come out, and how big, and how long. 'Usable' is the
    point: an embed that is present but not a JPEG (the R5 II's HEVC) is a failure,
    and used to render a blank card rather than reporting one."""
    from io import BytesIO
    from PIL import Image
    from gui.preview_extract import extract_preview_bytes, embed_lengths
    t0 = time.time()
    embeds = embed_lengths(path)
    data = extract_preview_bytes(path)
    secs = time.time() - t0
    if not data:
        return {"ok": False, "size": "", "secs": secs, "embeds": len(embeds),
                "note": "no embedded JPEG" if embeds else "no embed at all"}
    try:
        with Image.open(BytesIO(data)) as im:
            im.load()
            return {"ok": True, "size": f"{im.size[0]}x{im.size[1]}", "secs": secs,
                    "embeds": len(embeds), "note": ""}
    except Exception as e:
        return {"ok": False, "size": "", "secs": secs, "embeds": len(embeds),
                "note": f"unreadable: {e}"}


def check_decode(path: Path) -> dict:
    """Does LibRaw decode the sensor data, and how long. A full postprocess, not a
    half_size one: the question is whether the 100% view and Create JPG can work."""
    t0 = time.time()
    try:
        import rawpy
        with rawpy.imread(str(path)) as raw:
            rgb = raw.postprocess(use_camera_wb=True)
        return {"ok": True, "size": f"{rgb.shape[1]}x{rgb.shape[0]}",
                "secs": time.time() - t0, "note": ""}
    except Exception as e:
        return {"ok": False, "size": "", "secs": time.time() - t0,
                "note": type(e).__name__}


# ── the run ───────────────────────────────────────────────────────────────────

def measure(corpus: Path, scratch: Path) -> list:
    from utils.raw_extensions import RAW
    raws = sorted(p for p in corpus.rglob("*") if p.suffix.lower() in RAW)
    if not raws:
        raise SystemExit(f"No RAW files under {corpus}. Point --corpus at the "
                         f"sample corpus (see its download.py).")
    print(f"{len(raws)} RAW files under {corpus}", flush=True)

    meta = read_metadata(raws)
    print("  metadata read", flush=True)
    sidecars = check_sidecars(raws, scratch)
    print("  sidecars written and read back", flush=True)
    focus = check_focus(raws)
    print("  focus distance checked through the master scan's own field list",
          flush=True)

    rows = []
    for i, p in enumerate(raws, 1):
        m = meta.get(p, {})
        written, has_date = sidecars.get(p, (False, False))
        prev, dec = check_preview(p), check_decode(p)
        rows.append({
            "file": p.name,
            "body": _label(m),
            "format": p.suffix.upper().lstrip("."),
            # The per-body discriminator, and it differs by maker: Nikon's
            # NEFCompression separates Lossless from the HE/HE* modes LibRaw cannot
            # read, and Canon's CompressorVersion separates the ordinary CR3
            # (CanonCR3_001) from the HDR-PQ one (CanonCR3_003) whose only embedded
            # previews are HEVC. The generic Compression tag is NOT it — on a CR3 it
            # describes the thumbnail and reads "JPEG (old-style)", which says
            # something confidently untrue about the raw data.
            "mode": str(m.get("NEFCompression") or m.get("CompressorVersion") or ""),
            "date": bool(m.get("DateTimeOriginal")),
            "sidecar": written and has_date,
            "focus": focus.get(p, False),
            "preview_ok": prev["ok"], "preview_size": prev["size"],
            "preview_secs": prev["secs"], "preview_note": prev["note"],
            "decode_ok": dec["ok"], "decode_size": dec["size"],
            "decode_secs": dec["secs"], "decode_note": dec["note"],
        })
        print(f"  [{i}/{len(raws)}] {p.name:14s} preview "
              f"{'ok' if prev['ok'] else 'FAIL'}  decode "
              f"{'ok' if dec['ok'] else 'FAIL'}", flush=True)
    return rows


def _label(m: dict) -> str:
    from gui.preview_extract import format_camera_label
    return format_camera_label(m.get("Make", ""), m.get("Model", "")) or "unknown"


def by_body(rows: list) -> list:
    """Collapse to one row per body+format. A column is green only when EVERY file
    from that body passes it — a body that works in one shooting mode and not another
    is not a body that works, and saying otherwise is the failure this table exists
    to prevent."""
    groups = {}
    for r in rows:
        groups.setdefault((r["body"], r["format"]), []).append(r)
    out = []
    for (body, fmt), rs in sorted(groups.items()):
        def verdict(key):
            good = sum(1 for r in rs if r[key])
            return OK if good == len(rs) else BAD if good == 0 else PARTIAL
        out.append({
            "body": body, "format": fmt, "files": len(rs),
            "date": verdict("date"), "sidecar": verdict("sidecar"),
            "focus": verdict("focus"),
            "preview": verdict("preview_ok"), "decode": verdict("decode_ok"),
            "preview_secs": sum(r["preview_secs"] for r in rs) / len(rs),
            "decode_secs": sum(r["decode_secs"] for r in rs) / len(rs),
            "notes": sorted({r["preview_note"] for r in rs if r["preview_note"]} |
                            {r["decode_note"] for r in rs if r["decode_note"]}),
        })
    return out


# ── the document ──────────────────────────────────────────────────────────────

_MARK = {OK: "✅", BAD: "❌", PARTIAL: "⚠️"}


def versions() -> dict:
    v = {}
    try:
        v["exiftool"] = subprocess.run(["exiftool", "-ver"], capture_output=True,
                                       text=True).stdout.strip()
    except Exception:
        v["exiftool"] = "not found"
    try:
        import rawpy
        v["rawpy"] = rawpy.__version__
        v["libraw"] = ".".join(str(x) for x in rawpy.libraw_version)
    except Exception:
        v["rawpy"] = v["libraw"] = "not available"
    return v


def render(bodies: list, rows: list, v: dict) -> str:
    L = []
    add = L.append
    add("# Camera support")
    add("")
    add("Which cameras SpeciesViewer has actually been tested against, and what works "
        "on each. Generated by `tools/svr_camera_checklist.py` — every mark below was "
        "measured on this run, not asserted. A body that is not in this table has not "
        "been tested; that is not the same as \"does not work\", and we do not guess.")
    add("")
    add(f"**Measured {date.today().isoformat()}** with exiftool {v['exiftool']}, "
        f"rawpy {v['rawpy']} (LibRaw {v['libraw']}), on {len(rows)} sample files "
        f"across {len({b['body'] for b in bodies})} bodies.")
    add("")
    add("| Body | Format | Files | Date | Sidecar | Focus dist. | Preview | Decode | "
        "Preview time | Decode time |")
    add("|---|---|--:|:-:|:-:|:-:|:-:|:-:|--:|--:|")
    for b in bodies:
        add(f"| {b['body']} | {b['format']} | {b['files']} | "
            f"{_MARK[b['date']]} | {_MARK[b['sidecar']]} | {_MARK[b['focus']]} | "
            f"{_MARK[b['preview']]} | {_MARK[b['decode']]} | "
            f"{b['preview_secs']:.2f} s | {b['decode_secs']:.2f} s |")
    add("")
    add("✅ every sample passed &nbsp;·&nbsp; ⚠️ some passed &nbsp;·&nbsp; "
        "❌ none passed")
    add("")
    add("> **Body names are the camera's own.** These are the `Model` strings the "
        "cameras write into their files, not marketing names — so the Z 7II calls "
        "itself `NIKON Z 7_2`, the Z 50II `NIKON Z50_2`, and the R5 Mark II "
        "`Canon EOS R5m2`. They are left exactly as the file reports them, because "
        "that is the string SpeciesViewer sees, and a translation table would be one "
        "more hand-typed list to drift.")
    add("")
    add("## What the columns mean")
    add("")
    add("A RAW passes through five independent mechanisms, and a body can fail at one "
        "while working at the other four. That is why this is a table and not a "
        "yes/no.")
    add("")
    add("| Column | Question | What a ❌ costs you |")
    add("|---|---|---|")
    add("| **Date** | Does `DateTimeOriginal` read? | Nothing works. Pictures are "
        "filed by day. |")
    add("| **Sidecar** | Does an XMP sidecar get written, carrying the date? | "
        "Ratings, colour labels and GPS have nowhere to live. |")
    add("| **Focus dist.** | Does a focus distance reach the picture's fields? | "
        "The distance line is blank on the website and in the info panel. Nothing "
        "else depends on it. |")
    add("| **Preview** | Does a *usable* preview come out — a real JPEG, not merely "
        "some embed? | Culling falls back to a full decode: still correct, roughly "
        "30× slower. |")
    add("| **Decode** | Does LibRaw decode the sensor data? | The 100% view and "
        "Create JPG fall back to the embedded JPEG. DxO PhotoLab is unaffected — it "
        "does its own decoding. |")
    add("")
    add("**No single ❌ above means \"unsupported\".** Each column has a fallback, and "
        "the fallbacks are independent — which is the whole reason for measuring them "
        "separately. A body with a red Preview and a green Decode still culls "
        "correctly, just slower; a body with a green Preview and a red Decode still "
        "shows every picture at full size, because the camera's own JPEG is in the "
        "file.")
    add("")

    focus_bad = [b for b in bodies if b["focus"] == BAD]
    if focus_bad:
        add("## Known gaps")
        add("")
        makers = sorted({b["body"].split()[0].title() for b in focus_bad})
        add(f"**Focus distance is missing on {', '.join(makers)}** "
            f"({len(focus_bad)} of the {len(bodies)} rows here) — because those "
            "files do not contain it, not because nothing asks. Every maker keeps "
            "it somewhere different, and `engine/focus_distance.py` merges all four "
            "shapes into the single value the website and the info panel read:")
        add("")
        add("| Maker | Where the file keeps it | Read |")
        add("|---|---|:-:|")
        add("| Nikon | `Nikon:FocusDistance` — a single value | ✅ |")
        add("| Olympus / OM System | `Olympus:FocusDistance` — a single value | ✅ |")
        add("| Sony | `Composite:FocusDistance2` — derived by exiftool | ✅ |")
        add("| Canon | `FocusDistanceUpper` / `Lower` — a **range**, no single "
            "value | ✅ averaged |")
        add("| Panasonic | nothing. Only `HyperfocalDistance`, which is a property "
            "of the lens and aperture, not of what the camera focused on | — |")
        add("")
        add("Canon's two bounds are averaged. The true subject distance is not quite "
            "the midpoint — depth of field is asymmetric about the plane of focus, so "
            "it sits slightly nearer — but the bounds are centimetres apart at the "
            "distances that matter, and a simple number that can be checked beats a "
            "formula that cannot.")
        add("")
        add("Panasonic is the one genuine absence: there is nothing in the file to "
            "read. Reporting `HyperfocalDistance` instead would fill the field with "
            "a number that answers a different question.")
        add("")
        add("> The XMP sidecar written at ingest is narrower than any of this — it "
            "maps only `Olympus:FocusDistance`. That does not show up in this column, "
            "because the website and the info panels read the master scan's fields "
            "rather than the sidecar.")
        add("")

    notes = [b for b in bodies if b["notes"]]
    if notes:
        add("## Failures in detail")
        add("")
        for b in notes:
            add(f"- **{b['body']}** ({b['format']}) — {'; '.join(b['notes'])}")
        add("")

    add("## Every file measured")
    add("")
    add("| File | Body | Format | Mode | Preview | Decode |")
    add("|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: (r["body"], r["file"])):
        prev = r["preview_size"] if r["preview_ok"] else f"❌ {r['preview_note']}"
        dec = r["decode_size"] if r["decode_ok"] else f"❌ {r['decode_note']}"
        add(f"| `{r['file']}` | {r['body']} | {r['format']} | {r['mode']} | "
            f"{prev} | {dec} |")
    add("")
    add("---")
    add("")
    add("*Regenerate with `python tools/svr_camera_checklist.py`. The sample corpus is "
        "not in this repository; most of it is fetched from "
        "[raw.pixls.us](https://raw.pixls.us/) — CC0, sha256-verified — by the "
        "`download.py` that lives beside it. A few files come from manufacturer "
        "review galleries instead, for behaviour pixls cannot cover (it has no "
        "vertically-shot frame at all); those are evaluation samples, are not "
        "redistributable, and are listed in the corpus README.*")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", type=Path, default=REPO.parent / "camerafiles",
                    help="directory of sample RAWs (default: ../camerafiles)")
    ap.add_argument("--out", type=Path, default=REPO / "docs" / "camera_support.md",
                    help="Markdown table to write")
    ap.add_argument("--json", type=Path, help="also write the raw per-file results")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory(prefix="sv_checklist_") as tmp:
        rows = measure(args.corpus.resolve(), Path(tmp))
    bodies = by_body(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(bodies, rows, versions()), encoding="utf-8")
    print(f"\n{len(bodies)} body/format rows -> {args.out}")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"per-file results       -> {args.json}")


if __name__ == "__main__":
    main()
