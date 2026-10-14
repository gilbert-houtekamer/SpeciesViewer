# Illustrations

SpeciesViewer's drawn animals: the icon, the About picture, and the pictures beside a
long "Please wait…" window. All are made with Adobe Firefly from the author's own
photographs, in one style, so they read as one family.

## The Firefly recipe

Give Firefly the duck (`speciesviewer/assets/about_duck.jpg`) as the style reference, and
the photograph of the animal. The prompt that works:

> Can you make a square image in this style, colorful example
> "speciesviewer/assets/about_duck.jpg". It will be used at about 450×450, so make one of
> 1024×1024 pixels. No texts please, just the animal. No black lines, they make it harder
> than the duck. Use the tone of the duck — not too colourful; it is for a biology website.
> Keep the animal's own colour (a bit greener / browner where the real animal is). The
> background is dry and calm — it must not suggest water.

Then pick the version closest to the duck's tones; strong saturated greens or blues stand
out against the app's sand and grey.

## Where they are used

| Picture | File in the program | Used for |
|---|---|---|
| African Pygmy-goose (head) | `assets/icon/` | the program icon |
| African Pygmy-goose | `assets/about_duck.jpg` | Help ▸ About |
| Leopard tortoise | `assets/wait_tortoise.jpg` | Retire Trip to Album — the longest wait |
| Chameleon | `assets/wait_chameleon.jpg` | To Master ▸ Merge Pictures into Master (steps 2–4) |
| Vitelline masked weaver | `assets/wait_weaver.jpg` | Trips ▸ Read SD-card or Folder (and Days ▸ Copy Day from Card) — a collection is woven strand by strand |
| Eurasian jay | `assets/wait_jay.jpg` | Metadata backup and RAW backup — the jay buries acorns and finds them back |

A wait picture is `assets/wait_<name>.jpg`, about 512×512; a step shows it with
`'wait_picture': '<name>'` (see `gui/wait_picture.py`). Keep the 2048-pixel originals out
of git; the program needs only the small copy. In a window that is not a wait box, the
picture sits small beside the explanation (`picture_label` in the same module).

Which window gets which animal next, and from which of the author's photographs: see
[plans/illustration_candidates.csv](plans/illustration_candidates.csv).

The splash screen deliberately uses real photographs, not drawings: drawings are the
program's own voice, photographs the real thing.
