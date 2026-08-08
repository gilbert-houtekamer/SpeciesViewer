---
name: project-perf-optimizations
description: "Performance audit findings (2026-07-04): quick-win list with file+line, effort estimates, and risk ratings"
metadata: 
  node_type: memory
  type: project
  originSessionId: c1165c6a-ea24-4384-99c0-b7c6f34422fd
---

Audited 2026-07-04. Six concrete quick wins found, ranked by ROI.

**Why:** App is smooth on cached runs but slow on first open and large-trip batch edits. Bottlenecks are redundant filesystem stats, linear dict walks without an index, and full widget rebuilds per edit.

**How to apply:** Pick up these items when touching the relevant files; do not mix into unrelated PRs.

---

## Quick wins — ranked by ROI

### 1. processEvents every 10 iterations (5 min, zero risk)
- **File:** `speciesviewer/gui/rating_editor.py` ~line 1488
- **Fix:** `if i % 10 == 0: QApplication.processEvents()` — currently called every iteration in batch rate loop
- **Test:** trigger batch rate update, confirm no freeze

### 2. Cache ImageSize parse in `_compute_rate()` (15 min, zero risk)
- **File:** `speciesviewer/gui/rating_editor.py` ~line 108–121
- **Fix:** parse `re.split(r"[x ]", row["ImageSize"])` once per fname into a local dict; reuse on second walk
- **Test:** edit a rating, confirm Rate value unchanged

### 3. UPN→files reverse index in `FilePicture` (45 min, low risk)
- **File:** `speciesviewer/engine/filepicture.py` ~line 119 + `filecsv.py`
- **Fix:** at files_dict build time, also build `upn_to_files: dict[str, list[str]]`; replace O(n) linear scan in `updateDescription/Label/Headline` with direct lookup
- **Assumption:** files_dict is immutable after load (built once per ReadDirectory, never patched) — index goes stale silently if that changes
- **Test:** edit description/label/color in day and trip scope; confirm XMP writes happen

### 4. UPN→jpg_path index in `RatingProvider` (1 hr, low-medium risk)
- **File:** `speciesviewer/gui/rating_provider.py` ~line 397–415
- **Fix:** after `rebuild()` loads UPN dict, one loop builds `upn_to_jpg: dict[str, Path | None]`; `jpg_path()` becomes a dict lookup
- **Risk note:** current code does live `Path.exists()` per file — catches files that disappear mid-session. Index caches state at load time. Acceptable for this app (files don't vanish during a session).
- **Must rebuild:** index lives inside `rebuild()`, so scope changes (Day→Trip→Trip+Master) naturally rebuild it
- **Test:** open rating editor day + trip scope; batch delete by color; confirm JPG-protected UPNs are still blocked

### 5. Batch sidecar mtime checks (1–2 hr, medium risk — design choice needed)
- **File:** `speciesviewer/engine/sidecar_cache.py` ~line 60
- **Fix:** add `batch_validate(paths)` to SidecarCache; call once before grid render instead of per-card `stat()`
- **Risk note:** per-card check exists to catch live XMP edits from PhotoLab/Lightroom mid-session. Batching means a short window of stale data. User needs to decide if that's acceptable.
- **Test:** open grid, edit XMP externally mid-session, confirm whether card updates (or consciously accept it won't)

### 6. Incremental grid re-sort (2–4 hr, medium risk)
- **File:** `speciesviewer/gui/rating_editor.py` ~line 1654–1683
- **Fix:** track current card order in a list; after edit compute which cards moved; only reposition those in Qt layout instead of full takeAt + re-add loop
- **Risk:** Qt layout manipulation is fiddly — easy off-by-one card positions or double-renders
- **Test:** edit ratings, apply sort, multi-edit batch — verify grid order every time

---

## Other audit findings (lower priority)

- `jpg_path()` calls `Path.exists()` inside filter loops in `rebuild()` — same root cause as #4 above, fixed by the same index
- Species combo in rating editor does full O(n) UPN scan on populate — build a species index at load time
- ExifCache backup rotation runs on every save (overkill for small incremental saves)
- Master DB CSV rebuild does 3 sequential full-file passes — acceptable today but could be one pass
- Splash screen has a fixed 2 s delay regardless of whether master DB warm finished early

---

## Also discussed (not in this list)

Parallel ExifTool + stay_open mode — see separate conversation 2026-07-04. Summary:
- Multiple ExifTool workers in parallel: feasible, low code complexity, best on NVMe SSD
- `stay_open` mode (via `pyexiftool` library): **biggest single win** — eliminates ~0.5–1 s Perl startup per call, composes with parallelism
- Parallel bitmap creation: feasible with QImage-on-thread → QPixmap-on-main-thread pattern; only worth it for RAW full-res decode, not JPEG thumbnails
