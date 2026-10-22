# Contributing to SpeciesViewer

Thank you for wanting to help. SpeciesViewer is a beta, written and used by one photographer,
so every bug report, idea and fix is welcome.

## Reporting a bug or an idea

Open a [GitHub issue](../../issues), or e-mail
[speciesviewer@houtekamer.com](mailto:speciesviewer@houtekamer.com). For a bug, it helps to
include:

- what you did, what you expected, and what happened instead;
- the version (*Help ▸ About*) and Windows or macOS;
- the log file, `%LOCALAPPDATA%\SpeciesViewer\logs\speciesviewer.log` (its full path is the first line
  of the Log panel at the bottom of the main window);
- a screenshot, when it is about something on screen.

## Before you write code

Please **open an issue first** and describe what you have in mind, so the change fits in and is
not done twice. Small fixes (a typo, a crash with an obvious cause) can go straight to a pull
request.

## Running SpeciesViewer from source

See [Installing SpeciesViewer](docs/install.md): Python 3.12, ExifTool, then
`pip install -r requirements.txt` and `python speciesviewer/SpeciesViewer.py`.

The program's own documentation for developers:

- [docs/README.md](docs/README.md): which document explains what.
- [docs/manual/appendix-c-data-architecture.md](docs/manual/appendix-c-data-architecture.md):
  where pictures live, and how a trip flows into the master. Read this first.
- [docs/configuration_inventory.md](docs/configuration_inventory.md): every settings file, and
  where a new setting belongs.

## Testing

Run the smoke test from the repository root before every pull request:

    python -m pytest

It takes a few seconds and needs no master, pictures, internet or screen. It checks that every
module imports, that the files shipped with the program are present, and that the starter species
list is sound. Your own settings are never touched: the tests run against a throw-away folder.

Two self-tests that also run anywhere:

    python tools/sv_card_empty_selftest.py    # emptying a card: verdicts and copies
    python tools/svr_extension_audit.py       # RAW extensions live in one table only

And always try your change in the program itself, in a **test master**
(*Master ▸ Masters ▸ New Master…*), never in your real collection.

## How the code is written

- **Small steps.** One change per pull request, each one testable on its own.
- **Text for the user.** Messages, buttons and menus say what happens in plain words, without
  internal names. A dialog that deletes something says exactly what goes.
- **Nothing is lost silently.** A write that fails says so; deleted pictures go to a trash
  folder first.
- **Cross-platform.** Use `pathlib`; no drive letters, no registry.
- **Match the code around you**: its naming, its comments, its idioms.

## Licence

SpeciesViewer is free software under the **GNU General Public License, version 3**. By
contributing, you agree that your contribution is licensed under the same terms. See
[LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).
