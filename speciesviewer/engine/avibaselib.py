"""Shared helpers for reading and enriching an AviBase/BirdingBreaks species list."""

import os
import logging

logger = logging.getLogger("speciesviewer")


def read_avibase(triproot, filename):
    """Read AviBase CSV and return a dict keyed by enname (column 0).

    CSV columns: English/key ; Scientific ; Dutch ; Status
    Section-header rows (no scientific name) get number=-1.
    """
    file_path = os.path.join(triproot, filename)
    number = 1
    avibase_dict = {}

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f.read().split("\n"):
            tokens = line.split(";")
            try:
                if tokens[0] in ("﻿Column1", "﻿Engels", ""):
                    continue
                if tokens[0].strip() == "":
                    continue
                if len(tokens) > 1 and tokens[1] not in ("Wet", ""):
                    sp = {
                        "enname":       tokens[0],
                        "scname":       tokens[1],
                        "nlname":       tokens[2] if len(tokens) > 2 else "",
                        "status":       tokens[3] if len(tokens) > 3 else "",
                        "english":      "",
                        "number":       number,
                        "gezien":       0,
                        "foto":         0,
                        "knownspecies": False,
                    }
                    number += 1
                    avibase_dict[sp["enname"]] = sp
                elif len(tokens) > 1 and tokens[1] == "Wet":
                    sp = {
                        "enname":       tokens[0],
                        "scname":       tokens[1],
                        "nlname":       tokens[2] if len(tokens) > 2 else "",
                        "status":       tokens[3] if len(tokens) > 3 else "",
                        "english":      "",
                        "number":       "Wet",
                        "gezien":       0,
                        "foto":         0,
                        "knownspecies": False,
                    }
                    avibase_dict[sp["enname"]] = sp
                else:
                    # Section header row — no scientific name
                    avibase_dict[tokens[0]] = {
                        "enname":  tokens[0],
                        "scname":  "",
                        "nlname":  "",
                        "status":  "",
                        "english": "",
                        "number":  -1,
                    }
            except Exception:
                pass

    return avibase_dict


def add_species_info(avibase_dict, sc, sp):
    """Enrich avibase_dict entries with canonical scientific names and English names.

    sc  — SpeciesClassification instance (has english_dict, dutch_dict, sc_dict)
    sp  — Species instance (has species_dict for known-species check)
    Mutates avibase_dict in place.
    """
    for enname, entry in avibase_dict.items():
        if not entry.get("scname"):
            continue

        original_scname = entry["scname"]

        if enname.lower() in sc.english_dict:
            entry["scname"] = sc.english_dict[enname.lower()]
            if entry["scname"] in sp.species_dict:
                entry["knownspecies"] = True
        else:
            if not entry["nlname"] and entry["scname"].lower() in sc.sc_dict:
                entry["nlname"] = sc.sc_dict[entry["scname"].lower()].get("DutchName", "")
            if entry["nlname"].lower() in sc.dutch_dict:
                entry["scname"] = sc.dutch_dict[entry["nlname"].lower()]
                if entry["scname"] in sp.species_dict:
                    entry["knownspecies"] = True

        # Fill English name from classification when missing
        if not entry.get("english"):
            sc_entry = sc.sc_dict.get(entry["scname"].lower())
            if sc_entry and sc_entry.get("EnglishName"):
                entry["english"] = sc_entry["EnglishName"]

        if (original_scname and entry["scname"] and
                entry["scname"].lower() != original_scname.lower()):
            label = entry.get("nlname") or entry.get("english") or enname
            logger.info(
                f"Scientific name updated for '{label}': "
                f"'{original_scname}' → '{entry['scname']}'"
            )
