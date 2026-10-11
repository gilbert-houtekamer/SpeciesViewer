"""Publishing to THIS computer — the local website (docs/plans/website_optional_plan.md).

ONE JOB: the same methods as engine.sftplib.Sftp, done as file copies into a
folder with exactly the live site's layout, so every publishing step works
unchanged; utils/local_web.py shows the folder in the browser.

remotePrefix() is that folder, so the callers' "prefix + /nature/…" and
"prefix + /trips/<trip>/…" land in the same places as on the server.

INVARIANTS
- No upload cache. admin/FTPcache.json records what the SERVER already has;
  writing local copies there would make a later server publish skip files the
  server never received. openFTPCache/closeFTPCache do nothing.
- cachedUpload keeps Sftp's meaning: True = skipped (unchanged), False = sent.
- Publishing only. GBIF, place names and translation still need the internet;
  nothing here touches Project.online or their reachability checks.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("speciesviewer")


class LocalSite:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.remote_path = self.root.as_posix()      # what Sftp calls its prefix
        self.copied = 0

    def _p(self, remote_path) -> Path:
        return Path(remote_path)

    # ── connection: nothing to open ────────────────────────────────────────
    def remotePrefix(self):
        return self.remote_path

    def connect(self):
        logger.info(f"Publishing to this computer: {self.root}")

    def disconnect(self):
        if self.copied:
            logger.info(f"Local website: {self.copied} file(s) updated.")

    # ── the upload cache belongs to the server — never touched here ────────
    def openFTPCache(self, cachefilepath):
        return

    def closeFTPCache(self, cachefilepath):
        return

    # ── reading ────────────────────────────────────────────────────────────
    def listdir(self, remote_path):
        p = self._p(remote_path)
        yield from (os.listdir(p) if p.is_dir() else [])

    def listdir_attr(self, remote_path):
        p = self._p(remote_path)
        yield from (os.scandir(p) if p.is_dir() else [])

    def download(self, remote_path, target_local_path):
        Path(target_local_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self._p(remote_path), target_local_path)

    # ── writing ────────────────────────────────────────────────────────────
    def upload(self, source_local_path, remote_path, preserve_mtime=True):
        try:
            dest = self._p(remote_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_local_path, dest)
            self.copied += 1
        except OSError as err:
            logger.warning(f"Local website: copying '{source_local_path}' failed, {err}")

    def cachedUpload(self, source_local_path, remote_path, preserve_mtime=True):
        """Copy only when the file differs (size or time) — True = skipped,
        False = copied, exactly as Sftp.cachedUpload."""
        if not os.path.isfile(source_local_path):
            logger.warning(f"Local website: source missing: {source_local_path}")
            return True
        dest = self._p(remote_path)
        try:
            s, d = os.stat(source_local_path), dest.stat()
            if s.st_size == d.st_size and int(s.st_mtime) == int(d.st_mtime):
                return True
        except OSError:
            pass
        self.upload(source_local_path, remote_path)
        return False

    def makedirs(self, remote_path):
        self._p(remote_path).mkdir(parents=True, exist_ok=True)

    def ensure_remote_dir(self, remote_path):
        self._p(remote_path).parent.mkdir(parents=True, exist_ok=True)

    def safeMkdir(self, remotepath):
        self.makedirs(remotepath)

    def cached_mkdir(self, remote_path):
        existed = self._p(remote_path).is_dir()
        self.makedirs(remote_path)
        return existed

    def put_r_portable(self, localdir, remotedir, preserve_mtime=True):
        """Copy a folder tree; unchanged files are skipped. One summary line
        instead of a line per file — a local copy is not worth reporting."""
        if not os.path.isdir(localdir):
            logger.warning(f"Local website: cannot list '{localdir}'")
            return
        for dirpath, _dirs, files in os.walk(localdir):
            rel = os.path.relpath(dirpath, localdir).replace("\\", "/")
            target = remotedir if rel == "." else remotedir + "/" + rel
            self.makedirs(target)
            for name in files:
                self.cachedUpload(os.path.join(dirpath, name), target + "/" + name)

    # ── deleting ───────────────────────────────────────────────────────────
    def _inside(self, p: Path) -> bool:
        """Deletes never reach outside the local website: a wrong prefix must
        not remove anything elsewhere on this computer."""
        if self.root.resolve() in p.resolve().parents:
            return True
        logger.warning(f"Local website: refused to delete {p} — outside {self.root}")
        return False

    def remove(self, remote_path):
        if not self._inside(self._p(remote_path)):
            return
        try:
            self._p(remote_path).unlink()
            logger.info(f"Local website: removed {remote_path}")
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning(f"Local website: could not remove {remote_path}: {e}")

    def rmtree(self, remote_path):
        p = self._p(remote_path)
        if not p.exists() or not self._inside(p):
            return
        shutil.rmtree(p, ignore_errors=True)
        logger.info(f"Local website: removed {remote_path}")
