"""QuickViewer — a zoomable look at the camera's embedded JPEG, nothing more.

Opened with `z` from the culling grid. It shows only the camera's own embedded
JPEG (the ~3200×2400 the OM-1 writes) — no RAW decode, no metadata panel, no
species editor — so it appears instantly. `←` / `→` step through the pictures,
`Esc` closes. The same culling keys as the grid work here — `1`–`5` colour,
`q w e r t` / `Ctrl+1`–`5` stars, `0` clear, `x` quick-Blue — each writes and
advances, so you can cull while pixel-peeping. On close the caller moves its
cursor to the last picture shown and repaints any card it changed; nothing
else changes.

A command that writes without opening anything — `a` — reports itself in a
top-right badge, in the same style as the top-left interaction hint, which
clears on the next key. See flash().

`a` (quick species), `s` (species dialog), `c` (rating dialog) and the four
copy/paste keys (Ctrl+C/V for a species name, Ctrl+Shift+C/V for GPS + place)
work here too,
but they are the caller's to run — this view owns no species or rating UI, and
building one would be the startup cost it exists to skip. They travel up as
action_requested(what, keys) and the grid runs its OWN handler, parenting any
dialog to this window so it can't open behind it. The keys travel WITH the
request on purpose: `navigated` only fires on close, so the caller's cursor is
stale the whole time this view is open and must not be used to decide what an
action applies to. Like the grid's `s`/`c`, they act on the `[` / `]` range when
one is marked, else the picture on screen — unlike `x` and the digit keys, which
are per-picture write-and-advance keys and always act on just the current one.

A picture with no RAW/embedded JPEG but a video file plays instead (QMediaPlayer
+ QVideoWidget, `Space` toggles play/pause) — the one place in the app a movie
is actually watched rather than shown as a single frame; the grid only ever
shows a static frame grab (see gui/preview_extract.extract_video_frame_bytes),
since decoding video for every visible card would be the exact cost the grid's
viewport virtualization was built to avoid.

Deliberately NOT the Large Image View: that one is powerful but pays a startup
cost this view exists to skip. It reuses `_FullResView` (the zoom/pan viewport)
and `handle_cull_key` (the shared culling key handler) from elsewhere.

Displays (never sets) any exposure suggestion already recorded for the
picture — see gui/exposure_render — so pixel-peeping here shows what
PhotoLab/Lightroom will actually render. No slider here; that stays in the
Large Image View. A picture with no suggestion pays nothing extra.

Also shows the picture's colour label as a border (a stylesheet on the
wrapping frame — no pixel cost) and supports `[` / `]` to mark a range's
start/end, exactly like the grid and the Large Image View: resolved locally
(this view already carries the same ordered key list) and reported up via
selection_changed, so the grid's EXISTING selection highlighting and
bulk-action menu (Set exposure/rating/species for N pictures) just work on
whatever range you marked here — no new UI, reuses the grid's own handler.
The caller seeds `initial_brackets` from its own [ / ] state and adopts
whatever this view reports back, so a range can be opened in the grid and
closed here, or vice versa.

Remembers each picture's own zoom + pan (per key, this session only) and
restores it exactly on return. _FullResView's own default — keep the current
zoom, recentre on the new image — is right for stepping through a burst at a
steady magnification, but it has no memory of where YOU panned to on a given
picture, so leaving a carefully-framed shot and coming back showed a
different, re-centred crop instead. See restore_view in large_image_view.py.
"""

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (QApplication, QDialog, QLabel, QStackedWidget,
                             QVBoxLayout, QWidget)

from gui.image_loader import AsyncImageLoader
from gui.large_image_view import BADGE_CSS, _FullResView, color_to_css
from gui.preview_extract import extract_preview_bytes
from utils.name_profile import all_names_ordered


class QuickViewer(QDialog):
    navigated = pyqtSignal(str)   # key last shown — the caller moves its cursor here
    edited = pyqtSignal(set)      # keys whose rating/colour changed — caller repaints them
    # keys, bracket_left, bracket_right — a [ / ] range was marked, caller
    # adopts both the resulting selection and the brackets themselves, so the
    # grid, Quick Viewer and Large Image View all share one [ / ] administration.
    selection_changed = pyqtSignal(set, object, object)
    # 'a' / 's' / 'c' — what to do, and the keys to do it to. See the module
    # docstring for why the keys are carried rather than looked up by the caller.
    action_requested = pyqtSignal(str, set)

    def __init__(self, parent, collection, keys, index: int,
                 initial_brackets: tuple | None = None,
                 initial_selection: set | None = None):
        super().__init__(parent)
        self._collection = collection
        self._keys = list(keys)
        self._index = max(0, min(index, len(self._keys) - 1)) if self._keys else 0
        self._cur_key = None
        self._cur_ev = 0.0
        self._edited: set[str] = set()
        self._bracket_left, self._bracket_right = initial_brackets if initial_brackets else (None, None)
        # The caller's live selection, which is NOT always bracket-derived —
        # Ctrl+A, a click-drag or a context menu all fill it with no [ / ] set.
        # Inheriting only the brackets meant 'a' fell back to the single picture
        # on screen and silently ignored a range the user could see highlighted
        # in the grid behind this window.
        self._selection: set = set(initial_selection or ())
        self._view_state: dict[str, tuple] = {}   # key -> (scale, hscroll, vscroll)

        self.setWindowFlag(Qt.WindowType.Window, True)
        self.setModal(False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        # A colour-label border needs something to put it on — _FullResView
        # already sets its own background stylesheet, so a plain wrapping
        # frame (rather than restyling the view itself) keeps the two concerns
        # apart.
        self._frame = QWidget()
        self._frame.setObjectName("qvFrame")
        frame_lay = QVBoxLayout(self._frame)
        frame_lay.setContentsMargins(4, 4, 4, 4)

        # A movie has no embedded JPEG to pixel-peep — it plays instead, in the
        # same popup. One stacked page each, so switching between a still and a
        # video key is a page-flip, not a teardown/rebuild.
        self._stack = QStackedWidget()
        self._view = _FullResView(zoomable=True)
        # The scroll area must not eat the arrow keys — they navigate, they don't
        # pan (drag pans). Keep focus on the dialog.
        self._view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stack.addWidget(self._view)
        self._video_widget = QVideoWidget()
        self._video_widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stack.addWidget(self._video_widget)
        frame_lay.addWidget(self._stack)
        lay.addWidget(self._frame)

        # Top-right transient message, in the same badge style as the top-left
        # interaction hint. Parented to the frame rather than to the image
        # view's viewport so it is equally visible over a video page, which is
        # a different widget in the stack — a write you can't see reported is
        # the whole problem this solves.
        self._flash = QLabel(self._frame)
        self._flash.setStyleSheet(BADGE_CSS)
        self._flash.hide()

        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.setVideoOutput(self._video_widget)

        self._loader = AsyncImageLoader(self)

        scr = self.screen() or QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.resize(int(g.width() * 0.85), int(g.height() * 0.85))
            self.move(g.x() + (g.width() - self.width()) // 2,
                      g.y() + (g.height() - self.height()) // 2)

        self._load()

    # ── picture loading ──────────────────────────────────────────────────────
    def _load(self):
        if not self._keys:
            self.setWindowTitle("Quick view")
            self._stack.setCurrentWidget(self._view)
            self._view.set_message("Nothing to show.")
            return
        # Remember exactly how the picture we're LEAVING was framed (zoom +
        # pan) before switching self._cur_key away from it, so returning to it
        # later restores this instead of _FullResView's normal "keep zoom,
        # recentre" default — see _show.
        if self._cur_key is not None and not self._view._pixmap.isNull():
            self._view_state[self._cur_key] = (
                self._view._scale,
                self._view.horizontalScrollBar().value(),
                self._view.verticalScrollBar().value(),
            )
        self._player.stop()   # never keep playing behind whatever loads next
        key = self._keys[self._index]
        self._cur_key = key
        self._refresh_title()
        # Any exposure suggestion already set (large viewer / grid batch) is
        # applied on display, so QuickViewer shows what PhotoLab/Lightroom will
        # actually render rather than the untouched embed. Zero extra cost for
        # the normal case of no suggestion — apply_exposure_to_pixmap
        # short-circuits and returns the pixmap unchanged when ev is 0. Read
        # regardless of still/video so the colour border applies to both.
        try:
            item = self._collection.item_for_key(key)
            self._cur_ev = getattr(item, "exposure", 0.0) or 0.0
            self._apply_frame_color(getattr(item, "color", "") or "")
        except (ValueError, KeyError):
            self._cur_ev = 0.0
            self._apply_frame_color("")

        raw = self._collection.raw_path(key)
        if not raw:
            # No RAW/embedded JPEG to pixel-peep — a movie plays instead of
            # showing a blank card, wherever the engine exposes one.
            video_path_fn = getattr(self._collection, "video_path", None)
            video = video_path_fn(key) if video_path_fn else None
            if video is not None:
                self._show_video(video)
                return
            self._stack.setCurrentWidget(self._view)
            self._view.set_message("No RAW file for this picture.")
            return

        self._stack.setCurrentWidget(self._view)
        self._view.set_message("Reading the camera's JPEG…")
        self._loader.cancel_all()
        raw_s = str(raw)

        def produce():
            data = extract_preview_bytes(Path(raw_s))
            if not data:
                return None
            img = QImage.fromData(data)
            return img if not img.isNull() else None

        self._loader.request(produce, lambda px, k=key: self._show(px, k))

    def _show_video(self, path: Path):
        self._stack.setCurrentWidget(self._video_widget)
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def _show(self, px, key):
        if key != self._cur_key:
            return                       # a later step superseded this one
        if px is None or px.isNull():
            self._view.set_message("This RAW carries no embedded JPEG.")
            return
        from gui.exposure_render import apply_exposure_to_pixmap
        px = apply_exposure_to_pixmap(px, self._cur_ev)
        overlay = f"camera JPEG {px.width()}×{px.height()}"
        if self._cur_ev:
            from gui.exposure_widget import format_ev
            overlay += f"  ·  {format_ev(round(self._cur_ev * 3))} suggested"
        self._view.set_image(px, (0.5, 0.5), overlay=overlay)
        saved = self._view_state.get(key)
        if saved is not None:
            self._view.restore_view(*saved)

    def _apply_frame_color(self, color: str):
        css = color_to_css(color)
        if css:
            self._frame.setStyleSheet(
                f"QWidget#qvFrame {{ border: 4px solid {css}; border-radius: 6px; }}")
        else:
            self._frame.setStyleSheet("")

    def _species_line(self, key: str) -> str:
        """EVERY name the picture has — Dutch, English and scientific — ordered
        by the active name profile, with the sex/age/plumage suffix on the
        primary one. The same all_names_ordered() the Large Image View's name
        block and the species page use, so the three read alike.

        All three rather than just the profile's primary: this view is where a
        bird gets identified against a field guide, and the guide is as likely
        to be in English as in Dutch. A scientific name is capitalised to the
        "Genus species" convention — the resolver returns it lower-case."""
        try:
            item = self._collection.item_for_key(key)
        except (ValueError, KeyError):
            return ""
        triple = getattr(item, "name_triple", None) or {"sci": getattr(item, "sci_name", "")}
        row = {"nl": triple.get("nl", ""), "en": triple.get("en", ""),
               "sci": triple.get("sci", "")}
        lines = [t for t in all_names_ordered(row, getattr(item, "name_profile", "")) if t[0]]
        if not lines:
            # No triple (a culling-stage sidecar knows only what was typed).
            return (getattr(item, "species", "") or "").strip()
        names = [(t[0].upper() + t[1:]) if is_sci else t for t, is_sci in lines]
        return "  ·  ".join(names) + (getattr(item, "qualifier_suffix", "") or "")

    def _refresh_title(self):
        """Title = key · every name · position. The names are here because this
        view has no panel of any kind: without them, naming a picture with 'a'
        or 's' would change nothing you can see, and a write you can't see is a
        write you don't trust."""
        key = self._cur_key
        if not key:
            self.setWindowTitle("Quick view")
            return
        names = self._species_line(key)
        who = f"{key}  —  {names}" if names else key
        self.setWindowTitle(f"Quick view — {who}  ({self._index + 1} of {len(self._keys)})")

    def refresh_current(self):
        """Re-read the picture on screen after the CALLER changed it (a species
        or rating set through action_requested) — title and colour border only.
        Deliberately not a reload: the pixels did not change, and re-extracting
        the embedded JPEG would undo the zoom and pan the user is working at."""
        if not self._cur_key:
            return
        self._refresh_title()
        try:
            item = self._collection.item_for_key(self._cur_key)
            self._apply_frame_color(getattr(item, "color", "") or "")
        except (ValueError, KeyError):
            pass

    def flash(self, text: str):
        """Show a one-line message in the top-right corner until the next key.

        For the commands that write without opening anything — 'a' above all.
        A dialog reports its own result and then goes away, which is itself the
        acknowledgement; 'a' has neither, and silence after a write that
        touched thirty pictures is indistinguishable from a key that did
        nothing. Deliberately not on a timer: it clears when you move on, so it
        is still there if you looked away."""
        text = (text or "").strip()
        if not text:
            self._clear_flash()
            return
        self._flash.setText(text)
        self._flash.adjustSize()
        self._position_flash()
        self._flash.show()
        self._flash.raise_()

    def _clear_flash(self):
        self._flash.hide()

    def _position_flash(self):
        m = 8   # same inset as the hint/overlay badges in _FullResView
        self._flash.move(max(m, self._frame.width() - self._flash.width() - m), m)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self._flash.isHidden():
            self._position_flash()

    def clear_brackets(self):
        """Drop the [ / ] range after an action consumed it, so the next key
        press lands on one fresh picture rather than re-acting on the group —
        the same rule the grid's quick-colour key follows."""
        self._bracket_left = self._bracket_right = None
        self._selection = set()
        self.selection_changed.emit(set(), None, None)

    def _action_keys(self) -> set:
        """What 'a' / 's' / 'c' act on: the marked [ / ] range, else the
        selection inherited from the caller (which may have been made by
        Ctrl+A or by clicking, with no brackets at all), else the picture on
        screen. Mirrors the grid's _cull_keys_target()."""
        keys_in_view = set(self._keys)
        return (self._selection_from_brackets()
                or (self._selection & keys_in_view)
                or ({self._keys[self._index]} if self._keys else set()))

    def _request(self, what: str):
        keys = self._action_keys()
        if keys:
            self.action_requested.emit(what, keys)

    def _go(self, delta: int):
        if not self._keys:
            return
        self._index = max(0, min(self._index + delta, len(self._keys) - 1))
        self._load()

    def _goto_key(self, key: str):
        """handle_cull_key's advance-after-edit → step to `key` and load it."""
        try:
            self._index = self._keys.index(key)
        except ValueError:
            return
        self._load()

    def _mark_edited(self, key: str):
        """handle_cull_key's refresh callback — fires after a successful write."""
        self._edited.add(key)

    # ── range selection ([ / ]) ──────────────────────────────────────────────
    def _selection_from_brackets(self) -> set:
        """Mirrors the grid's and the Large Image View's own copy of this
        exactly (same shape, same self._keys order) — see image_viewer.py /
        large_image_view.py. One marker alone selects just that picture; both
        select the (order-independent) range between them."""
        left, right = self._bracket_left, self._bracket_right
        order = self._keys
        if not left and not right:
            return set()
        if not left or not right:
            anchor = left or right
            return {anchor} if anchor in order else set()
        try:
            a, b = order.index(left), order.index(right)
        except ValueError:
            surviving = next((k for k in (left, right) if k in order), None)
            return {surviving} if surviving else set()
        if a > b:
            a, b = b, a
        return set(order[a:b + 1])

    # ── keys ─────────────────────────────────────────────────────────────────
    def keyPressEvent(self, e):
        # The message reports what the LAST command did, so it must not outlive
        # the next one. Cleared before the key is acted on, so a command that
        # raises a new message still leaves it standing.
        self._clear_flash()
        k = e.key()
        if k == Qt.Key.Key_Escape:
            self.close()
            return
        if k == Qt.Key.Key_Space and self._stack.currentWidget() is self._video_widget:
            if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self._player.pause()
            else:
                self._player.play()
            return
        if k in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self._go(-1)
            return
        if k in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self._go(1)
            return
        if k in (Qt.Key.Key_BracketLeft, Qt.Key.Key_BracketRight) and self._keys:
            if k == Qt.Key.Key_BracketLeft:
                self._bracket_left = self._keys[self._index]
            else:
                self._bracket_right = self._keys[self._index]
            # A fresh [ / ] range replaces whatever selection was inherited from
            # the caller — marking a range here is an explicit "this, not that".
            self._selection = self._selection_from_brackets()
            self.selection_changed.emit(
                set(self._selection), self._bracket_left, self._bracket_right)
            return
        # 'x' — quick-stamp Blue and advance, same as the grid's shortcut and
        # the same gate/advance rule as the digit colour keys below (only
        # advance if the picture could actually carry the colour).
        if k == Qt.Key.Key_X and self._keys:
            key = self._keys[self._index]
            applied = False
            try:
                item = self._collection.item_for_key(key)
                if getattr(item, "can_edit_color", False):
                    item.set_color("Blue")
                    applied = True
            except (ValueError, KeyError):
                applied = False
            if applied:
                self._mark_edited(key)
                self._goto_key(self._keys[min(self._index + 1, len(self._keys) - 1)])
            return
        # Colour / star keys — same handler the grid uses: write then advance.
        from gui.viewer_keys import handle_cull_key
        if self._keys and handle_cull_key(
                e, order=list(self._keys),
                current_key=self._keys[self._index],
                item_for_key=self._collection.item_for_key,
                set_cursor=self._goto_key,
                refresh_key=self._mark_edited,
                open_species=lambda: self._request("species"),
                open_rating=lambda: self._request("rating"),
                quick_species=lambda: self._request("species_quick"),
                clipboard=self._request):
            return
        super().keyPressEvent(e)

    def closeEvent(self, e):
        self._loader.cancel_all()
        self._player.stop()
        if self._edited:
            self.edited.emit(set(self._edited))
        if self._cur_key:
            self.navigated.emit(self._cur_key)
        super().closeEvent(e)
