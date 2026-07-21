"""Albums ▸ Delete Album — remove the active album, reversibly.

Acts on the ACTIVE album (the one in the Album tab), the same way Build Website
and Write Blogs do — so there is no "which album" ambiguity. It is deliberately
NOT reachable through any Trip door: an album is its own thing.

Review-then-confirm, with a backup: the folder is zipped into the albums-root
.trash first, so an accidental delete is undone by unzipping. The master is never
touched — the album only ever referenced it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFrame, QLabel,
    QMessageBox, QVBoxLayout,
)

from utils.config import _config, set_setting
from engine import album_delete

logger = logging.getLogger("speciesviewer")


def _human(nbytes: int) -> str:
    mb = nbytes / (1024 * 1024)
    return f"{mb:.1f} MB" if mb >= 1 else f"{nbytes / 1024:.0f} KB"


class AlbumDeleteDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.passed_parent = parent
        self.setWindowTitle("Delete Album")
        self.setMinimumWidth(560)

        root_cfg = (_config.get("virttrip", "virttripbiotadatabase", fallback="")
                    or "").replace('"', "").replace("'", "").strip()
        web_cfg = (_config.get("virttrip", "virttripbiotawebroot", fallback="")
                   or "").replace('"', "").replace("'", "").strip()
        self._album_root = root_cfg
        self._web_root = web_cfg
        self._info = album_delete.scan_album(root_cfg, web_cfg) if root_cfg else None

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        if not root_cfg or not self._info or not self._info["exists"]:
            layout.addWidget(QLabel(
                "No album is selected. Open an album in the Album tab first, then "
                "delete it here."))
            box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            box.rejected.connect(self.reject)
            box.accepted.connect(self.reject)
            layout.addWidget(box)
            self._can_delete = False
            return
        self._can_delete = True

        info = self._info
        layout.addWidget(QLabel(
            f"Delete the album <b>{info['name']}</b>?"))

        detail = QLabel(
            f"<b>Local folder</b> — {info['root']}<br>"
            f"{_human(info['size_bytes'])}, {info['picture_count']} day(s) of "
            f"picture data. Zipped into <i>.trash</i> before deletion, so it can "
            f"be restored by unzipping.")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        # Remote deletion is only offered when the album was published AND the
        # server is reachable — otherwise the checkbox would promise something it
        # cannot do.
        import engine.connectivity as connectivity
        self._reachable = False
        try:
            self._reachable = connectivity.publish_reachable()
        except Exception:
            self._reachable = False

        self._remote_cb = QCheckBox("Also delete the published website")
        if info["web_root"] and info["has_local_web"] and self._reachable:
            self._remote_cb.setChecked(True)
            self._remote_cb.setText(
                f"Also delete the published website  ({info['web_root']})")
        else:
            self._remote_cb.setChecked(False)
            self._remote_cb.setEnabled(False)
            if not info["web_root"] or not info["has_local_web"]:
                self._remote_cb.setText(
                    "Also delete the published website  (this album was never published)")
            else:
                self._remote_cb.setText(
                    "Also delete the published website  (server unreachable)")
        layout.addWidget(self._remote_cb)

        # An advertised album would leave a dead tripindex link — but that
        # un-advertise step is a separate feature (the Albums publish action),
        # so say so rather than half-do it.
        note = QLabel(
            "The <b>master is not touched</b> — an album only references the "
            "master's pictures, it never copies them. If this album is advertised "
            "on the trip index, remove it there separately.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(note)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(sep)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel)
        self._delete_btn = buttons.addButton(
            "Delete album", QDialogButtonBox.ButtonRole.DestructiveRole)
        self._delete_btn.clicked.connect(self._do_delete)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ── delete ───────────────────────────────────────────────────────────────
    def _do_delete(self):
        info = self._info
        remote = self._remote_cb.isChecked() and self._remote_cb.isEnabled()
        confirm = QMessageBox.question(
            self, "Delete Album",
            f"Delete <b>{info['name']}</b>"
            + (" and its published website" if remote else "")
            + "?<br><br>The folder is backed up to <i>.trash</i> first; the master "
            "is not affected.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if confirm != QMessageBox.StandardButton.Yes:
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            # 1. Remote first, while the LOCAL FTPcache.json still exists — not
            #    that rmtree needs it (it lists the server directly), but doing
            #    the network step before the irreversible local wipe means a
            #    remote failure leaves the album intact to retry.
            if remote:
                self._delete_remote(info)
            # 2. Back up, then remove the folder.
            backup = album_delete.backup_album(self._album_root)
            ok = album_delete.delete_local(self._album_root)
        finally:
            QApplication.restoreOverrideCursor()

        if not ok:
            QMessageBox.warning(
                self, "Delete Album",
                "The album folder could not be fully removed — something may have "
                "it open. The backup zip was still written.")
            return

        # 3. It was the active album; it is gone. Clear the pointers so the GUI
        #    does not try to read a folder that no longer exists.
        set_setting("virttrip", "virttripbiotadatabase", "")
        set_setting("virttrip", "virttripbiotawebroot", "")
        self._refresh_gui()
        logger.info(f"Album deleted: {info['name']}"
                    + (f"  (backup: {backup.name})" if backup else ""))
        QMessageBox.information(
            self, "Delete Album",
            f"Deleted <b>{info['name']}</b>."
            + (f"<br>Backup: <i>{backup.name}</i>" if backup else ""))
        self.accept()

    def _delete_remote(self, info):
        """rmtree the published tree. Mirrors svv4's connect; rmtree invalidates
        the upload cache as it walks."""
        import engine.sftplib
        password = (_config.get("websettings", "Sftppwd", fallback="") or "").strip("'")
        sftp = engine.sftplib.Sftp(
            hostname="houtfp.ssh.transip.me", username="houtekamercom",
            password=password)
        try:
            sftp.connect()
            target = album_delete.remote_web_target(info["web_root"], sftp.remotePrefix())
            if target:
                logger.info(f"Deleting published album tree: {target}")
                sftp.rmtree(target)
        except Exception as e:
            logger.warning(f"album delete: remote removal failed: {e}")
            QMessageBox.warning(
                self, "Delete Album",
                f"Could not delete the published website:\n\n{e}\n\n"
                "The local album will still be deleted.")
        finally:
            try:
                sftp.disconnect()
            except Exception:
                pass

    def _refresh_gui(self):
        try:
            from gui.virttrip_overview import populate_virttrip_overview
            populate_virttrip_overview(self.passed_parent)
            self.passed_parent.virttrip_panel.rebuild()
        except Exception as e:
            logger.info(f"album delete: GUI refresh: {e}")
