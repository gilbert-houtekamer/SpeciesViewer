# Installing SpeciesViewer

## Windows — the download

1. Download `SpeciesViewer-<version>-windows.zip` from the
   [latest release](https://github.com/gilbert-houtekamer/SpeciesViewer/releases/latest).
   Windows may warn "Windows protected your PC" when you start it: SpeciesViewer has no paid
   code-signing certificate. Click *More info*, then *Run anyway*.
2. Unpack it to any folder, for example `C:\Programs\SpeciesViewer`. Keep the folder together:
   the program needs the files beside it.
3. Start `SpeciesViewer.exe`.

ExifTool, which SpeciesViewer uses to read and write picture metadata, is included. Nothing is
written to the program folder; your settings go to `%LOCALAPPDATA%\SpeciesViewer`.

To update, unpack the new ZIP over the old folder or next to it. Your settings stay where
they are.

To remove SpeciesViewer, delete the program folder. To remove your settings too, delete
`%LOCALAPPDATA%\SpeciesViewer`. Your pictures and your master collection are never inside
either folder.

## macOS and Linux — from source

SpeciesViewer is written to be platform independent: it is Python with Qt, both of which run
on macOS and Linux. A port has not been done yet, though. It is developed and tested on
Windows, so expect rough edges elsewhere — and reports of what does or does not work are very
welcome.

You need:

- **Python 3.12** — on macOS from [python.org](https://www.python.org/downloads/) or Homebrew
  (`brew install python@3.12`).
- **ExifTool** — on macOS `brew install exiftool`; on Debian/Ubuntu
  `sudo apt install libimage-exiftool-perl`.
- **git**, to fetch the source.

Then, in a terminal:

```sh
git clone https://github.com/gilbert-houtekamer/SpeciesViewer.git
cd SpeciesViewer
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python speciesviewer/SpeciesViewer.py
```

Settings go to `~/Library/Application Support/SpeciesViewer` on macOS and to
`~/.local/share/SpeciesViewer` on Linux.

To update: `git pull`, then `pip install -r requirements.txt` again in case something new is
needed.

## Windows — from source

The same as above, with Python 3.12 from [python.org](https://www.python.org/downloads/)
(not the Microsoft Store version) and ExifTool from [exiftool.org](https://exiftool.org) on
your `PATH`:

```bat
git clone https://github.com/gilbert-houtekamer/SpeciesViewer.git
cd SpeciesViewer
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python speciesviewer\SpeciesViewer.py
```

## First start

SpeciesViewer opens with **Help ▸ Setup Checklist** when there is nothing set up yet. Follow it
from the top:

1. **Create a master collection** (Master ▸ Masters) — the folder where your best pictures of
   every species will live. Choose a disk with room to grow.
2. **Add pictures** — from a memory card or a folder of a day's pictures — and name the
   species.
3. **Trips, albums and a website** are optional; the checklist shows what each one needs when
   you get there.
