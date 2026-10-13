# Licensing background — the developer's version of NOTICE.md

*Kept for whoever maintains SpeciesViewer. The public page is [`NOTICE.md`](../NOTICE.md),
which the program shows under Help ▸ About ▸ Licences and credits; it states the facts.
This file keeps the reasoning behind them: why the licence is GPL-3.0, what was checked in
each wheel, and why the build is shaped the way it is. When a dependency or the build
changes, update both. Split off 13 October 2026.*


SpeciesViewer is distributed under the **GNU General Public License, version 3**
(see [`LICENSE`](../LICENSE)). This file lists the third-party software it builds on,
the licences that software carries, and the terms attached to the online services and
data it uses.

It covers the code as published in this repository. A packaged binary build additionally
ships native libraries that arrive inside the Python wheels; those are listed separately
under [Native libraries](#native-libraries-inside-the-python-wheels), with the two build
rules that follow from their licences.

---

## Why SpeciesViewer is GPL-3.0

The choice is made by a dependency rather than a preference. **PyQt6** — the Qt binding the
entire user interface is written against — is offered by Riverbank Computing under either the
GPL v3 or a paid commercial licence; its wheel declares `GPL-3.0-only`. Distributing
SpeciesViewer under any other licence would require the commercial option.

GPL-3.0 also expresses what is intended here: anybody may use, study, modify and share
SpeciesViewer, including for their own photography business, and nobody may distribute a
closed-source version of it.

---

## Python packages

Direct runtime dependencies, with the version this list was verified against
(Python 3.12, Windows 11, 21 August 2026).

| Package | Version | Licence | Used for |
|---|---|---|---|
| PyQt6 | 6.11.0 | **GPL-3.0-only** | the whole user interface |
| PyQt6-Qt6 | 6.11.1 | LGPL-3.0 | the Qt libraries themselves |
| PyQt6-sip | 13.11.1 | BSD-2-Clause | PyQt's binding layer |
| Pillow | 12.2.0 | MIT-CMU | image loading, thumbnails, cached previews |
| pillow-heif | 1.3.0 | BSD-3-Clause (wheel: see below) | reading HEIC/HEIF pictures |
| rawpy | 0.27.0 | MIT (wraps LibRaw: see below) | full-resolution RAW decode |
| numpy | 2.4.6 | BSD-3-Clause | pixel arrays behind the RAW decode |
| opencv-python | 4.13.0.92 | Apache-2.0 | image scaling and sharpening |
| PyExifTool | 0.5.6 | GPL-3.0+ / BSD | driving the ExifTool process |
| pygbif | 0.6.6 | MIT | GBIF taxonomy and occurrence lookups |
| requests | 2.34.2 | Apache-2.0 | all outgoing HTTP |
| pysftp | 0.2.9 | BSD-3-Clause | publishing the website over SFTP |
| paramiko | 3.5.1 | LGPL-2.1-or-later | the SSH transport under pysftp |
| deep-translator | 1.11.4 | MIT | Dutch ↔ English blog translation |
| langdetect | 1.0.9 | MIT | deciding which language a text is in |
| fastcrc | 0.3.6 | MIT | translation-cache keys |
| openpyxl | 3.1.5 | MIT | the Excel checklist generator |

Transitive dependencies pulled in by the above are all permissive — MIT, BSD, Apache-2.0,
PSF, MPL-2.0 (`certifi`) — and impose no condition beyond preserving their copyright
notices. The full resolved list with versions can be produced at any time with
`pip list` / `pip show` in the project environment.

Build- and development-only tools (`pyinstaller`, `pytest`, `black`, `ruff`, `bm3d`) are not
distributed as part of SpeciesViewer and are not listed here. PyInstaller's own licence
carries an explicit exception permitting the distribution of non-GPL programs built with it,
so it places no condition on anything either.

---

## Native libraries inside the Python wheels

A packaged build embeds these; a source checkout relies on whatever the wheels installed.

| Library | Licence | Arrives with | Notes |
|---|---|---|---|
| Qt 6 | LGPL-3.0 | PyQt6-Qt6 | dynamically linked; compatible with GPL-3.0 |
| LibRaw (`raw_r.dll`) | LGPL-2.1 **or** CDDL-1.0 | rawpy | see the note below |
| libheif | LGPL-3.0 | pillow-heif | HEIF container handling |
| libde265 | LGPL-3.0 | pillow-heif | HEVC **decoder** — this is what SpeciesViewer uses |
| x265 | **GPL-2.0** | pillow-heif | HEVC **encoder** — see the note below |
| FFmpeg | LGPL-2.1 | opencv-python | video codecs; not used by SpeciesViewer |

### The two build rules — settled, applied in `tools/SpeciesViewer.spec`

*Both are in force since the first rough ZIP (11 October 2026); the build log confirms
"no libx265". The text below is the original reasoning.*

**1. pillow-heif's wheel is GPL-2.0, not BSD.** The package declares BSD-3-Clause, but its
own `LICENSES_bundled.txt` states plainly: *"License for pillow-heif binary wheels: GPLv2,
due to base library licenses."* The cause is `libx265`, the HEVC **encoder** — present in the
installed wheel as `libx265-215-*.dll`. GPL-2.0-only and GPL-3.0 do not combine.

SpeciesViewer never encodes HEIC. It calls `pillow_heif.register_heif_opener()` and decodes,
in exactly two places ([`gui/preview_provider.py`](../speciesviewer/gui/preview_provider.py)).
The clean resolution is therefore to **exclude `libx265` from the packaged build** rather than
to reason about whether x265's GPL-2.0 grant carries an "or later" clause. That removes the
question instead of answering it, and costs nothing, since the excluded library is one
SpeciesViewer has no code path to reach.

**2. LibRaw is LGPL-2.1 / CDDL-1.0.** Neither is a comfortable fit with GPL-3.0 if the library
were *statically* bound into the executable. It is not: rawpy ships `raw_r.dll` as a separate
dynamically-loaded file, and a one-folder PyInstaller build keeps it that way — the user can
replace it with their own LibRaw build, which is exactly what the LGPL asks for. **Do not
produce a one-file build** without revisiting this.

---

## External programs

**ExifTool**, by Phil Harvey — <https://exiftool.org> — is a separate executable that
SpeciesViewer runs as a child process for essentially every metadata read and write.
It is free software under the same terms as Perl itself: the Artistic License or the GNU
General Public License. It is not derived from SpeciesViewer and SpeciesViewer is not derived
from it; they communicate only through the command line.

The Windows download of SpeciesViewer **includes** the official ExifTool package from
exiftool.org, unchanged except for the rename its own instructions ask for
(`exiftool(-k).exe` → `exiftool.exe`), in the `exiftool` folder beside `SpeciesViewer.exe`,
with its own README. The version is pinned in `tools/exiftool_pin.json`.

**DxO PhotoLab** is proprietary software that SpeciesViewer interoperates with by reading and
writing `.dop` sidecar files. No DxO code is included or required to build SpeciesViewer.

---

## Bundled web assets

Published websites carry these files, copied from
[`speciesviewer/dist/includes web/`](../speciesviewer/dist/includes%20web/):

**Leaflet 1.9.4** — © 2010–2023 Vladimir Agafonkin, © 2010–2011 CloudMade —
BSD-2-Clause. <https://leafletjs.com>. Self-hosted deliberately, so published sites do not
depend on a CDN.

The photographs, icons and stylesheets under the same folder are the work of the
SpeciesViewer author unless stated otherwise, and are covered by the repository licence.

---

## Online services and data

These carry *terms of use* rather than software licences, and the obligations belong to
whoever runs SpeciesViewer.

**OpenStreetMap** — map tiles (`tile.openstreetmap.org`) and place-name lookups
(`nominatim.openstreetmap.org`). Map data © OpenStreetMap contributors, available under the
**Open Database License (ODbL)**. Both services require attribution — shown in the map
view — and are subject to usage policies that cap request rates and require a descriptive
`User-Agent` identifying the application and a contact. OpenStreetMap enforces its policy by
blocking User-Agent strings, so it must never be one shared string: since 0.4 each
installation sends `SpeciesViewer/<version> (<contact e-mail from its settings>)`
(`utils/site_config.user_agent()`), falling back to the project address.

**OpenTopoMap** — alternative tiles, © OpenTopoMap contributors, CC-BY-SA 3.0, over the same
OSM data.

**GBIF** — the Global Biodiversity Information Facility (`api.gbif.org`), used for species
names and classification. GBIF-mediated *occurrence* data is published under Creative Commons
licences that vary per dataset (CC0, CC-BY, CC-BY-NC); SpeciesViewer does not publish GBIF
occurrence records today (checked 13 October 2026). If a species page ever shows them, it must
carry the GBIF citation.

**Avibase** (`avibase.bsc-eoc.org`) and the **Netherlands Biodiversity API**
(`api.biodiversitydata.nl`) — consulted for checklists and species names, subject to their
own terms.

**Google Translate**, reached through `deep-translator` — used to translate blog text between
Dutch and English. Subject to Google's terms of service; no key is shipped.

---

## Not covered by this repository's licence

Photographs, their metadata and the websites SpeciesViewer generates from them remain the
property of the photographer. The GPL applies to the program, never to its output.
