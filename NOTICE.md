# Third-party notices

SpeciesViewer is free software, distributed under the **GNU General Public License,
version 3** (<https://www.gnu.org/licenses/gpl-3.0.html>; the full text comes with the
program as `LICENSE`). It builds on the work of others. This page lists that software, the
licence each part carries, and the online services and data SpeciesViewer uses.

---

## Licence

SpeciesViewer's user interface is built with **PyQt6** by Riverbank Computing, which is
available under the GPL version 3. SpeciesViewer uses the same licence.

Anybody may use, study, change and share SpeciesViewer, also for their own photography
business. Anybody who distributes a changed version must share its source code under the same
licence.

---

## Python packages

Versions as of August 2026.

| Package | Version | Licence | Used for |
|---|---|---|---|
| PyQt6 | 6.11.0 | **GPL-3.0-only** | the whole user interface |
| PyQt6-Qt6 | 6.11.1 | LGPL-3.0 | the Qt libraries themselves |
| PyQt6-sip | 13.11.1 | BSD-2-Clause | PyQt's binding layer |
| Pillow | 12.2.0 | MIT-CMU | image loading, thumbnails, cached previews |
| pillow-heif | 1.3.0 | BSD-3-Clause | reading HEIC/HEIF pictures |
| rawpy | 0.27.0 | MIT (wraps LibRaw, see below) | full-resolution RAW decode |
| numpy | 2.4.6 | BSD-3-Clause | pixel arrays behind the RAW decode |
| opencv-python | 4.13.0.92 | Apache-2.0 | image scaling and sharpening |
| PyExifTool | 0.5.6 | GPL-3.0+ / BSD | driving the ExifTool process |
| pygbif | 0.6.6 | MIT | GBIF species names and classification |
| requests | 2.34.2 | Apache-2.0 | all outgoing HTTP |
| pysftp | 0.2.9 | BSD-3-Clause | publishing the website over SFTP |
| paramiko | 3.5.1 | LGPL-2.1-or-later | the SSH transport under pysftp |
| deep-translator | 1.11.4 | MIT | Dutch ↔ English blog translation |
| langdetect | 1.0.9 | MIT | deciding which language a text is in |
| fastcrc | 0.3.6 | MIT | translation-cache keys |
| openpyxl | 3.1.5 | MIT | the Excel checklist |

The packages these depend on in turn carry permissive licences — MIT, BSD, Apache-2.0, PSF
and MPL-2.0 (`certifi`).

---

## Native libraries inside the Python wheels

The downloadable program includes these libraries.

| Library | Licence | Arrives with | Notes |
|---|---|---|---|
| Qt 6 | LGPL-3.0 | PyQt6-Qt6 | a separate, replaceable file |
| LibRaw (`raw_r.dll`) | LGPL-2.1 **or** CDDL-1.0 | rawpy | a separate, replaceable file |
| libheif | LGPL-3.0 | pillow-heif | HEIF container handling |
| libde265 | LGPL-3.0 | pillow-heif | HEVC decoder, for reading HEIC pictures |
| FFmpeg | LGPL-2.1 | opencv-python | video codecs |

The program is delivered as a folder, not as a single file, so each of these libraries is a
separate file that anybody can replace with their own build, as their LGPL licences provide.

SpeciesViewer reads HEIC pictures but never writes them. The HEVC *encoder* that comes with
pillow-heif (`libx265`, GPL-2.0) is therefore not included.

---

## External programs

**ExifTool**, by Phil Harvey — <https://exiftool.org> — is a separate program that
SpeciesViewer runs for every metadata read and write. It is free software under the same
terms as Perl itself: the Artistic License or the GNU General Public License.

The Windows download of SpeciesViewer **includes** the official ExifTool package from
exiftool.org, unchanged except for the rename its own instructions ask for
(`exiftool(-k).exe` → `exiftool.exe`), in the `exiftool` folder beside `SpeciesViewer.exe`,
with its own README.

**DxO PhotoLab** is a separate commercial program. SpeciesViewer works with it by reading and
writing its `.dop` sidecar files; no DxO software is included.

---

## Included with every published website

**Leaflet 1.9.4** — © 2010–2023 Vladimir Agafonkin, © 2010–2011 CloudMade — BSD-2-Clause.
<https://leafletjs.com>. Each website carries its own copy, so its maps do not depend on
another website.

The photographs, icons and stylesheets that come with SpeciesViewer are the work of its author
and are covered by the same licence as the program.

---

## Online services and data

These services have their own terms of use.

**OpenStreetMap** — map tiles (`tile.openstreetmap.org`) and place names
(`nominatim.openstreetmap.org`). Map data © OpenStreetMap contributors, available under the
**Open Database License (ODbL)**; the attribution is shown in every map. As the OpenStreetMap
usage policy asks, SpeciesViewer identifies itself to these services together with the
contact e-mail address from its settings.

**OpenTopoMap** — alternative map tiles, © OpenTopoMap contributors, CC-BY-SA 3.0, based on
OpenStreetMap data.

**GBIF** — the Global Biodiversity Information Facility (`api.gbif.org`), for species names
and their classification.

**Species lists** — the species list a new collection starts from is built from these
sources, each under the **Creative Commons Attribution 4.0** licence (CC BY 4.0):
- **AviList** — the AviList Global Avian Checklist (`avilist.org`): names, placement and
  order of the birds.
- **Mammal Diversity Database** (MDD) of the American Society of Mammalogists
  (`mammaldiversity.org`): names, placement and order of the mammals.
- **IOC World Bird List** (`worldbirdnames.org`), Gill, F., D. Donsker & P. Rasmussen (eds):
  the Dutch bird names, from its multilingual list.

**Avibase** (`avibase.bsc-eoc.org`) and the **Netherlands Biodiversity API**
(`api.biodiversitydata.nl`) — for checklists and species names.

**Google Translate**, reached through `deep-translator` — for translating blog text between
Dutch and English.

---

## Illustrations

The bird and animal pictures in SpeciesViewer's windows — the owl of the Setup Checklist,
the toucan of Reset Trip, the coati, the macaws while you wait, and the others — were made
with **Adobe Firefly** from photographs by Gilbert Houtekamer. © Gilbert Houtekamer. They
are part of SpeciesViewer and carry the same licence, the GPL version 3.

---

## Your pictures

Your photographs, their metadata and the websites SpeciesViewer makes from them remain yours.
The GPL applies to the program, never to what you make with it.
