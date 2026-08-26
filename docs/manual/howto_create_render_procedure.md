# How to: Create a render that the master picks up (DxO PhotoLab)

The order of steps matters. A JPG render only reaches the website if PhotoLab
actually ingests your crop/recipe **and** the master then registers the new
file. Do it out of order and PhotoLab quietly ignores a from-scratch sidecar, or
you forget the register step and the render never counts. This is the sequence
that works — and, done in this order, the app now **auto-notices** the render
and offers a one-click register when you return to it (see the bottom).

Everything on the SpeciesViewer side happens in the **large image view** (open a
picture from the day grid). The two buttons that matter are **✂ Crop** and the
**PL** badge (open in DxO) on the FILE row.

## The sequence

1. **Make sure a `.dop` exists first.**
   PhotoLab does **not** reliably auto-read a *from-scratch* `.dop` — one the app
   wrote for a RAW that PhotoLab has never seen. So the `.dop` must be in place
   *before* you open the file in PhotoLab. If you're going to crop, do it now:
   **✂ Crop** in the large viewer writes the crop into both the `.dop` (for
   PhotoLab) and the `.xmp` (for Lightroom). If you're not cropping but the RAW
   has no `.dop` yet, make sure one is authored before step 2.

2. **Open in PhotoLab with the PL button** — optionally *after* cropping.
   Use the **PL** badge on the large viewer's FILE row (not a manual
   File ▸ Open in DxO). It hands PhotoLab the RAW with the right shell context,
   so DxO lands in the correct species folder with the filmstrip of siblings.

3. **Read the sidecar in PhotoLab.**
   Because a from-scratch `.dop` isn't picked up automatically (step 1), tell
   PhotoLab to read the sidecar so it ingests your crop/recipe. Skip this and
   PhotoLab develops the uncropped, recipe-less file.

4. **Set the DeepPRIME options.**
   Choose the denoise method you want (DeepPRIME / DeepPRIME XD) before export.
   This is what the master stamps as the picture's noise-removal method.

5. **Write (export) in PhotoLab.**
   Export the JPG beside the RAW, in the same folder. That's the file the master
   will register.

## What happens next (automatic)

When you return to the SpeciesViewer window, the **Album / virtual-trip day
viewer** scans the folders you handed to PhotoLab this session and notices the
fresh JPG the master doesn't track yet:

- the picture's card shows a green **+ JPG** link, and
- in the large viewer, the **JPG** token on the FILE row turns amber.

Click **+ JPG** to register the render into the master (it becomes the picture's
best JPG and the Rate rises). Then re-write the day's blog / rebuild the website
to publish it.

> **Scope.** This auto-notice + one-click register works in the **Album /
> virtual-trip** day viewer (a live window on the master). In a **real Trip** day
> viewer there is no master to register into — there a render is folded in by
> re-running **Read &amp; Summarize** for the day instead.

## Why the order is strict

The fragile link is PhotoLab's sidecar handling: it recognises a `.dop` it
authored, but a `.dop` the app created from scratch needs an explicit *read
sidecar* (steps 1 + 3). Everything downstream — the master's noise-removal
stamp, the best-JPG pick, the website — reads what PhotoLab actually wrote, so if
the crop/recipe never made it into the export, none of it can recover the intent.

Related code: the crop writer and the *open in DxO* launch live in
[`gui/large_image_view.py`](../speciesviewer/gui/large_image_view.py); the
new-render detector is `pending_renders` in
[`gui/sidecar_provider.py`](../speciesviewer/gui/sidecar_provider.py), backed by
`untracked_renders_for_upns` in
[`db/master_mutate.py`](../speciesviewer/db/master_mutate.py).
