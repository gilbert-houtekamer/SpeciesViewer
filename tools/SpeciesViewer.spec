# PyInstaller recipe for the Windows release ZIP.
#
#   python tools/svr_build_zip.py        <- builds with this file, adds exiftool
#                                           and "Read me first.txt", and zips it
#
# The two licence build rules (reasoning in docs/licensing_background.md):
#   1. one FOLDER, never one file — Qt6, LibRaw (raw_r.dll), libheif stay
#      replaceable files, which is what their LGPL licences ask for;
#   2. no libx265 — pillow-heif's HEIC *encoder* is GPLv2; SpeciesViewer only
#      decodes HEIC, so the encoder is left out instead of argued about.
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent            # repo root (this file is in tools/)
SV = ROOT / "speciesviewer"

# SpeciesViewer.py puts speciesviewer/ on sys.path, so its packages are
# top-level (gui, engine, …). Include every module: a few are imported by
# name at run time (gui.place_sync, gui.connection_status, …) and PyInstaller
# cannot see those.
OWN_PACKAGES = ["gui", "engine", "utils", "db", "widgets", "settings",
                "dialogs", "batch"]
hidden = [m for p in OWN_PACKAGES for m in collect_submodules(p)]

# Resources the code finds relative to the speciesviewer/ folder
# (Path(__file__).parent.parent / "dist", …). In the build that folder is
# _internal/, so each goes to the same relative place.
datas = [
    (str(SV / "assets"), "assets"),
    (str(SV / "HTMLtemplates"), "HTMLtemplates"),
    (str(SV / "dist" / "installation files"), "dist/installation files"),
    (str(SV / "dist" / "includes web"), "dist/includes web"),
    (str(SV / "gui" / "sv_exiftool.config"), "gui"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "NOTICE.md"), "."),
]

a = Analysis(
    [str(SV / "SpeciesViewer.py")],
    pathex=[str(SV), str(ROOT)],
    datas=datas,
    hiddenimports=hidden,
    # Development-only packages that are installed in the venv.
    excludes=["pytest", "black", "ruff", "bm3d", "PyInstaller"],
)
a.binaries = [b for b in a.binaries if "libx265" not in b[0].lower()]

pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="SpeciesViewer",
    icon=str(SV / "assets" / "icon" / "speciesviewer.ico"),   # the .exe in File Explorer
    # No console window for users. For a crash hunt, set True and rebuild:
    # the traceback then shows instead of vanishing.
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SpeciesViewer")
