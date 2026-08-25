#!/usr/bin/env python3
"""Self-test for emptying a card: build a synthetic card + trip, then check that
every verdict comes out right and that the copy does what it claims.

    python tools/sv_card_empty_selftest.py

Runs on temporary folders only — it never touches a real card, a real trip, or
the network. Exits non-zero on the first failed check.

The checks that matter, and why each one is here:
  1. a frame on the card that never reached the laptop must BLOCK
  2. a card SV has never read must BLOCK — absence of evidence is not a verdict
  3. a fully judged card classifies keeper/condemned/executed/presumed correctly,
     and consolidation carries the .xmp with every picture
  4. the clear stamp can be read back off the card
  5. consolidating twice copies nothing the second time
  6. shooting on a cleared card RETIRES its stamp — the danger being a stale
     "safe to format" on a card that has since been filled again
  7. names that match nothing must BLOCK, not read as "all deleted" — the bug
     this file exists for: without the prefix bridge, a real camera card matched
     nothing and reported itself ready to format
"""
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "speciesviewer"))
logging.basicConfig(level=logging.INFO, format="  | %(message)s")
from engine import card_empty as ce

XMP = """<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF
 xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
 <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">
  <xmp:Label>{label}</xmp:Label><xmp:Rating>{rating}</xmp:Rating>
 </rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>"""

base = Path(tempfile.mkdtemp(prefix="cardtest_"))
card, project, keeper = base / "CARD", base / "PROJECT", base / "KEEPER"
day = "2026-05-04"
(project / day).mkdir(parents=True)
(project / day / ".trash").mkdir()
card.mkdir(); keeper.mkdir()

PREFIX = "2026 Test"
# (card name, label, where it is on the laptop, ingested?)
FRAMES = [
    ("P100.ORF", "Green",  "day",     True),   # keeper
    ("P101.ORF", "Red",    "day",     True),   # keeper
    ("P102.ORF", "Blue",   "day",     True),   # condemned, not executed
    ("P103.ORF", "Purple", "trash",   True),   # executed
    ("P104.ORF", "",       "nowhere", True),   # presumed executed
]
roster = {}
for name, label, where, ingested in FRAMES:
    laptop_name = f"{PREFIX} - {name}"
    blob = (name * 500).encode()
    roster[name.lower()] = {"size": len(blob), "mtime_ns": 1, "sha256": "x",
                            "capture_date": day}
    if ingested:
        roster[name.lower()]["last_copied"] = "2026-05-04T20:00:00"
    if where == "day":
        (project / day / laptop_name).write_bytes(blob)
        (project / day / f"{Path(laptop_name).stem}.xmp").write_text(
            XMP.format(label=label, rating=3), encoding="utf-8")
    elif where == "trash":
        d = project / day / ".trash" / Path(laptop_name).stem
        d.mkdir(parents=True)
        (d / laptop_name).write_bytes(blob)

def write_card(extra=None):
    data = {"version": 1, "files": dict(roster), "reads": [
        {"trip": "2026 Test", "when": "2026-05-04T21:00:00"}]}
    if extra:
        data["files"].update(extra)
    (card / ce.CARD_CACHE_NAME).write_text(json.dumps(data), encoding="utf-8")

def show(title, rep):
    print(f"\n=== {title}")
    for line in ce.format_report(rep):
        print("   " + line)
    print(f"   can_empty = {ce.can_empty(rep)}")
    for b in rep["blockers"]:
        print(f"   BLOCK: {b}")

# ── 1. a frame that was never ingested must block ────────────────────────────
write_card(extra={"p999.orf": {"size": 10, "mtime_ns": 1, "sha256": "y"}})
r = ce.classify_card(card, project)
show("1. card holds a frame that never reached the laptop", r)
assert not ce.can_empty(r), "a never-ingested frame MUST block"
assert r["counts"][ce.UNACCOUNTED] == 1

# ── 2. no roster at all = unknown, never clear ───────────────────────────────
(card / ce.CARD_CACHE_NAME).unlink()
r0 = ce.classify_card(card, project)
show("2. card SV has never read", r0)
assert not ce.can_empty(r0), "a card with no roster MUST block"

# ── 3. every frame accounted for -> consolidate ───────────────────────────────
write_card()
r = ce.classify_card(card, project)
show("3. every frame has a verdict", r)
assert ce.can_empty(r), r["blockers"]
assert r["counts"] == {ce.KEEPER: 2, ce.CONDEMNED: 1,
                       ce.EXECUTED: 1, ce.PRESUMED: 1}, r["counts"]

summary = ce.consolidate(r, keeper, "2026 Test", "card 03")
print(f"\n   copied={summary['copied']} skipped={summary['skipped']} "
      f"sidecars={summary['sidecars']} failed={summary['failed']}")
landed = sorted(str(p.relative_to(keeper)).replace("\\", "/")
                for p in keeper.rglob("*") if p.is_file())
print("   on the keeper card:")
for f in landed:
    print(f"     {f}")
assert summary["copied"] == 3, summary          # 2 keepers + 1 condemned
assert summary["sidecars"] == 3, summary        # each carried its .xmp
assert any("condemned/" in f for f in landed), "condemned must be set apart"
assert any(f.endswith(".xmp") for f in landed), "culling work must travel"

# ── 4. stamp, and read the verdict back off the card ─────────────────────────
ok = ce.stamp_cleared(card, "2026 Test", str(keeper), summary, r)
assert ok
print(f"\n   clear_verdict -> {ce.clear_verdict(card)}")
assert "CLEAR" in ce.clear_verdict(card)

# ── 5. re-running is idempotent (nothing copied twice) ───────────────────────
again = ce.consolidate(r, keeper, "2026 Test", "card 03")
print(f"   re-run: copied={again['copied']} skipped={again['skipped']}")
assert again["copied"] == 0 and again["skipped"] == 3, again

# ── 6. shoot on the card again -> the stamp must stop applying ────────────────
write_card(extra={"p200.orf": {"size": 10, "mtime_ns": 1, "sha256": "z"}})
idx = ce.read_card_index(card)
idx["cleared"] = json.loads((card / ce.CARD_CACHE_NAME).read_text())\
    .get("cleared") or {"roster_size": 5, "keepers": 3, "when": "x", "keeper_card": "y"}
(card / ce.CARD_CACHE_NAME).write_text(json.dumps(idx), encoding="utf-8")
print(f"   after new pictures, clear_verdict -> {ce.clear_verdict(card) or '(none — correct)'}")
assert ce.clear_verdict(card) == "", "a re-shot card must lose its clear stamp"

# -- 7. a card matching NOTHING must block, not read as "all deleted" ---------
# THE BUG THIS FILE EXISTS FOR. Card names are un-prefixed, laptop names are
# prefixed, and with no bridge between them every frame fell through to
# "presumed deleted" -- which is precisely the verdict that says the card is
# ready to format. It passed the first real-card test only because that card had
# been built by copying already-prefixed files back onto it.
write_card()
elsewhere = base / "ELSEWHERE"
elsewhere.mkdir()
r7 = ce.classify_card(card, elsewhere)
show("7. classified against the wrong folder", r7)
assert not ce.can_empty(r7), "a card matching nothing MUST block"
assert any("could be found" in b for b in r7["blockers"]), r7["blockers"]

print("\nALL CHECKS PASSED")
shutil.rmtree(base, ignore_errors=True)
