"""PhotoLab Denoise settings — which DxO denoise method SpeciesViewer stamps into
a picture's `.dop` when it writes a crop (the "preparing a JPG" moment), so the
RAW opens in PhotoLab already set to the method this machine uses.

The method code is PhotoLab-version specific (DeepPRIME XD3 = `DeepRaw2RGBv7` in
PL9, DeepPRIME 3 = `DeepRaw2RGBv6`), so you pick your PL version, then the method.
The label→code table is viewable and editable right here (it also lives in
photolab_denoise.ini). Written ONLY on the crop path — a RAW you never crop is
untouched. See utils/photolab_denoise.py.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QComboBox, QCheckBox, QLabel,
    QDialogButtonBox, QFrame, QTableWidget, QTableWidgetItem, QPushButton,
    QGroupBox, QHeaderView,
)

from utils.photolab_denoise import (
    load_map, save_map, load_selection, save_selection, ini_path,
)
from utils.open_path import reveal_path


class PhotoLabDenoiseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PhotoLab Denoise")
        self.setMinimumWidth(560)

        self._map = load_map()               # {version: [(label, code), ...]}
        sel = load_selection()
        self._cur_version = sel["version"] if sel["version"] in self._map \
            else (next(iter(self._map), ""))
        self._populating = False

        root = QVBoxLayout(self)
        root.setSpacing(8)

        intro = QLabel(
            "When SpeciesViewer writes a crop to a picture's DxO .dop, it can also "
            "set the denoise method — so the RAW opens in PhotoLab already on the "
            "method you use here. This happens ONLY when you crop (preparing a JPG); "
            "a RAW you never crop is left untouched.")
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(intro)

        self._enabled = QCheckBox("Set the denoise method when I write a crop")
        self._enabled.setChecked(sel["enabled"])
        self._enabled.toggled.connect(self._sync_enabled)
        root.addWidget(self._enabled)

        form = QFormLayout()
        self._version = QComboBox()
        self._version.addItems(list(self._map.keys()))
        vi = self._version.findText(self._cur_version)
        if vi >= 0:
            self._version.setCurrentIndex(vi)
        self._version.currentTextChanged.connect(self._on_version_changed)
        form.addRow("PhotoLab version:", self._version)

        self._method = QComboBox()
        form.addRow("Active method:", self._method)
        root.addLayout(form)

        # ── editable code table for the selected version ──────────────────────
        box = QGroupBox("Method → .dop code  (edit to fix or add methods)")
        bl = QVBoxLayout(box)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Method (as shown in PhotoLab)", ".dop code"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.itemChanged.connect(self._on_table_changed)
        bl.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add method")
        add_btn.clicked.connect(self._add_row)
        rm_btn = QPushButton("Remove selected")
        rm_btn.clicked.connect(self._remove_row)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rm_btn)
        btn_row.addStretch()
        bl.addLayout(btn_row)
        root.addWidget(box)

        hint = QLabel(
            'Empty code = &ldquo;Standard&rdquo; (writes <code>NoiseActive = true</code> only). '
            'Confirmed in PL9: DeepPRIME&nbsp;3 = <code>DeepRaw2RGBv6</code>, '
            'DeepPRIME&nbsp;XD3 = <code>DeepRaw2RGBv7</code>.')
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(hint)

        path_row = QHBoxLayout()
        path_lbl = QLabel(f"File: <code>{ini_path()}</code>")
        path_lbl.setStyleSheet("color: #888; font-size: 10px;")
        path_lbl.setTextInteractionFlags(path_lbl.textInteractionFlags()
                                         | Qt.TextInteractionFlag.TextSelectableByMouse)
        show_btn = QPushButton("Show file")
        show_btn.clicked.connect(lambda: reveal_path(ini_path()))
        path_row.addWidget(path_lbl, 1)
        path_row.addWidget(show_btn)
        root.addLayout(path_row)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._load_version(self._cur_version, want_method=sel["method"])
        self._sync_enabled(self._enabled.isChecked())

    # ── table / combo population ──────────────────────────────────────────────
    def _load_version(self, version: str, want_method: str | None = None):
        self._populating = True
        rows = self._map.get(version, [])
        self._table.setRowCount(0)
        for label, code in rows:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(label))
            self._table.setItem(r, 1, QTableWidgetItem(code))
        self._populating = False
        self._rebuild_method_combo(want_method)

    def _rebuild_method_combo(self, want: str | None = None):
        keep = want if want is not None else self._method.currentText()
        self._method.blockSignals(True)
        self._method.clear()
        for r in range(self._table.rowCount()):
            it = self._table.item(r, 0)
            if it and it.text().strip():
                self._method.addItem(it.text().strip())
        mi = self._method.findText(keep)
        if mi >= 0:
            self._method.setCurrentIndex(mi)
        self._method.blockSignals(False)

    def _commit_table(self):
        """Read the table back into self._map for the current version."""
        rows = []
        for r in range(self._table.rowCount()):
            li = self._table.item(r, 0)
            ci = self._table.item(r, 1)
            label = (li.text().strip() if li else "")
            code = (ci.text().strip() if ci else "")
            if label:
                rows.append((label, code))
        self._map[self._cur_version] = rows

    # ── events ────────────────────────────────────────────────────────────────
    def _on_version_changed(self, version: str):
        self._commit_table()
        self._cur_version = version
        self._load_version(version)

    def _on_table_changed(self, _item):
        if not self._populating:
            self._rebuild_method_combo()

    def _add_row(self):
        r = self._table.rowCount()
        self._table.insertRow(r)
        self._table.setItem(r, 0, QTableWidgetItem(""))
        self._table.setItem(r, 1, QTableWidgetItem(""))

    def _remove_row(self):
        r = self._table.currentRow()
        if r >= 0:
            self._table.removeRow(r)
            self._rebuild_method_combo()

    def _sync_enabled(self, on: bool):
        self._version.setEnabled(on)
        self._method.setEnabled(on)

    def _save(self):
        self._commit_table()
        save_map(self._map)
        save_selection(self._version.currentText(),
                       self._method.currentText(),
                       self._enabled.isChecked())
        self.accept()
