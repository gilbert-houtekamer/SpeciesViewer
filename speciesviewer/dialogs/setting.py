"""Help ▸ About: name, version, copyright, licence — and, one click down,
the full third-party credits, rendered from NOTICE.md so the file in the
repository and the screen the user sees cannot drift apart."""
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import (QColor, QDesktopServices, QPixmap, QTextCharFormat,
                         QTextCursor)
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton,
                             QTextBrowser, QVBoxLayout, QWidget)

from utils.version import APP_NAME, __version__

_HERE = Path(__file__).resolve().parent
_PICTURE = _HERE.parent / "assets" / "about_duck.jpg"
# Source tree: the repo root. Packaged build: _internal/, beside assets/.
_NOTICE = next((p for p in (_HERE.parent.parent / "NOTICE.md",
                            _HERE.parent / "NOTICE.md") if p.exists()),
               _HERE.parent.parent / "NOTICE.md")

COPYRIGHT = "Copyright © 2024–2026 Gilbert Houtekamer"
GPL_URL = "https://www.gnu.org/licenses/gpl-3.0.html"

# A pastel sand: warm next to the duck's orange, calm next to the grey water.
# In dark mode the same sand, at night: a deep warm brown with light text.
_LIGHT = dict(bg="#F4EDE1", ink="#2E2A24", muted="#6B6257", link="#1F5FA8",
              button="#FFFFFF", hover="#FBF7F0", border="#CDBFA8")
_DARK = dict(bg="#2E2924", ink="#EEE6D8", muted="#B3A795", link="#8AB4F8",
             button="#3B352E", hover="#463F37", border="#5C5248")
_SIDE = 460          # the picture is square; the text panel matches its height


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setFixedSize(2 * _SIDE, _SIDE)

        from gui import theme
        c = _DARK if theme.is_dark() else _LIGHT
        panel = QWidget(objectName="aboutPanel")
        panel.setStyleSheet(
            f"#aboutPanel {{ background: {c['bg']}; }}"
            f"#aboutPanel QLabel {{ color: {c['ink']}; background: transparent; }}"
            f"#aboutPanel QPushButton {{ color: {c['ink']}; background: {c['button']};"
            f"  border: 1px solid {c['border']}; border-radius: 4px; padding: 6px 14px; }}"
            f"#aboutPanel QPushButton:hover {{ background: {c['hover']}; }}")
        text = QVBoxLayout(panel)
        text.setContentsMargins(36, 34, 36, 26)
        text.setSpacing(0)

        def label(html, size, gap=0, muted=False, bold=False):
            lbl = QLabel(html)
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setOpenExternalLinks(True)
            weight = "font-weight:600;" if bold else ""
            colour = f"color:{c['muted']};" if muted else ""
            lbl.setStyleSheet(f"font-size:{size}pt; {weight} {colour}")
            text.addWidget(lbl)
            if gap:
                text.addSpacing(gap)

        label(APP_NAME, 24, 2, bold=True)
        label(f"Version {__version__}", 11, 14, muted=True)
        label("Organize species.<br>Explore collections.<br>"
              "Bring biodiversity to life.", 12, 22)
        label(COPYRIGHT, 11, 8)
        label("Free software under the "
              f"<a href='{GPL_URL}' style='color:{c['link']}'>GNU General Public License, version 3</a>. "
              "You may use, share and change it. "
              "It comes with no warranty.", 10, 8, muted=True)
        label("Pictures and icon from the author's own photographs.",
              10, 0, muted=True)
        text.addStretch(1)

        buttons = QHBoxLayout()
        credits = QPushButton("Licences and credits…")
        credits.clicked.connect(lambda: CreditsDialog(self).exec())
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        buttons.addWidget(credits)
        buttons.addStretch(1)
        buttons.addWidget(close)
        text.addLayout(buttons)

        picture = QLabel()
        pix = QPixmap(str(_PICTURE))
        if not pix.isNull():
            dpr = self.devicePixelRatioF()
            pix = pix.scaled(int(_SIDE * dpr), int(_SIDE * dpr),
                             Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
            pix.setDevicePixelRatio(dpr)
        picture.setPixmap(pix)
        picture.setFixedSize(_SIDE, _SIDE)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(panel, 1)
        row.addWidget(picture)


def _recolour_links(view: QTextBrowser, colour: QColor) -> None:
    """Give every link in the document `colour` (Markdown import bakes in
    the palette's dark-blue link colour, which the app's QSS can't reach)."""
    cursor = QTextCursor(view.document())
    block = view.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.charFormat().isAnchor():
                fmt = QTextCharFormat()
                fmt.setForeground(colour)
                cursor.setPosition(frag.position())
                cursor.setPosition(frag.position() + frag.length(),
                                   QTextCursor.MoveMode.KeepAnchor)
                cursor.mergeCharFormat(fmt)
            it += 1
        block = block.next()


class CreditsDialog(QDialog):
    """NOTICE.md, as is: every library, its licence, and the online
    services and data SpeciesViewer relies on."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Licences and credits")
        self.resize(820, 640)
        view = QTextBrowser()
        view.setOpenLinks(False)                   # web links go to the browser
        view.anchorClicked.connect(lambda url: self._open_link(view, url))
        try:
            view.setMarkdown(_NOTICE.read_text(encoding="utf-8"))
            from gui import theme
            if theme.is_dark():                    # default link blue vanishes on dark
                _recolour_links(view, QColor("#8AB4F8"))
        except OSError:
            view.setPlainText(
                "The credits file (NOTICE.md) is missing from this installation.\n"
                "It is published with the source code of SpeciesViewer.")
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(view)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close)
        layout.addLayout(bottom)

    @staticmethod
    def _open_link(view: QTextBrowser, url: QUrl):
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
        elif url.hasFragment():
            # "#native-libraries-inside-…" → the heading with those words.
            # Qt's Markdown import gives headings no anchors, so find the text.
            heading = url.fragment().replace("-", " ")
            cursor = view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            view.setTextCursor(cursor)
            while view.find(heading):
                if view.textCursor().block().blockFormat().headingLevel():
                    break
            view.ensureCursorVisible()
