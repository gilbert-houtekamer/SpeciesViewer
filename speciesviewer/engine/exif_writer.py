import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Iterable, Union

logger = logging.getLogger("speciesviewer")

# On Windows the Perl wrapper transcodes argv through the active ANSI code page
# before exiftool's own -charset directive can interpret it, so non-ASCII values
# in command-line args (e.g. ü) are silently replaced with '?'. Passing the same
# args via -@ argfile avoids the transcode: exiftool reads the file's raw bytes
# and respects -charset utf8.
# ── persistent writer ─────────────────────────────────────────────────────────
# Starting exiftool costs ~270 ms; the write itself costs almost nothing. That is
# invisible in a batch (one spawn for the lot) but it IS the delay felt when
# culling one picture at a time in the large viewer — colour, next, colour, next.
# One stay_open process, kept for the life of the app, removes it.
#
# encoding="utf-8" is not optional. The argfile below exists because Windows
# transcodes argv through the ANSI code page and silently turns "ü" into "?";
# stay_open passes arguments over a pipe instead, and this is what makes that
# pipe UTF-8. Same protection, different route.
#
# Mirrors gui/preview_extract's reader: a lock, a lazy start, and a drop on any
# failure so the next call restarts clean.
_writer_lock = threading.Lock()
_writer = None


def _get_writer():
    """A running ExifToolHelper for writing. Caller must hold _writer_lock."""
    global _writer
    if _writer is not None and _writer.running:
        return _writer
    import exiftool
    _writer = exiftool.ExifToolHelper(
        common_args=["-charset", "utf8", "-charset", "filename=utf8"],
        encoding="utf-8", check_execute=False)
    _writer.run()
    return _writer


def _drop_writer():
    """Tear down a wedged writer. Caller must hold _writer_lock."""
    global _writer
    try:
        if _writer is not None and _writer.running:
            _writer.terminate()
    except Exception:
        pass
    _writer = None


def close_persistent_writer():
    """Shut the shared writer down (app exit). Safe to call when none is open."""
    with _writer_lock:
        _drop_writer()


def _write_via_persistent(filepath, args) -> bool:
    """One write through the shared process. False means 'not done' — the
    caller then falls back to a fresh subprocess, so a broken helper can never
    turn into a lost edit."""
    with _writer_lock:
        try:
            et = _get_writer()
            out = et.execute(*args, str(filepath)) or ""
        except Exception as e:
            logger.warning(f"persistent exiftool write failed for "
                           f"{Path(filepath).name}: {e}")
            _drop_writer()
            return False
        # check_execute=False, so a failure is reported in the text rather than
        # raised. "0 image files updated" is a failure with a zero exit code —
        # exactly the kind that would otherwise pass unnoticed.
        if "updated" not in out or out.strip().startswith("Error"):
            logger.warning(f"persistent exiftool write did not update "
                           f"{Path(filepath).name}: {out.strip()[:200]}")
            return False
        return True


def exiftool_write(
    filepath: Union[str, Path],
    exif_args: Iterable[str],
    *,
    overwrite_original: bool = True,
    persistent: bool = True,
) -> None:
    args = list(exif_args)
    if overwrite_original:
        args.append("-overwrite_original")

    if persistent and _write_via_persistent(filepath, args):
        return

    fd, tmp_path = tempfile.mkstemp(prefix="exiftool_args_", suffix=".txt", text=False)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tmp:
            for arg in args:
                tmp.write(arg + "\n")
        cmd = [
            "exiftool",
            "-charset", "utf8",
            "-charset", "filename=utf8",
            "-@", tmp_path,
            str(filepath),
        ]
        subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
            encoding="utf-8",
        )
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def exiftool_write_batch(items, *, overwrite_original: bool = True) -> int:
    """Write DIFFERENT tags to many files in ONE exiftool run.

    `items` is [(filepath, [args…]), …]. run_exiftool_files covers the common
    case where every file gets the SAME value; this covers the other one — a GPS
    backfill gives each picture its own coordinates, so there is nothing to
    share.

    Uses -@ argfile with -execute between commands: exiftool runs each command
    in sequence inside a single process. Spawning exiftool costs ~270 ms and the
    write itself almost nothing, so N files go from N spawns to one.

    Everything travels in the argfile, which is also what keeps a long file list
    clear of the Windows command-line limit. Returns the number of commands sent.
    """
    items = [(str(f), list(a)) for f, a in items if a]
    if not items:
        return 0

    fd, tmp_path = tempfile.mkstemp(prefix="exiftool_batch_", suffix=".txt", text=False)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as tmp:
            for filepath, args in items:
                for arg in args:
                    tmp.write(arg + "\n")
                if overwrite_original:
                    tmp.write("-overwrite_original\n")
                tmp.write(filepath + "\n")
                tmp.write("-execute\n")
        cmd = ["exiftool", "-charset", "utf8", "-charset", "filename=utf8",
               "-@", tmp_path]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8")
        if proc.returncode:
            logger.warning(
                f"exiftool batch write: {len(items)} command(s), exit "
                f"{proc.returncode}: {(proc.stderr or '').strip()[:300]}")
        return len(items)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def run_exiftool_files(option_args, files, *, pre_args=None):
    """Run exiftool with `option_args` over many `files`, passing EVERYTHING
    (options + file paths) through a -@ argfile. This avoids the OS command-line
    length limit (~32 KB on Windows) that silently breaks a single exiftool call
    over hundreds of files — the cause of '0 sidecars' on a big card. `pre_args`
    go on the command line BEFORE -@ (e.g. ["-config", path]). Returns the
    CompletedProcess (stdout/stderr captured)."""
    files = [str(f) for f in files]
    fd, arg_path = tempfile.mkstemp(prefix="exiftool_files_", suffix=".txt", text=False)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            for token in list(option_args) + files:
                f.write(token + "\n")
        cmd = (["exiftool"] + list(pre_args or [])
               + ["-charset", "utf8", "-charset", "filename=utf8", "-@", arg_path])
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, encoding="utf-8")
    finally:
        try:
            os.remove(arg_path)
        except OSError:
            pass
