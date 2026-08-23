# One canonical RAW-extension table

Plan for backlog ch.21's `BUG 31 separate literal sets of RAW extensions`. Written
2026-08-23 on branch `SpeciesViewer03`. **Phase 0 (measure) is done; nothing has been
changed yet.**

The chapter says one canonical table "is a small change and a precondition for testing
any new brand honestly". Measuring first changed my view of the second half of that
sentence: the drift is not only a tidiness problem, it is **already producing wrong
behaviour for Canon**, on the current branch, today.

---

## Phase 0 — what is actually there (measured, not estimated)

Scanned every `.py` in the tree with `ast` (venvs excluded), looking for list/set/tuple
literals of string constants that contain at least two RAW extensions.

**39 literal RAW-extension sets, in 25 files, with 20 distinct values.**

The union of everything ever listed is 12 extensions:

```
arw cr2 cr3 dng nef nrw orf pef raf raw rw2 srw
```

Grouped by exact value, most common first. `+` marks non-RAW members of the same
literal.

| n | value | where |
|---:|---|---|
| **10** | `arw cr2 cr3 dng nef orf raf rw2` | `filepicture:204` `topics_scan:30` `preview_provider:1336` `preview_provider:1408` `preview_provider:1497` `rating_provider:36` `sidecar_provider:41` `trip_process:757` `trip_process:2385` `svr_purge_phantom_pictures:49` |
| **3** | `arw cr2 cr3 dng nef orf raf rw2` `+heic` | `filepicture:129` `filepicture:165` `filepicture:203` |
| **3** | `arw cr2 nef orf rw2` | `preview_provider:40` `rating_editor:3343` `rating_provider:46` |
| **3** | `nef nrw orf` `+heic` | `svu5SyncSidecars:23` `projectlib:643` `projectlib:645` |
| **2** | `arw cr2 cr3 dng nef orf pef raf rw2` | `trip_thumbs:23` `biota_source:35` |
| **2** | `arw cr2 nef orf rw2` `+heic` | `image_viewer:619` `species_editor:47` |
| **2** | `arw cr2 cr3 dng nef orf raf rw2` `+heic,jpeg,jpg` | `sv2ReadDirExif:75` `trip_overview:180` |
| **2** | `nef nrw orf` `+heic,jpeg,jpg` | `projectlib:639` `projectlib:641` |
| 1 | `arw cr2 cr3 dng nef nrw orf raf rw2` | `sv0ReadSDCard:89` |
| 1 | `arw cr2 cr3 dng nef orf raf rw2` `+jpeg,jpg` | `sv2ReadDirExif:280` |
| 1 | `arw cr2 cr3 dng nef orf pef raf rw2` `+heic` | `admin_rebuild:324` |
| 1 | `arw cr2 cr3 dng nef orf pef raf rw2 srw` `+heic` | `master_no_display:23` |
| 1 | `arw cr2 cr3 dng nef orf pef raf rw2` `+heic,jpeg,jpg,png,tif,tiff` | `admin_rebuild:26` |
| 1 | `arw cr2 cr3 nef orf raf rw2` `+heic` | `exifcache:70` |
| 1 | `arw cr2 cr3 nef orf raf rw2` | `svr_thumbnail_damage_scan:40` |
| 1 | `arw cr2 cr3 dng nef orf raf raw rw2` `+heic,heif,jpeg,jpg,png,tif,tiff` | `trip_delete_scan:30` |
| 1 | `arw cr2 cr3 dng nef orf raf rw2` `+heic,heif,jpeg,jpg,png,tif,tiff` | `unfiled:48` |
| 1 | `arw cr2 cr3 dng nef orf raf rw2` `+heic,heif` | `work_backup:72` |
| 1 | `arw cr2 cr3 dng nef orf raf rw2` `+heic,heif,jpeg,jpg,png` | `trip_process:2451` |
| 1 | `arw cr2 dng nef orf rw2` `+heic,heif,jpeg,jpg,png,tif,tiff` | `map_view:78` |

Style drifts too, independently of content: some sets are dotted (`".nef"`), some bare
(`"nef"`), some upper-case, some `set`, some `tuple`, some `list`. Any canonical helper
has to take a `Path` or a bare string and not care.

---

## What the measurement found that the chapter did not

### 1. `.cr3` is missing from five live code paths — Canon mirrorless is half-invisible

`arw cr2 nef orf rw2` — five extensions, no `.cr3`, no `.dng`, no `.raf` — is the test in:

- `preview_provider.py:40` `_RAW_EXTS`
- `rating_provider.py:46` `_RAW_EXTS`
- `rating_editor.py:3343` `raw_exts_set`
- `image_viewer.py:619` `raw_exts` (`+heic`)
- `species_editor.py:47` `raw_exts` (`+heic`)

Every Canon mirrorless body since 2018 shoots **CR3**, not CR2. In `preview_provider`
that set gates the embedded-preview route at lines 534, 940, 949, 1054, 1228 and 1287.

**The clinching detail: `preview_provider.py` defines both lists.** `_RAW_EXTS` (5, no
`.cr3`) at line 40, and `dxo_raw_exts` (8, with `.cr3`) at 1336 and 1408. Inside one
module a CR3 is a RAW for the PhotoLab path and not a RAW for the preview path.
`rating_provider.py` has exactly the same split. That is not a considered difference —
it is one list that grew and one that did not.

This compounds the R5 Mark II HEVC-preview bug in ch.21: for a Canon owner, one bug stops
the preview being *found* and the other stops it being *decoded*.

### 2. `.nrw` is in 1 of the 39 sets

`sv0ReadSDCard:89`, the one fixed on 2026-08-23, plus `svu5SyncSidecars` and
`projectlib.findBestItem` which had it all along. The other 36 do not have it. This is
the state the chapter already describes; the count is now exact.

### 3. `.pef`, `.srw`, `.raw` and `.dng` appear essentially at random

`.pef` in 4 sets, `.srw` in 1, `.raw` in 1, and `.dng` is missing from `exifcache:70`
and `svr_thumbnail_damage_scan:40` but present in 30 others. No pattern maps to a
purpose. These look like whatever the author had in mind that day.

---

## Which differences are probably intended

Not everything here is drift, and flattening all 20 into one set would be wrong:

| concept | what it is | why it differs |
|---|---|---|
| **RAW** | camera raw only | the base case |
| **RAW + HEIC** | "an original that carries a rating" | HEIC is not a raw, but it *is* a source file needing a sidecar — see the unresolved HEIC question in ch.7 |
| **DxO-openable RAW** | RAW minus HEIC | PhotoLab cannot open HEIC. `filepicture:203/204` states this explicitly: `raw_exts` and `dxo_raw_exts` on adjacent lines, differing by exactly `heic`. **This one is real and must survive.** |
| **PICTURE / viewable** | RAW + JPEG + HEIC + PNG + TIFF | "anything we can show" — `unfiled`, `map_view`, `trip_delete_scan`, `trip_process:2451`, `admin_rebuild:26` |

Three that are *not* one of the four and should be left alone for now:

- `sv2ReadDirExif:280` `WARN_EXTENSIONS` — drives a warning, not a classification.
- `exifcache:70` `IMMUTABLE_EXTENSIONS` — "EXIF that never changes"; a different question
  that merely happens to correlate with RAW-ness.
- `projectlib:639-645` `findBestItem()` — 3-element ordered preferences (JPG before NEF),
  a ranking, not a membership test.

`svu5SyncSidecars:23` `RAW_EXTENSIONS = {'.orf','.nef','.nrw','.heic'}` is the odd one:
it reads like "the bodies Gilbert owns" rather than a general list. **Needs a decision
rather than a guess** — see open questions.

---

## Proposed: `speciesviewer/utils/raw_extensions.py`

One module, no dependencies, importable from `batch/`, `engine/`, `gui/` and `tools/`
alike. Dotted and lower-case is the internal form; the helpers normalise so callers can
stop caring.

```python
RAW          = frozenset({".arw", ".cr2", ".cr3", ".dng", ".nef", ".nrw",
                          ".orf", ".ori", ".pef", ".raf", ".rw2", ".srw"})
RAW_OR_HEIC  = RAW | {".heic", ".heif"}     # an "original" that carries a rating
DXO_RAW      = RAW - {".nrw"}               # what PhotoLab will open
PICTURE      = RAW_OR_HEIC | {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

def is_raw(p) -> bool: ...          # accepts Path or str, with or without a dot
def is_picture(p) -> bool: ...
def ext_of(p) -> str: ...           # normalised ".nef"
```

Two additions to the union of what exists today, both deliberate:

- **`.ori`** — the OM System uncompressed sibling of `.orf`. It is in the new corpus and
  decodes fine, and it appears **nowhere in the tree at all** — not in one of the 39 sets.
  An OM-1 owner shooting `.ori` has files SpeciesViewer does not recognise as pictures,
  the same shape of hole as `.nrw`.
- **`.heif`** alongside `.heic` — already true in `unfiled`, `work_backup`,
  `trip_delete_scan`, `map_view`; just made consistent.

`DXO_RAW` excluding `.nrw` is a **guess and is marked as one in the code** — Coolpix NRW
is old and PhotoLab's support is unverified. It is the one line in this module that
should be checked against reality before being trusted.

---

## Migration, smallest safe step first

**Phase 1 — add the module, change no callers.** New file plus its unit test. Nothing
imports it. Zero behavioural risk; reviewable in one sitting.

**Phase 2 — the 10 pure `DXO_RAW` sites and the 5 broken `_RAW_EXTS` sites.** These are
pure membership tests with no delete or write behind them. **This is the phase that fixes
Canon**, and it is where the value is. 15 sites, mechanical.

**Phase 3 — the `RAW_OR_HEIC` sites.** `filepicture` (3), `admin_rebuild:324`,
`master_no_display`, `work_backup`. Slightly wider blast radius; still no deletes.

**Phase 4 — the PICTURE sites. ⚠ HANDLE SEPARATELY.** `trip_delete_scan:30` and
`work_backup:72` decide **what gets deleted and what gets backed up**. Widening those
sets changes which files a delete scan considers — that is exactly the class of change
that caused the ghost-row and `.nrw` incidents. This phase should be its own review, its
own dry-run against a real trip, and its own commit. It is not mechanical and must not be
bundled with phases 2-3.

**Phase 5 — a guardrail, not a cleanup.** Keep the scanner as a test: fail if any module
outside `utils/raw_extensions.py` defines a literal collection containing two or more
RAW extensions. That is what stops set number 40 from being written next month. Without
it this whole exercise decays back to where it started.

---

## Decisions — answered by Gilbert 2026-08-23

1. **HEIC is its own named set, not part of RAW.** PhotoLab **9** does accept HEIC as
   input, so the old "DxO cannot open HEIC" reasoning is out of date — but it treats it
   *essentially as a JPG*, not as a raw. Whether HEIC is a raw for SpeciesViewer's
   purposes is still open (ch.7, the sidecar question). Since there will be more HEIC over
   time, it gets its own constant so that question can be answered in one place later
   instead of being re-litigated in twenty literals.
2. **`.cr3` should have been in all of them.** Confirmed a bug, not a difference.
3. **`.dng` stays in `RAW`.** DNG is a vendor-independent RAW format, and DxO **PureRAW**
   emits enhanced DNGs — which he used for a while before concluding that PhotoLab's JPG
   from a RAW and from a DNG come out very similar, DNG being very large. That is a
   *retention* decision, and it already lives elsewhere as the master policy
   "keep `.dop`, drop `.dng`". **Classification and retention are different layers:**
   a `.dng` is a raw we recognise, and separately a file the master chooses not to keep.
4. **`.pef` and `.srw` are dropped.** Nobody here shoots Pentax or Samsung, and claiming
   support we have never tested is the dishonesty the ch.21 checklist is meant to prevent.
   After the rewrite they are a one-line addition to one file.
5. **`svu5SyncSidecars.RAW_EXTENSIONS` becomes the canonical set** — "can be any RAW,
   EXIF handles the details on crop etc." Its `.heic` member is kept, so it becomes
   `RAW | HEIC`; that widens what gets a sidecar synced and narrows nothing.

### Resulting table

```python
RAW   = {".arw", ".cr2", ".cr3", ".dng", ".nef", ".nrw", ".orf", ".ori", ".raf", ".rw2"}
HEIC  = {".heic", ".heif"}          # accepted, but NOT a raw -- see ch.7
JPEG  = {".jpg", ".jpeg"}
```

with `RAW_OR_HEIC`, `PICTURE` and `DXO_RAW` derived from those.

### The one line still unverified

`DXO_RAW = RAW - {".nrw"}`. No sample of a Coolpix NRW was in the corpus, and today's
`dxo_raw_exts` does not list `.nrw` either — so excluding it preserves current behaviour
exactly rather than guessing new behaviour. It is marked as unverified in the code. There
are real `.nrw` files in the master from the 2026-08-23 card; dragging one onto PhotoLab
settles it in ten seconds.

---

## Progress

**Phase 1 — DONE.** `speciesviewer/utils/raw_extensions.py` (the table + `is_raw` /
`is_picture` / `ext_of` helpers, all accepting a Path or a bare string in any case) and
`tools/svr_extension_audit.py` (the Phase-5 guardrail, brought forward because it was
already written and it is read-only). The audit exits 1 while any module outside the
table hard-codes RAW extensions, and carries an `EXEMPT` list where each entry must
state its reason.

**Phase 2 split into 2a and 2b.** The plan called Phase 2 "15 sites, mechanical". That
bundles the actual bug with mere tidying, and they deserve separate tests.

**Phase 2a — DONE, awaiting a live run.** The five sets missing `.cr3`:
`preview_provider:40`, `rating_provider:46`, `rating_editor:3343` → `RAW`;
`image_viewer:619`, `species_editor:47` → `RAW_OR_HEIC`. Each local name is kept, so no
usage site changed — 10 insertions, 5 deletions. All five modules import cleanly with Qt
loaded and now recognise `.cr3`. Audit offenders: 33 → 28.

**Phase 2b — DONE, awaiting a live run.** Nine sites: `sidecar_provider:41`,
`topics_scan:30`, `filepicture:204`, `preview_provider` x3, `rating_provider:36`,
`trip_process` x2. Audit offenders 28 -> 19.

It was NOT the pure tidying the plan called it. **Phase 2a alone was not enough to make
a `.nrw` visible**, and the live test proved it: `image_viewer.discover_images` (2a) now
yields the 35 NRW paths for 2016-07-23, but `SidecarCollection` then builds a group with
neither `raw` nor `jpg` set, because `sidecar_provider._RAW_EXTS` did not know the
extension. The picture existed and had no displayable file. Two gates in series, one
fixed, still broken — which is the argument for the whole exercise in miniature.

A trap found on the way: **these sets are not all dotted.** `filepicture:204`,
`topics_scan:30`, `preview_provider` x3 and `rating_provider:36` are BARE ("nef"),
because they test the CSV's `WindowsExtension` column; the rest test `Path.suffix` and
are dotted. Substituting the dotted table directly would have silently matched nothing.
`utils.raw_extensions.bare()` converts, so the fact is stated once instead of a second
hand-maintained table.

Verified end to end against `2016-07-23` (299 NEF + 35 NRW + 66 JPG):
400 paths -> 377 pictures -> **0 groups without a displayable file**, 35 NRW typed as raw.

**Deliberately held back from Phase 2**: `tools/svr_purge_phantom_pictures.py:49` and
`tools/svr_thumbnail_damage_scan.py:40`. A tool with *purge* in its name gets the same
care as Phase 4 regardless of how simple the diff looks.
