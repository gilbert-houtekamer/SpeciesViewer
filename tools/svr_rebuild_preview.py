"""Non-destructive master rebuild PREVIEW + black-image correlation scan.

Runs the admin-heal reconcile against the master WITHOUT changing anything, so
you can open the suggested CSVs next to the live ones in Excel and decide before
committing to a real heal. Produces, under <out>/:

  data_files.csv        suggested (healed) data_files.csv     } diff these against
  data_pictures.csv     suggested (healed) data_pictures.csv  } metadata/active/*
  rebuild_remove.csv     rows a heal would DELETE (file truly gone)
  rebuild_repoint.csv    rows a heal would PATH-FIX (file present, path mangled)
  rebuild_add.csv        files on disk missing from the CSV
  rebuild_species_change.csv  pictures whose folder disagrees with the admin
  display_status.csv     per-picture display resolution -> "black image" suspects

Nothing under metadata/active/ is touched. Run repeatedly and keep the dated
output folders so black-image sightings can be correlated across runs.

Usage:
    python tools/svr_rebuild_preview.py [--master PATH] [--out DIR]
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "speciesviewer"))

from engine.admin_rebuild import plan_rebuild, execute_heal, scan_display_status


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--master", default="E:/Dropbox/naturedb")
    ap.add_argument("--out", default=None,
                    help="output folder (default: ./rebuild_preview_<timestamp>)")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out or os.path.join(os.getcwd(), f"rebuild_preview_{stamp}")
    os.makedirs(out, exist_ok=True)
    active = os.path.join(args.master, "metadata", "active")

    print(f"master : {args.master}")
    print(f"output : {out}\n")

    # 1. reconcile (dry) -- writes rebuild_*.csv into out
    print("[1/3] planning rebuild (scanning biota tree)…")
    plan = plan_rebuild(args.master, out_dir=out)
    if not plan.get("ok"):
        print("  FAILED:", plan.get("error")); return
    print(f"      disk_files={plan['disk_files']}  admin_files={plan['admin_files']}")
    print(f"      REMOVE={plan['remove']}  REPOINT={plan['repoint']}  "
          f"ADD={plan['add']} {plan['add_breakdown']}  SPECIES={plan['species_change']}")

    # 2. preview heal -- suggested CSVs into out, master untouched
    print("\n[2/3] writing suggested (healed) CSVs (master untouched)…")
    heal = execute_heal(args.master, out_dir=out, report_dir=out)
    if not heal.get("ok"):
        print("  FAILED:", heal.get("error")); return
    print(f"      data_files.csv   : {heal['kept_files']} rows "
          f"(removed {heal['removed_files']}, repointed {heal['repointed']})")
    print(f"      data_pictures.csv: dropped {heal['pictures_dropped']} pictures, "
          f"cleaned {heal['files_cleaned']} Files lists")

    # 3. display-status / black-image scan
    print("\n[3/3] scanning display status (black-image suspects)…")
    disp = scan_display_status(args.master,
                               out_csv=os.path.join(out, "display_status.csv"))
    if disp.get("ok"):
        print(f"      pictures={disp['total']}  by status: {disp['by_status']}")

    print("\n" + "=" * 64)
    print("Compare in Excel:")
    print("  CURRENT  ", os.path.join(active, "data_files.csv"))
    print("  SUGGESTED", os.path.join(out, "data_files.csv"))
    print("  CURRENT  ", os.path.join(active, "data_pictures.csv"))
    print("  SUGGESTED", os.path.join(out, "data_pictures.csv"))
    print("Black-image suspects: filter display_status.csv on status = black")
    print("=" * 64)


if __name__ == "__main__":
    main()
