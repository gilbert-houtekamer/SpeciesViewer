"""Widgets that behave correctly by default.

ONE job: make the right thing the easy thing, for the cases where Qt's default
is a trap rather than a preference.

So far that is four cases: wrapped labels, a combo list whose type-ahead
lands the match at the top, an eye that shows a hidden field's text, and the
species picker every species viewer shares.
"""

from PyQt6.QtCore import QModelIndex, QObject, QEvent, QTimer, Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import QAbstractItemView, QComboBox, QCompleter, QLabel


def wrapped(text: str, parent=None) -> QLabel:
    """A QLabel that wraps — use this for any explanatory paragraph.

    ⚠ WHY THIS EXISTS. A QLabel without word wrap can only draw its text as one
    line, so when the layout asks it "what is the narrowest you can be?" it has
    to answer with the full width of that line. A window's minimum width is the
    widest answer among its rows, and a layout minimum silently overrules
    `resize()` — so one long paragraph sets the width of the whole dialog and no
    size request can take it back. "Empty a card" asked for 820 px and opened at
    3310, wider than a laptop screen.

    The trap is specifically the INLINE form:

        layout.addWidget(QLabel("a long explanation…"))     # can never be fixed

    because no reference is kept, there is nothing to call `setWordWrap(True)`
    on afterwards. Every one of the seven cases found in this codebase was that
    shape, and in each of them the labels that DID wrap were the ones held in a
    variable. The bug is not carelessness about wrapping; it is that the concise
    way to add a label is the broken one. Hence a helper that is just as short:

        layout.addWidget(wrapped("a long explanation…"))

    Short labels — a field caption, a column heading — do not need this and read
    better without it; wrapping "Card to empty:" would only let it fold in half.
    """
    label = QLabel(text, parent) if parent is not None else QLabel(text)
    label.setWordWrap(True)
    return label


class _TypeAheadToTop(QObject):
    """Event filter on a combo's popup list: after a typed letter, scroll the
    match to the TOP. Lives on the view (parented to it), so it dies with it."""

    def eventFilter(self, view, event):
        if event.type() == QEvent.Type.KeyPress and event.text().strip():
            # Qt's own keyboardSearch runs after this filter returns; scroll once
            # it has moved the current item.
            QTimer.singleShot(0, lambda: view.scrollTo(
                view.currentIndex(), QAbstractItemView.ScrollHint.PositionAtTop))
        return False


def type_ahead_to_top(combo: QComboBox) -> QComboBox:
    """Make typing a letter in the open drop-down put the matching item at the
    TOP of the list, not wherever it first becomes visible.

    Qt scrolls a type-ahead match only just into view: jumping back up the list
    it lands on the top line, but jumping DOWN it lands on the bottom line, so
    the names after it — the ones you are looking for — need scrolling. Arrow
    keys and the mouse wheel are untouched (a letter only; arrows have no text).
    """
    view = combo.view()
    # Keep a Python reference: Qt's parent keeps only the C++ object alive, and
    # once the wrapper is garbage-collected the filter is a bare QObject whose
    # eventFilter override is gone — it silently stops working.
    combo._type_ahead_filter = _TypeAheadToTop(view)
    view.installEventFilter(combo._type_ahead_filter)
    return combo


# ── Species picker ───────────────────────────────────────────────────────────
SPECIES_SEARCH_MIN = 3      # same minimum as the Biota Browser's search


class SpeciesPicker(QObject):
    """One species picker for the Trip, Album and Master Species Viewers.

    ONE job: turn a typed part of ANY of a species' names (English, Dutch,
    scientific) into a pick of one of the combo's entries.

    Invariant: typing never changes the combo's current entry. Only a pick from
    the popup, or Enter on the top match, does — so nothing loads mid-word (the
    old non-editable trip combo jumped to a new species on every letter).

    It stays a QComboBox, so every currentData() caller is unchanged and ▾ still
    browses the full list. Qt's own completer is replaced: it only searches the
    entry's shown text (one language), and fires from the first letter.
    Matches are ranked: a name that STARTS with the text, then a word inside a
    name, then anywhere — so "aals" puts aalscholver above bengaalse stern.
    Each popup row shows all the species' names, so you can see why it matched.
    """

    _ROLE_INDEX = Qt.ItemDataRole.UserRole + 1     # combo row the match stands for

    def __init__(self, combo: QComboBox, placeholder: str):
        super().__init__(combo)
        self._combo = combo
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.setCompleter(None)            # Qt's prefix completer: one language only
        self._edit = combo.lineEdit()
        self._edit.setPlaceholderText(placeholder)
        self._entries: list = []            # (combo row, popup text, [casefolded names])
        self._model = QStandardItemModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion)  # we filter + rank
        self._completer.setWidget(self._edit)  # NOT setCompleter: we decide when it opens
        self._completer.activated[QModelIndex].connect(self._on_activated)
        self._edit.textEdited.connect(self._on_text_edited)
        self._edit.returnPressed.connect(self._on_return)
        self._edit.installEventFilter(self)

    # The combo's entries changed: (re)index their names. `names_for(data)` gives
    # every name of the species behind an entry's data (display name first).
    def set_names(self, names_for) -> None:
        self._entries = []
        for row in range(self._combo.count()):
            data = self._combo.itemData(row)
            if data is None:                # "— Select or type a species —"
                continue
            label = self._combo.itemText(row)
            names = [n for n in (names_for(data) or []) if n]
            extra = [n for n in names if n.casefold() != str(data).casefold()]
            popup = " · ".join([label] + extra)
            self._entries.append((row, popup, [n.casefold() for n in [label] + names]))

    def _matches(self, text: str) -> list:
        q = text.strip().casefold()
        if len(q) < SPECIES_SEARCH_MIN:
            return []
        ranked = []
        for row, popup, names in self._entries:
            if any(n.startswith(q) for n in names):
                rank = 0
            elif any(w.startswith(q) for n in names
                     for w in n.replace("-", " ").split()):
                rank = 1
            elif any(q in n for n in names):
                rank = 2
            else:
                continue
            ranked.append((rank, popup.casefold(), row, popup))
        ranked.sort()
        return [(row, popup) for _r, _p, row, popup in ranked]

    def _on_text_edited(self, text: str) -> None:
        hits = self._matches(text)
        self._model.clear()
        for row, popup in hits:
            it = QStandardItem(popup)
            it.setData(row, self._ROLE_INDEX)
            it.setEditable(False)
            self._model.appendRow(it)
        if not hits:
            self._completer.popup().hide()
            return
        self._completer.setCompletionPrefix("")
        self._completer.complete()          # opens under the field, sized to it
        popup = self._completer.popup()
        popup.setMinimumWidth(max(self._combo.width(),
                                  popup.sizeHintForColumn(0) + 30))

    def _on_activated(self, index) -> None:
        row = index.data(self._ROLE_INDEX) if index is not None else None
        if row is None:
            return
        self._completer.popup().hide()
        self._pick(int(row))

    def _on_return(self) -> None:
        # Enter: the highlighted row if there is one (the line edit may hear the
        # key before the completer does), else the top match.
        cur = self._completer.popup().currentIndex()
        if cur.isValid() and cur.data(self._ROLE_INDEX) is not None:
            self._on_activated(cur)
            return
        hits = self._matches(self._edit.text())
        if hits:
            self._completer.popup().hide()
            self._pick(hits[0][0])
        else:
            self._restore_text()

    def _pick(self, row: int) -> None:
        self._model.clear()                    # no stale highlight for the next Enter
        if row != self._combo.currentIndex():
            self._combo.setCurrentIndex(row)   # → the viewer's currentIndexChanged
        self._restore_text()

    def _restore_text(self) -> None:
        """The field shows the CURRENT entry again — half-typed text never
        lingers looking like a selection."""
        i = self._combo.currentIndex()
        self._edit.setText(self._combo.itemText(i) if i >= 0 else "")

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Type.FocusIn:
            # Typing replaces the shown name instead of appending to it.
            QTimer.singleShot(0, self._edit.selectAll)
        elif t == QEvent.Type.FocusOut and not self._completer.popup().isVisible():
            self._restore_text()
        elif t == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self._completer.popup().hide()
            self._restore_text()
            return True
        return False


def species_picker(combo: QComboBox,
                   placeholder: str = "Type 3 letters of a Dutch, English or "
                                      "scientific name…") -> SpeciesPicker:
    """Make `combo` the shared species picker (see SpeciesPicker); call
    `combo._species_picker.set_names(...)` after every refill. The reference is
    kept on the combo — a bare QObject filter whose wrapper is collected silently
    stops working (same trap as type_ahead_to_top)."""
    combo._species_picker = SpeciesPicker(combo, placeholder)
    return combo._species_picker


def _eye_icon(crossed: bool, color) -> "QIcon":
    """An eye, drawn here (no icon font or emoji needed); `crossed` = struck through."""
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QIcon, QPainter, QPainterPath, QPen, QPixmap
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(color, 5))
    outline = QPainterPath(QPointF(6, 32))
    outline.quadTo(32, 6, 58, 32)
    outline.quadTo(32, 58, 6, 32)
    p.drawPath(outline)
    p.setBrush(color)
    p.drawEllipse(QRectF(23, 23, 18, 18))
    if crossed:
        p.drawLine(QPointF(12, 54), QPointF(52, 10))
    p.end()
    return QIcon(pm)


def password_eye(field):
    """Put an eye at the right end of a hidden (password) QLineEdit: click it to
    see what you typed, click again to hide it — as most apps do."""
    from PyQt6.QtWidgets import QLineEdit
    color = field.palette().color(field.foregroundRole())
    show_icon, hide_icon = _eye_icon(False, color), _eye_icon(True, color)
    action = field.addAction(show_icon, QLineEdit.ActionPosition.TrailingPosition)
    action.setToolTip("Show what you typed")

    def toggle():
        hidden = field.echoMode() == QLineEdit.EchoMode.Password
        field.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        action.setIcon(hide_icon if hidden else show_icon)
        action.setToolTip("Hide it again" if hidden else "Show what you typed")
    action.triggered.connect(toggle)
    return field
