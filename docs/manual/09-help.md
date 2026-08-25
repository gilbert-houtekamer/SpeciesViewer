# 9. Help

Two items. Small, but the first one earns its place.

| Item | Needs |
|---|---|
| Connection status… | |
| About | |

---

## Connection status…

Shows whether the services SpeciesViewer depends on are reachable, as a set of
status lamps.

This matters more than it sounds, because the application is designed to work
**offline** and degrade in specific ways rather than fail:

- **Publishing** gates on SFTP. If the site is unreachable, the steps that upload
  cannot complete — and it is much better to know that before running a full
  website rebuild.
- **Species name resolution** (GBIF) keeps working offline for names already
  known locally. New names simply stay unidentified until you are back online, at
  which point re-running the step resolves them. The lookup times out rather than
  hanging.

So the useful reading of this dialog is: *what will silently not happen if I run
the pipeline right now?*

---

## About

Version and build information.
