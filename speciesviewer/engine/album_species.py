"""The species of an album: one row per species, one column per day.

ONE JOB: turn the master's pictures over an album's date range ({day: [picture
rows]}, as db.master_db.pictures_in_daterange gives them) into the album's
species table, in the species list's own order (birds first), and write it as
the text report + CSV the Count Species window shows and exports.

INVARIANT: the same table shape as the trip's Count Species (svr2CountSpecies)
so the window, the spreadsheet export and the "no JPG" line work unchanged —
with two differences an album asks for: no New (lifer) column, and the best
star rating in its place. Reads only; writes only the two report files.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from engine.specieslib import Level
from engine.subspecies import species_of
from engine.species_count import counts_as_species

COLOUR_COLUMNS = ["Red", "Yellow", "Green", "Blue", "Purple", "No colour"]
_NOT_KEPT = {"Blue", "Purple"}          # marked for deletion: not counted in Pics


def _stars(row) -> int:
    try:
        return max(0, int(float(row.get("Rating") or 0)))
    except (TypeError, ValueError):
        return 0


def _species_in_order(sc_dict, node="biota"):
    """Species-level records in the species list's own (taxonomic) order, each
    with is_bird — the order the trip report uses."""
    rec = sc_dict.get(node)
    if not rec:
        return
    for leaf in rec.get("Leaves") or []:
        child = sc_dict.get(leaf)
        if not child:
            continue
        if str(child.get("Level")) == str(Level.SPECIES):
            yield child
        else:
            yield from _species_in_order(sc_dict, leaf)


def _is_bird(sc_dict, rec) -> bool:
    up = rec
    for _ in range(4):
        up = sc_dict.get((up or {}).get("Up", ""))
        if up is None:
            return False
        if (up.get("ScientificName") or "").lower() == "aves":
            return True
    return False


def species_table(sc_dict, by_day: dict):
    """(days, rows). days: the album's days that have pictures, sorted. rows:
    one dict per species seen — Nr (blank for a second line of the same
    species, e.g. a subspecies), Group, English, Dutch, Scientific, Stars, one
    mark per day (best stars that day, j = a JPG without stars, x = RAW only),
    Days, Pics (not Blue/Purple), JPG yes/no, the colour counts and Total."""
    days = sorted(by_day)
    seen: dict = {}                 # sci -> {day: [rows]}
    for day in days:
        for r in by_day[day]:
            sci = (r.get("WetSoortNaam") or "").strip().lower()
            if sci:
                seen.setdefault(sci, {}).setdefault(day, []).append(r)

    rows, counted, nr = [], set(), {"Birds": 0, "Other": 0}
    for rec in _species_in_order(sc_dict):
        sci_l = (rec.get("ScientificName") or "").lower()
        per_day = seen.get(sci_l)
        if not per_day:
            continue
        group = "Birds" if _is_bird(sc_dict, rec) else "Other"
        whole = species_of(sc_dict, sci_l)
        # a group ("oplurus sp"), or a species with only probable/possible
        # pictures, is listed but not numbered (engine/species_count.py)
        counts = any(counts_as_species(sci_l, r.get("certainty") or "")
                     for rows_day in per_day.values() for r in rows_day)
        repeat = whole in counted or not counts
        if not repeat:
            counted.add(whole)
        if not repeat:
            nr[group] += 1
        sci = rec.get("ScientificName") or sci_l
        parts = sci.split()
        if len(parts) == 3:
            sci = f"{parts[0]} {parts[1]} ssp. {parts[2]}"
        row = {"Nr": "" if repeat else nr[group], "Group": group,
               "English": rec.get("EnglishName") or f"*{sci}*",
               "Dutch": rec.get("DutchName") or f"*{sci}*",
               "Scientific": sci}
        best = pics = 0
        any_jpg = False
        colours = dict.fromkeys(COLOUR_COLUMNS, 0)
        marks = {}
        for day in days:
            pics_day = per_day.get(day)
            if not pics_day:
                marks[day] = ""
                continue
            stars = max(_stars(p) for p in pics_day)
            jpg = any((p.get("BestJPG") or "").strip() for p in pics_day)
            marks[day] = (str(stars) if stars and jpg else "j" if jpg else "x")
            best = max(best, stars)
            any_jpg |= jpg
            for p in pics_day:
                label = (p.get("Label") or "").strip().capitalize()
                colours[label if label in COLOUR_COLUMNS else "No colour"] += 1
                if label not in _NOT_KEPT:
                    pics += 1
        row["Stars"] = best or ""
        row.update(marks)
        row.update({"Days": sum(1 for m in marks.values() if m), "Pics": pics,
                    "JPG": "yes" if any_jpg else "no"})
        row.update(colours)
        row["Total"] = sum(colours.values())
        rows.append(row)
    return days, rows


def write_report(reports_dir, title: str, fromdate: str, todate: str,
                 days: list, rows: list) -> tuple[Path, Path]:
    """Write 'Species overview.txt' (to read) and 'Species overview.csv' (for the
    spreadsheet export and the no-JPG line) into reports_dir."""
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    def header():
        line = " ".ljust(76) + "  ★ "
        for d in days:
            line += d[-2:] + " "
        return line + " Days   Pics"

    def line(r):
        nr = str(r["Nr"]).rjust(3) if r["Nr"] != "" else "   "
        s = f"{nr} {r['English'][:31].ljust(32)}{r['Dutch'][:39].ljust(40)}"
        s += f"{str(r['Stars']).rjust(3)} "
        for d in days:
            s += (r.get(d) or ".").ljust(3)
        s += f"{r['Days']:4}{r['Pics']:7}"
        if r["JPG"] == "no":
            s += "   No JPG"
        return s

    birds = [r for r in rows if r["Group"] == "Birds"]
    other = [r for r in rows if r["Group"] == "Other"]
    n_species = sum(1 for r in rows if r["Nr"] != "")
    no_jpg = sum(1 for r in rows if r["JPG"] == "no")
    txt = reports_dir / "Species overview.txt"
    with open(txt, "w", encoding="utf-8") as f:
        print(f"Species overview for the album {title}", file=f)
        print(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} from the master, "
              f"{fromdate} to {todate} ({len(days)} day(s) with pictures).", file=f)
        print("\nLegend per day:  1..5 = best star rating that day   j = JPG without stars"
              "   x = RAW only", file=f)
        print("Summary:         ★ = best star rating in the album   Days = days the species"
              " was seen", file=f)
        print("                 Pics = pictures kept (not Blue/Purple)   No JPG = no JPG at"
              " all in the album: no picture on the website, but still counted\n", file=f)
        if birds:
            print("Birds:\n", file=f)
            print(header(), file=f)
            for r in birds:
                print(line(r), file=f)
        if other:
            print("\nOther animals and plants:\n", file=f)
            print(header(), file=f)
            for r in other:
                print(line(r), file=f)
        print(f"\nTotals: {n_species} species"
              + (f", {no_jpg} without a JPG." if no_jpg else ", every one with a JPG."),
              file=f)

    columns = (["Nr", "Group", "English", "Dutch", "Scientific", "Stars"] + days
               + ["Days", "Pics", "JPG"] + COLOUR_COLUMNS + ["Total"])
    csv_path = reports_dir / "Species overview.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)
    return txt, csv_path
