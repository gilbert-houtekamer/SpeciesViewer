# svu5pdfchecklist.py: Turn a checklist copied out of a PDF into a semicolon-separated list.
# Written for 'oude checklist brazil.txt' (Chapada dos Guimaraes / Pantanal 2018), where the
# PDF text extraction left the per-day X/H tally marks on lines of their own and repeated the
# day-number header on every page. Keeps only what a species list needs: group, English name,
# scientific name — the shape svu2avibase reads with columns A group, B English, C scientific.
# The group headings in the source (ZOOGDIEREN, REPTIELEN) become the group column; mammals
# and reptiles carry no scientific name in the source at all, so that column stays empty.
# Run again per checklist: the numbering check at the end is the thing worth reading.

import argparse
import io
import re
from collections import Counter
from pathlib import Path

GROUPS = {"ZOOGDIEREN": "Mammals", "REPTIELEN": "Reptiles"}
MONTHS = (r"\b(januari|februari|maart|april|mei|juni|juli|augustus"
          r"|september|oktober|november|december)\b")
GENUS = re.compile(r"^[A-Z][a-z]+$")
EPITHET = re.compile(r"^[a-z][a-z-]+$")


def split_names(tokens):
    """English name, scientific name — the scientific name being the trailing
    Genus plus its all-lowercase epithet(s). Scanned from the right so that a
    hyphenated English name ("Gray-cowled Wood-Rail Aramides cajaneus") and the
    odd trinomial ("Vireo olivaceus chivi") both land right. Never starts at
    token 0: a line without a scientific name keeps its whole English name."""
    for i in range(len(tokens) - 1, 0, -1):
        if GENUS.match(tokens[i]) and tokens[i + 1:] \
                and all(EPITHET.match(t) for t in tokens[i + 1:]):
            return " ".join(tokens[:i]), " ".join(tokens[i:])
    return " ".join(tokens), ""


def parse(text):
    """Rows of (group, number, english, scientific), plus the lines left out."""
    group, rows, skipped = "Birds", [], []
    for lineno, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s:
            continue
        if s in GROUPS:
            group = GROUPS[s]
            continue
        if re.fullmatch(r"[XH](\s+[XH])*", s):      # tally marks, one per line
            continue
        if re.fullmatch(r"\d+(\s+\d+)*", s):        # the day-number header, per page
            continue
        if re.search(MONTHS, s, re.I):              # the trip-date line, not a species
            skipped.append((lineno, s))
            continue
        m = re.match(r"^(\d+)\s+(.+)$", s)
        if not m:                                   # titles and anything unexpected
            skipped.append((lineno, s))
            continue
        tokens = m.group(2).split()
        while tokens and tokens[-1] in ("X", "H"):  # tally still on the species line
            tokens.pop()
        if not tokens:
            skipped.append((lineno, s))
            continue
        english, scientific = split_names(tokens)
        rows.append((group, int(m.group(1)), english, scientific))
    return rows, skipped


def report(rows, skipped):
    """Say what came out, and check each group's numbering runs 1..N. A gap or a
    repeat is usually the source list's own slip, but it is never worth guessing
    about — the numbers are printed so they can be looked up in the PDF."""
    print(Counter(g for g, _, _, _ in rows))
    seen = {}
    for group, number, _, _ in rows:
        seen.setdefault(group, []).append(number)
    for group, numbers in seen.items():
        expected = list(range(1, len(numbers) + 1))
        print(f"   {group}: {len(numbers)} rows, numbered 1..{len(numbers)}: "
              f"{'yes' if numbers == expected else 'NO — check the source'}")
        if numbers != expected:
            missing = sorted(set(expected) - set(numbers))
            repeated = sorted(n for n, c in Counter(numbers).items() if c > 1)
            print(f"      missing {missing}, repeated {repeated}")
    without = [e for g, _, e, s in rows if not s]
    if without:
        print(f"   {len(without)} rows with no scientific name, first: {without[0]!r}")
    for lineno, s in skipped:
        print(f"   skipped line {lineno}: {s}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path, help="the text pulled out of the PDF")
    ap.add_argument("-o", "--output", type=Path,
                    help="the semicolon file to write (default: source with .csv)")
    args = ap.parse_args()

    out = args.output or args.source.with_suffix(".csv")
    text = args.source.read_bytes().decode("utf-8-sig")
    rows, skipped = parse(text)
    # utf-8-sig: Excel reads the accented names right only with the BOM.
    with io.open(out, "w", encoding="utf-8-sig", newline="") as f:
        f.write("Group;EnglishName;ScientificName\n")
        for group, _, english, scientific in rows:
            f.write(f"{group};{english};{scientific}\n")
    print(f"Wrote {len(rows)} rows to '{out}'.")
    report(rows, skipped)


if __name__ == "__main__":
    main()
