import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, Union

logger = logging.getLogger("speciesviewer")

# On Windows the Perl wrapper transcodes argv through the active ANSI code page
# before exiftool's own -charset directive can interpret it, so non-ASCII values
# in command-line args (e.g. ü) are silently replaced with '?'. Passing the same
# args via -@ argfile avoids the transcode: exiftool reads the file's raw bytes
# and respects -charset utf8.
def exiftool_write(
    filepath: Union[str, Path],
    exif_args: Iterable[str],
    *,
    overwrite_original: bool = True,
) -> None:
    args = list(exif_args)
    if overwrite_original:
        args.append("-overwrite_original")

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
