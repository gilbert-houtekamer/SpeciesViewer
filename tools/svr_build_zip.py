#!/usr/bin/env python3
"""Build the rough SpeciesViewer ZIP (0.4 order, 2026-10-11).

ONE JOB: PyInstaller one-folder build from tools/SpeciesViewer.spec, then one
ZIP of that folder, named after the version. Everything lands in build/
(git-ignored); the source tree is not touched.

    python tools/svr_build_zip.py

Rough: exiftool must be on the PATH of the machine that runs it.
"""
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
sys.path.insert(0, str(ROOT / "speciesviewer"))
from utils.version import __version__  # noqa: E402


def main() -> int:
    t0 = time.time()
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
    platform = {"win32": "windows", "darwin": "macos"}.get(sys.platform, sys.platform)
    zip_base = BUILD / f"SpeciesViewer-{__version__}-{platform}"
    zip_path = shutil.make_archive(str(zip_base), "zip", root_dir=dist, base_dir="SpeciesViewer")
    files = sum(1 for p in folder.rglob("*") if p.is_file())
    size = Path(zip_path).stat().st_size / 1e6
    # ASCII only: this output is often redirected, and a Windows code page
    # cannot encode a check mark.
    print(f"OK {zip_path}\n  {files} files, {size:.0f} MB zipped, no libx265 - "
          f"{time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
