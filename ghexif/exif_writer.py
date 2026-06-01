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
