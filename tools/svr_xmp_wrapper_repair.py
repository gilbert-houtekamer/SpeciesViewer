#!/usr/bin/env python3
"""Put the <?xpacket?> wrapper back on sidecars that lost it.

An XMP sidecar is an xpacket: a `<?xpacket begin=...?>` processing instruction,
the `x:xmpmeta` document, and a `<?xpacket end="w"?>` trailer. SpeciesViewer's
old crop writer rewrote sidecars with ElementTree, which knows nothing about that
wrapper and dropped it. exiftool can then REFUSE to write the file at all —
"0 image files updated ... 1 files weren't updated due to errors" — so every
later colour, rating or place on that picture goes nowhere.

The file is not otherwise damaged: the metadata inside it is intact. Re-wrapping
makes it writable again without touching a single tag.

Each repair is verified by asking exiftool to write to the file afterwards; if it
still refuses, the original is restored and the file is reported, never left in a
half-fixed state.

  python tools/svr_xmp_wrapper_repair.py <picture-root> [--apply]
  e.g. python tools/svr_xmp_wrapper_repair.py "D:/CurrentPictures/2026/2026 Brazil" --apply
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "speciesviewer"))

BEGIN = '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
END = '\n<?xpacket end="w"?>\n'


def needs_repair(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(40)
    except OSError:
        return False
    return not head.lstrip("\ufeff").lstrip().startswith("<?xpacket")


def repair(path) -> tuple[bool, str]:
    """Dedupe, re-wrap, and prove exiftool can write the result. (ok, note).

    Two separate injuries, in this order:

    1. DUPLICATE PROPERTIES. The old writer set the crs: crop as ATTRIBUTES on
       rdf:Description while exiftool and PhotoLab keep them as ELEMENTS, so the
       file came to hold both and exiftool refused it outright: "Error: [minor]
       Duplicate XMP property: crs:CropTop". It is flagged minor, so `-m` lets
       exiftool proceed — and rewriting the file is what removes the duplicate.
       `-m` belongs HERE, in a deliberate one-off repair, not in the app: the app
       should meet a malformed sidecar loudly, not work around one quietly.
    2. THE MISSING WRAPPER, re-added afterwards, because exiftool preserves the
       shape it found and will not put an xpacket back on its own.

    The final check is a STRICT write (no -m) through the app's own writer: the
    repair has to leave a file SpeciesViewer can write, not merely one exiftool
    can be persuaded to write.
    """
    import subprocess

    from engine.exif_writer import exiftool_write

    path = Path(path)
    backup = path.with_suffix(path.suffix + ".prewrap")
    shutil.copy2(path, backup)
    try:
        # 1. dedupe (harmless value: the crop's own constrain flag)
        subprocess.run(["exiftool", "-m", "-XMP-crs:CropConstrainToWarp=0",
                        "-overwrite_original", str(path)],
                       capture_output=True, text=True, check=False)
        # 2. re-wrap whatever exiftool left behind
        body = path.read_text(encoding="utf-8", errors="replace").strip()
        if not body.startswith("<?xpacket"):
            path.write_text(BEGIN + body + END, encoding="utf-8")
        # 3. strict proof
        exiftool_write(path, ["-XMP-crs:CropConstrainToWarp=0"])
    except Exception as e:                                # noqa: BLE001
        shutil.copy2(backup, path)
        backup.unlink(missing_ok=True)
        return False, f"still unwritable, original restored: {e}"
    backup.unlink(missing_ok=True)
    return True, "deduped, re-wrapped, verified writable"


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    apply_ = "--apply" in argv
    if not args:
        print(__doc__)
        return 2
    root = Path(args[0])
    broken = sorted(p for p in root.rglob("*.xmp") if needs_repair(p))
    print(f"{len(broken)} sidecar(s) missing the xpacket wrapper\n")
    for p in broken:
        print(f"  {p.relative_to(root)}")
    if not apply_:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0
    ok = fail = 0
    print()
    for p in broken:
        good, note = repair(p)
        if good:
            ok += 1
        else:
            fail += 1
            print(f"  FAILED {p.name}: {note}")
    print(f"\nrepaired {ok}, still broken {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
