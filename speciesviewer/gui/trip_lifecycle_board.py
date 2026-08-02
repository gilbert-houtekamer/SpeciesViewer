# trip_lifecycle_board.py — a VISUAL companion to the To Master menu.
#
# Lays the trip → master → album journey out spatially: each action is a card
# whose state (Done / Enabled / Locked) and click come straight from the SAME
# MenuSpec the menu uses, so the board can never disagree with the menu's gating.
# Green gate banners mark the phase boundaries (no edits possible / no more
# changes / changes now happen in the album).
#
# The board is a VIEW. build_menu_spec (gui/menuspec.py) assembles the model —
# which specs sit in which phase, cheap milestone predicates for the Done state,
# and which pipeline steps get a per-day progress bar — and passes it here.
#
# Design reference: docs/design-docs/trip_lifecycle_board.html
#
# Model shape (list of phase dicts):
#   { "title", "sub", "gate": (text, predicate) | None,
#     "groups": [ { "label", "blocks": [ block, … ] } ] }
# block:
#   { "title", "desc",
#     "spec":   MenuSpec | None,          # gives is_enabled() + callback()
#     "on_click": callable | None,        # used when there is no spec
#     "enabled":  callable | None,        # used when there is no spec
#     "done":     callable() -> bool | None,   # milestone → Done state
#     "prog":     "read" | "blog" | "publish" | None,   # per-day progress bar
#     "warn":     bool }                  # destructive → amber accent

import logging
import os
import re

from PyQt6.QtWidgets import (
    QDialog, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QFrame, QScrollArea,
    QProgressBar,
)
from PyQt6.QtCore import Qt, pyqtSignal

from utils.config import _config
from gui import theme

logger = logging.getLogger("speciesviewer")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# palette — mirrors docs/design-docs/trip_lifecycle_board.html (light).
# Sourced from gui/theme.py (ch.15 global QSS, Phase 2), except _RDY_BG:
# this board's "ready" background is genuinely white, not theme.READY_BG's
# pale blue (Biota Browser/Species Panel) — a real, pre-existing divergence
# discovered while migrating, left as-is rather than silently unified.
_DONE_FG = theme.DONE_FG; _DONE_BG = theme.DONE_BG
_RDY_FG  = theme.READY_FG; _RDY_BG  = "#ffffff"
_LCK_FG  = theme.LOCKED_FG; _LCK_BG  = theme.LOCKED_BG; _LCK_BD = theme.LOCKED_BORDER
_GATE    = theme.GATE_FG; _GATE_BG = theme.GATE_BG
_WARN    = theme.WARN
_INK = theme.INK; _INK_SOFT = theme.INK_SOFT; _LINE = theme.LINE


def trip_day_progress():
    """Trip-side per-step day counts for the pipeline progress bars.

    Mirrors the proven day walk in menuspec._scan_trip_publish (reads the TRIP
    repo's per-day statemachine), extended to also count Read & Summarize and
    Write Blog. Returns {"read":(n,tot), "blog":(n,tot), "publish":(n,tot_jpg)}
    or None when there is no trip / no days."""
    try:
        from engine.statemachine import StateMachine
        from utils.trip_identity import resolve_trip_picture_root
        trip_db = _config.get("trip", "tripbiotadatabase", fallback="").replace('"', "").replace("'", "").strip()
        if not trip_db:
            return None
        image_root = resolve_trip_picture_root(trip_db, kind="trip")
        if not image_root or not os.path.isdir(image_root):
            return None
        review_root = os.path.join(trip_db, "metadata", "review")
        total = total_jpg = rs = blog = pub = 0
        for name in sorted(os.listdir(image_root)):
            day_dir = os.path.join(image_root, name)
            if not _DATE_RE.match(name) or not os.path.isdir(day_dir):
                continue
            total += 1
            try:
                has_jpg = any(f.lower().endswith(".jpg") for f in os.listdir(day_dir))
            except OSError:
                has_jpg = False
            if has_jpg:
                total_jpg += 1
            try:
                ts = StateMachine(trip_db, os.path.join(review_root, name), readState=True).Taskstate
            except Exception:
                ts = {}
            if ts.get("WritePictures", [False])[0]:
                rs += 1
            if ts.get("WriteBlog", [False])[0]:
                blog += 1
            if ts.get("WriteCommit", [False])[0]:
                pub += 1
        if total == 0:
            return None
        return {"read": (rs, total), "blog": (blog, total), "publish": (pub, total_jpg or total)}
    except Exception as e:
        logger.info(f"lifecycle board: day progress skipped: {e}")
        return None


class BlockCard(QFrame):
    """One action card. Emits `clicked` on a left click (any state — the board
    decides what a click means from the state it computed)."""
    clicked = pyqtSignal()

    def __init__(self, block, state, prog):
        super().__init__()
        self.setObjectName("card")
        self._state = state
        warn = block.get("warn", False)
        accent = _WARN if warn else (
            _DONE_FG if state == "done" else _RDY_FG if state == "enabled" else _LCK_BD)
        bg = _DONE_BG if state == "done" else _RDY_BG if state == "enabled" else _LCK_BG
        border = accent if state == "enabled" else _LINE
        lw = 7 if state == "enabled" else 5
        self.setStyleSheet(
            f"QFrame#card {{ background:{bg}; border:1px solid {border};"
            f" border-left:{lw}px solid {accent}; border-radius:9px; }}"
            f"QFrame#card:hover {{ border-color:{accent}; }}")
        if state != "locked":
            self.setCursor(Qt.CursorShape.PointingHandCursor)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(3)

        top = QHBoxLayout(); top.setSpacing(8)
        title = QLabel(("✓ " if state == "done" else "") + block["title"])
        title.setStyleSheet(
            f"color:{_INK if state != 'locked' else _INK_SOFT}; font-weight:600; font-size:13px; border:none;")
        title.setWordWrap(True)
        top.addWidget(title, 1)
        chip = QLabel({"done": "DONE", "enabled": "ENABLED", "locked": "LOCKED"}[state])
        chip_fg = _DONE_FG if state == "done" else (_WARN if (warn and state == "enabled") else _RDY_FG if state == "enabled" else _LCK_FG)
        chip.setStyleSheet(
            f"color:{chip_fg}; font-family:Consolas,monospace; font-size:9px; font-weight:700;"
            f" letter-spacing:1px; border:none;")
        top.addWidget(chip, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)

        desc = QLabel(block.get("desc", ""))
        desc.setStyleSheet(f"color:{_INK_SOFT}; font-size:11px; border:none;")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        pkey = block.get("prog")
        if pkey and prog and pkey in prog:
            n, tot = prog[pkey]
            bar = QProgressBar()
            bar.setRange(0, max(tot, 1)); bar.setValue(n)
            bar.setTextVisible(False); bar.setFixedHeight(6)
            fill = _DONE_FG if (tot and n >= tot) else _RDY_FG
            bar.setStyleSheet(
                f"QProgressBar {{ background:{_LCK_BG}; border:none; border-radius:3px; }}"
                f"QProgressBar::chunk {{ background:{fill}; border-radius:3px; }}")
            lay.addSpacing(4)
            lay.addWidget(bar)
            cap = QLabel(f"{n} / {tot} days")
            cap.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace; font-size:10px; border:none;")
            lay.addWidget(cap)

    def mouseReleaseEvent(self, event):
        # Emit LAST: handling the click rebuilds the board, which deletes this
        # card — so nothing may touch `self` after the emit (super() first).
        fire = (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event.pos()))
        super().mouseReleaseEvent(event)
        if fire:
            self.clicked.emit()


class TripLifecycleBoard(QDialog):
    """The lifecycle board dialog. Independent of the yellow panel; fires the
    real actions via each block's MenuSpec, then refreshes itself."""

    def __init__(self, main_window, model):
        super().__init__(main_window)
        self.main_window = main_window
        self.model = model
        self.setWindowTitle("Trip to Master — lifecycle board")
        self.resize(1080, 640)
        self.setStyleSheet(f"QDialog {{ background:#f5f7f3; }}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(10)

        head = QLabel("Trip lifecycle")
        head.setStyleSheet(f"color:{_INK}; font-size:18px; font-weight:700; border:none;")
        outer.addWidget(head)
        self._sub = QLabel("")
        self._sub.setStyleSheet(f"color:{_INK_SOFT}; font-size:12px; border:none;")
        self._sub.setWordWrap(True)
        outer.addWidget(self._sub)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(self._scroll, 1)

        bar = QHBoxLayout(); bar.addStretch(1)
        from PyQt6.QtWidgets import QPushButton
        refresh = QPushButton("Refresh"); refresh.clicked.connect(self._rebuild)
        close = QPushButton("Close"); close.clicked.connect(self.close)
        bar.addWidget(refresh); bar.addWidget(close)
        outer.addLayout(bar)

        self._rebuild()

    # ── state ────────────────────────────────────────────────────────────
    def _state(self, b):
        try:
            if b.get("done") and b["done"]():
                return "done"
        except Exception:
            pass
        spec = b.get("spec")
        try:
            if spec is not None and spec.is_enabled():
                return "enabled"
            if spec is None and b.get("enabled") and b["enabled"]():
                return "enabled"
        except Exception:
            pass
        return "locked"

    def _fire(self, b, state):
        if state == "locked":
            sb = getattr(self.main_window, "status_bar", None)
            if sb:
                sb.showMessage(f"“{b['title']}” isn't available at this point.")
            return
        cb = (b["spec"].callback if b.get("spec") else b.get("on_click"))
        if cb is None:
            return
        try:
            cb()
        except Exception as e:
            logger.warning(f"lifecycle board: '{b['title']}' failed: {e}")
        # The action may have changed trip state and the menu's gating; refresh
        # both so the board and the menu stay in step. Defer the board rebuild to
        # the next event-loop tick: rebuilding here would delete the card that is
        # still unwinding this very click ("wrapped C/C++ object … deleted").
        try:
            self.main_window.rebuild_menu()
        except Exception:
            pass
        # Navigation cards (e.g. the pipeline steps → Active Trip panel) close the
        # board so the yellow panel they raised is in front. The rest refresh in
        # place — deferred, so the just-clicked card isn't deleted mid-click.
        if b.get("close_after"):
            self.close()
            return
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, self._rebuild)

    # ── render ───────────────────────────────────────────────────────────
    def _rebuild(self):
        trip = _config.get("trip", "tripbiotadatabase", fallback="").replace('"', "").replace("'", "").strip()
        name = os.path.basename(trip) if trip else "(no active trip)"
        self._sub.setText(f"Active trip: {name} — click an enabled card to run that step.")
        prog = trip_day_progress()

        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        for pi, phase in enumerate(self.model):
            col = QWidget()
            cl = QVBoxLayout(col)
            cl.setContentsMargins(16, 0, 16, 0)
            cl.setSpacing(0)
            if pi > 0:
                col.setStyleSheet("QWidget { border-left:1px dashed %s; }" % _LINE)

            ph = QLabel(phase["title"])
            ph.setStyleSheet(f"color:{_INK}; font-size:13px; font-weight:700; border:none;")
            cl.addWidget(ph)
            sub = QLabel(phase.get("sub", ""))
            sub.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace; font-size:9px; letter-spacing:1px; border:none;")
            cl.addWidget(sub)
            cl.addSpacing(8)

            for g in phase["groups"]:
                gl = QLabel(g["label"].upper())
                gl.setStyleSheet(f"color:{_INK_SOFT}; font-family:Consolas,monospace; font-size:9px; letter-spacing:1px; border:none;")
                cl.addWidget(gl)
                cl.addSpacing(4)
                for b in g["blocks"]:
                    st = self._state(b)
                    card = BlockCard(b, st, prog)
                    card.clicked.connect(lambda _b=b, _s=st: self._fire(_b, _s))
                    cl.addWidget(card)
                    cl.addSpacing(8)

            cl.addStretch(1)

            gate = phase.get("gate")
            if gate:
                text, pred = gate
                try:
                    on = bool(pred())
                except Exception:
                    on = False
                gb = QLabel(("▣ " if on else "▢ ") + text)
                if on:
                    gb.setStyleSheet(
                        f"color:{_GATE}; background:{_GATE_BG}; border:1px dashed {_GATE};"
                        f" border-radius:7px; padding:8px 10px; font-size:12px; font-weight:600;")
                else:
                    gb.setStyleSheet(
                        f"color:{_LCK_FG}; border:1px dashed transparent; padding:8px 10px;"
                        f" font-size:12px; font-weight:600;")
                gb.setWordWrap(True)
                cl.addWidget(gb)

            row.addWidget(col, 1)

        self._scroll.setWidget(host)
