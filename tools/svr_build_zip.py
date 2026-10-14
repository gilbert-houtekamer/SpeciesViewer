#!/usr/bin/env python3
"""Build the SpeciesViewer ZIP (0.4 order, 2026-10-11).

ONE JOB: PyInstaller one-folder build from tools/SpeciesViewer.spec, the pinned
official exiftool beside the .exe, then one ZIP of that folder, named after the
version. Everything lands in build/ (git-ignored); the source tree is not
touched.

    python tools/svr_build_zip.py [--exiftool-zip PATH]

exiftool: the official Windows 64-bit package from exiftool.org, pinned in
tools/exiftool_pin.json (version + SHA-256). The download itself is not in git;
it is looked for in build/vendor/ and in your Downloads folder, or given with
--exiftool-zip. Any other file is refused — the ZIP ships the version we
tested. It lands in <SpeciesViewer>/exiftool/, where utils/exiftool_path.py
finds it (a copy chosen with Locate… still wins, for upgrades).
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
PIN = json.loads((ROOT / "tools" / "exiftool_pin.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(ROOT / "speciesviewer"))
from utils.version import __version__  # noqa: E402


def find_exiftool_zip(given: str | None) -> Path:
    candidates = [Path(given)] if given else [
        BUILD / "vendor" / PIN["zip"], Path.home() / "Downloads" / PIN["zip"]]
    for c in candidates:
        if c.is_file():
            return c
    raise SystemExit(
        f"FAILED: {PIN['zip']} not found (looked in: "
        f"{', '.join(str(c) for c in candidates)}).\n"
        f"Download exiftool {PIN['version']}, Windows 64-bit, from exiftool.org.")


def check_exiftool_zip(zip_path: Path) -> None:
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if digest != PIN["sha256"]:
        raise SystemExit(
            f"FAILED: {zip_path} is not the pinned exiftool {PIN['version']} "
            f"(SHA-256 {digest}, expected {PIN['sha256']}). A newer version: test "
            f"it, then update tools/exiftool_pin.json.")


def install_exiftool(zip_path: Path, app_folder: Path) -> str:
    """Unpack into <app>/exiftool/, rename exiftool(-k).exe → exiftool.exe as
    exiftool.org instructs, and return the version it reports."""
    target = app_folder / "exiftool"
    if target.exists():
        shutil.rmtree(target)
    with zipfile.ZipFile(zip_path) as z:
        top = z.namelist()[0].split("/")[0]          # exiftool-13.59_64/
        for member in z.namelist():
            rel = member[len(top) + 1:]
            if not rel or member.endswith("/"):
                continue
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(z.read(member))
    (target / "exiftool(-k).exe").rename(target / "exiftool.exe")
    out = subprocess.run([str(target / "exiftool.exe"), "-ver"],
                         capture_output=True, text=True, timeout=60)
    return out.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--exiftool-zip", help=f"path to {PIN['zip']}")
    args = ap.parse_args()
    t0 = time.time()
    windows = sys.platform.startswith("win")
    if windows:                      # fail fast: before the long PyInstaller run
        exiftool_zip = find_exiftool_zip(args.exiftool_zip)
        check_exiftool_zip(exiftool_zip)

    dist, work = BUILD / "pyinstaller-dist", BUILD / "pyinstaller-work"
    rc = subprocess.call([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                          "--distpath", str(dist), "--workpath", str(work),
                          str(ROOT / "tools" / "SpeciesViewer.spec")])
    if rc:
        print(f"PyInstaller failed ({rc})")
        return rc
    folder = dist / "SpeciesViewer"
    # Build rule 2 (NOTICE.md): the GPLv2 HEIC encoder must not ship.
    x265 = [p.name for p in folder.rglob("*x265*")]
    if x265:
        print(f"FAILED: libx265 found in the build: {x265}")
        return 1
    # matplotlib comes along only because pygbif imports it; its demo pictures
    # (grace_hopper.jpg, …) are no use to anyone and look odd in the folder.
    shutil.rmtree(folder / "_internal" / "matplotlib" / "mpl-data" / "sample_data",
                  ignore_errors=True)
    exiftool_note = "no exiftool (not Windows: install it with the system)"
    if windows:
        version = install_exiftool(exiftool_zip, folder)
        if version != PIN["version"]:
            print(f"FAILED: the shipped exiftool reports {version!r}, "
                  f"expected {PIN['version']}")
            return 1
        exiftool_note = f"exiftool {version}"
    # "Read me first.txt": start, the Windows warning, first steps.
    shutil.copy2(ROOT / "tools" / "Read me first.txt", folder / "Read me first.txt")
    for name in ("LICENSE", "NOTICE.md"):      # beside the .exe, where people look
        shutil.copy2(ROOT / name, folder / name)
    platform = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
    zip_base = BUILD / f"SpeciesViewer-{__version__}-{platform}"
    zip_path = shutil.make_archive(str(zip_base), "zip", root_dir=dist, base_dir="SpeciesViewer")
    files = sum(1 for p in folder.rglob("*") if p.is_file())
    size = Path(zip_path).stat().st_size / 1e6
    # ASCII only: this output is often redirected, and a Windows code page
    # cannot encode a check mark.
    print(f"OK {zip_path}\n  {files} files, {size:.0f} MB zipped, no libx265, "
          f"{exiftool_note} - {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
