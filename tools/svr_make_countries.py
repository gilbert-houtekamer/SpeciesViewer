"""Generate speciesviewer/assets/countries.csv — every ISO country with its English
and Dutch name, plus the other spellings our pictures use. Dev-only; the app never
runs this and does not need Babel.

    <venv with babel>/python tools/svr_make_countries.py

The names come from CLDR (the Unicode locale data, via Babel). OURS below lays our
own choices on top: the display name we prefer where CLDR differs, and the aliases —
former names (Swaziland), short forms (UK, USA), our own typos (Ecuados), what the
place lookup (Nominatim) writes, and the few non-country values that ended up in the
Country field (note column says so). See docs/plans/country_names_plan.md.

Re-running overwrites the CSV, so edit OURS here, not the CSV by hand.
"""

import csv
import re
from pathlib import Path

from babel import Locale

OUT = Path(__file__).resolve().parents[1] / "speciesviewer" / "assets" / "countries.csv"

# CLDR two-letter codes that are regions or placeholders, not countries.
NOT_COUNTRIES = {"EU", "EZ", "UN", "ZZ", "QO", "XA", "XB"}

# iso -> (en or None, nl or None, [aliases], note). None = keep the CLDR name.
OURS = {
    "BE": (None, None, ["Belgie", "Belgique"], ""),
    "BR": (None, None, ["Brasil", "Brazilie"], ""),
    "CI": (None, None, ["Ivory Coast"], ""),
    "CZ": (None, None, ["Czech Republic", "Tsjechische Republiek"], ""),
    "DE": (None, None, ["Deutschland"], ""),
    "EC": (None, None, ["Ecuados"], "Ecuados is a typo"),
    "ES": (None, None, ["España"], ""),
    "GB": (None, "Groot-Brittannië", ["UK", "England", "Great Britain",
                                      "Scotland", "Wales", "Northern Ireland",
                                      "Engeland", "Schotland"], ""),
    "GM": ("The Gambia", None, ["Gambia"], ""),
    "MA": (None, None, ["Maroc"], ""),
    "MM": (None, None, ["Burma"], ""),
    "NL": (None, None, ["Holland", "Nederland", "The Netherlands",
                        "Friesland", "Oostvoorne"],
           "Friesland and Oostvoorne are places, not countries"),
    "SZ": (None, None, ["Swaziland"], "former name Swaziland"),
    "TR": (None, None, ["Turkey", "Turkije"], ""),
    "TZ": (None, None, ["Tanzania, United Republic of"], ""),
    "US": (None, None, ["USA", "US", "VS", "Verenigde Staten van Amerika",
                        "United States of America", "Texas"],
           "Texas is a state, not a country"),
}


def main():
    en = Locale("en").territories
    nl = Locale("nl").territories
    rows = []
    for iso in sorted(k for k in en if re.fullmatch("[A-Z]{2}", k)):
        if iso in NOT_COUNTRIES:
            continue
        my_en, my_nl, aliases, note = OURS.get(iso, (None, None, [], ""))
        name_en, name_nl = my_en or en[iso], my_nl or nl.get(iso, en[iso])
        # The CLDR names stay findable when we display a different one.
        extra = [n for n in (en[iso], nl.get(iso, "")) if n and n not in (name_en, name_nl)]
        seen, alias_out = {name_en, name_nl}, []
        for a in extra + aliases:
            if a not in seen:
                seen.add(a)
                alias_out.append(a)
        rows.append((iso, name_en, name_nl, ";".join(alias_out), note))
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(("iso", "en", "nl", "aliases", "note"))
        w.writerows(rows)
    print(f"{len(rows)} countries -> {OUT}")


if __name__ == "__main__":
    main()
