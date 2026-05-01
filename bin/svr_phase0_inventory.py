"""Phase 0 inventory for the seq-decouple-prefix branch.

Reports the size of the cleanup needed to split numeric sort prefixes
(e.g. "040 passeriformes") out of ScientificName into a separate Sequence
column. Read-only — touches nothing.
"""
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

MASTER = Path(r"E:/Dropbox/naturedb")
PREFIX_RE = re.compile(r"^\d+\s+")


def has_prefix(name):
    return bool(name) and bool(PREFIX_RE.match(name))


def strip_prefix(name):
    return PREFIX_RE.sub("", name)


def section(title):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def csv_rows(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            yield row


# ---------------------------------------------------------------------
# 1. speciescollection.csv
# ---------------------------------------------------------------------
section("speciescollection.csv (taxonomy / sc_dict)")
sc_path = MASTER / "metadata/active/speciescollection.csv"
total = 0
prefixed_by_level = Counter()
total_by_level = Counter()
prefixed_examples = defaultdict(list)
prefixed_up_refs = 0
prefixed_leaf_refs = 0

for row in csv_rows(sc_path):
    total += 1
    level = row["Level"]
    total_by_level[level] += 1
    if has_prefix(row["ScientificName"]):
        prefixed_by_level[level] += 1
        if len(prefixed_examples[level]) < 3:
            prefixed_examples[level].append(row["ScientificName"])
    if has_prefix(row.get("Up", "")):
        prefixed_up_refs += 1
    leaves_raw = row.get("Leaves", "") or ""
    if leaves_raw and leaves_raw != "[]":
        for chunk in leaves_raw.strip("[]").split(","):
            if has_prefix(chunk.strip().strip("'\"")):
                prefixed_leaf_refs += 1

print(f"Total rows:           {total}")
print(f"Prefixed Up refs:     {prefixed_up_refs}")
print(f"Prefixed Leaf refs:   {prefixed_leaf_refs}")
print()
print(f"{'Level':<18}{'total':>8}{'prefixed':>12}   examples")
for level in sorted(total_by_level, key=lambda l: -total_by_level[l]):
    ex = ", ".join(prefixed_examples.get(level, [])) or "—"
    print(f"{level:<18}{total_by_level[level]:>8}{prefixed_by_level[level]:>12}   {ex}")

# ---------------------------------------------------------------------
# 2. species.csv
# ---------------------------------------------------------------------
section("species.csv (species_dict)")
sp_path = MASTER / "metadata/active/species.csv"
total = 0
prefixed = 0
prefixed_targetpath = 0
sp_total_by_level = Counter()
sp_prefixed_by_level = Counter()

for row in csv_rows(sp_path):
    total += 1
    level = row["Level"]
    sp_total_by_level[level] += 1
    if has_prefix(row["ScientificName"]):
        prefixed += 1
        sp_prefixed_by_level[level] += 1
    tp = row.get("TargetPath", "") or ""
    if any(has_prefix(seg) for seg in tp.split("/")):
        prefixed_targetpath += 1

print(f"Total rows:           {total}")
print(f"Prefixed names:       {prefixed}")
print(f"TargetPath rows with prefixed segment: {prefixed_targetpath}")
print()
print(f"{'Level':<18}{'total':>8}{'prefixed':>12}")
for level in sorted(sp_total_by_level, key=lambda l: -sp_total_by_level[l]):
    print(f"{level:<18}{sp_total_by_level[level]:>8}{sp_prefixed_by_level[level]:>12}")

# ---------------------------------------------------------------------
# 3. data_pictures.csv (denormalised name columns + TargetPath)
# ---------------------------------------------------------------------
section("data_pictures.csv (UniquePictureName_dict)")
dp_path = MASTER / "metadata/active/data_pictures.csv"
total = 0
prefixed_targetpath = 0
denorm_columns = ["Kingdom", "Phylum", "Class", "Order", "Family"]
prefixed_denorm = Counter()

for row in csv_rows(dp_path):
    total += 1
    tp = row.get("TargetPath", "") or ""
    if any(has_prefix(seg) for seg in tp.split("/")):
        prefixed_targetpath += 1
    for col in denorm_columns:
        if has_prefix(row.get(col, "") or ""):
            prefixed_denorm[col] += 1

print(f"Total picture rows:   {total}")
print(f"TargetPath rows with prefixed segment: {prefixed_targetpath}")
print()
print(f"{'denorm column':<14}{'prefixed rows':>16}")
for col in denorm_columns:
    print(f"{col:<14}{prefixed_denorm[col]:>16}")

# ---------------------------------------------------------------------
# 4. On-disk directories: biota/ and web/biota/
# ---------------------------------------------------------------------
section("On-disk directories with numeric prefix")
for label, root in [
    ("biota", MASTER / "biota"),
    ("web/biota", MASTER / "web/biota"),
]:
    if not root.exists():
        print(f"{label}: {root} does not exist, skipping")
        continue
    prefixed_dirs = []
    total_dirs = 0
    for dirpath, dirnames, _ in os.walk(root):
        for d in dirnames:
            total_dirs += 1
            if has_prefix(d):
                prefixed_dirs.append(os.path.join(dirpath, d))
    print(f"\n{label}: {total_dirs} dirs total, {len(prefixed_dirs)} need rename")
    if prefixed_dirs:
        # show a few examples and the unique prefixed basenames
        unique_basenames = sorted({os.path.basename(p) for p in prefixed_dirs})
        print(f"   {len(unique_basenames)} unique prefixed basenames")
        for name in unique_basenames[:15]:
            print(f"      {name}")
        if len(unique_basenames) > 15:
            print(f"      ... and {len(unique_basenames) - 15} more")

# ---------------------------------------------------------------------
# 5. Web pages — redirect rule estimation
# ---------------------------------------------------------------------
section("Web pages under prefixed paths (redirect estimate)")
web_root = MASTER / "web/biota"
if web_root.exists():
    prefixed_html = 0
    total_html = 0
    for dirpath, _, filenames in os.walk(web_root):
        rel = Path(dirpath).relative_to(web_root)
        any_prefixed_segment = any(has_prefix(seg) for seg in rel.parts)
        for f in filenames:
            if f.endswith(".html"):
                total_html += 1
                if any_prefixed_segment:
                    prefixed_html += 1
    print(f"Total .html under web/biota: {total_html}")
    print(f"Live under prefixed path:    {prefixed_html}")
    print()
    print("Each unique prefixed directory needs ~1 RewriteRule pattern,")
    print("so redirect file size scales with the unique-basename count above,")
    print("not with the .html count.")

print()
print("=" * 70)
print("Done.")
print("=" * 70)
