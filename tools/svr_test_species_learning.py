"""Test that a species looked up once is never looked up again.

A species is learned in four places (batch recognition, the culling viewer, the
species editor, the Edit Species List dialog) and used to be persisted in three
different ways — or, in the species editor's case, not at all. It is now
buffered on the classification and appended once, to the master's list and the
local copy, when the session ends.

Exercised here, against a copy of a real collection, with no network:

  1. append — a new species, its missing ancestors, in ancestor-first order
  2. enrichment — a later row wins on read, so a filled-in name persists
  3. the H1 fix — a row with a broken Up costs only that row, not the rest
  4. no trailing newline — appending must not glue rows together
  5. the buffer — nothing is written until the flush, then written once
  6. master required — an unreachable master is an error AND keeps the buffer
  7. the negative cache is process-wide, not per classification

NOTHING outside a temp directory is written; the real collection is only read.

Usage:
    python tools/svr_test_species_learning.py [--collection PATH]
"""
import argparse
import configparser
import logging
import os
import shutil
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "speciesviewer"))

import engine.speciesclassification as scmod                      # noqa: E402
from engine.speciesclassification import (SpeciesClassification,   # noqa: E402
                                          Level, namesInSpeciesCollection)

failures = []


def check(label, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(label + (f" — {detail}" if detail else ""))


def fresh(src, tmp, name="sc.csv"):
    p = os.path.join(tempfile.mkdtemp(dir=tmp), name)
    shutil.copy(src, p)
    return p


def loaded(path):
    sc = SpeciesClassification(None)
    sc.readSpeciesCollection(path)
    return sc


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--collection", help="a speciescollection.csv to copy (read-only)")
    args = ap.parse_args()

    src = args.collection
    if not src:
        cfg = configparser.ConfigParser()
        from utils.runtime_files import svconfig_path
        cfg.read(svconfig_path(), encoding="utf-8")
        master = cfg.get("master", "MasterBiotaDatabase", fallback="")
        src = os.path.join(master, "metadata", "active", "speciescollection.csv")
    if not os.path.exists(src):
        ap.error(f"no species collection at {src!r}")

    logging.basicConfig(level=logging.CRITICAL)
    logging.getLogger("speciesviewer").setLevel(logging.CRITICAL)
    tmp = tempfile.mkdtemp(prefix="sv_learn_test_")
    print(f"collection  {src}\nsandbox     {tmp}\n")
    try:
        base_rows = len(namesInSpeciesCollection(src))
        print(f"{base_rows} rows on file\n")

        # -- 1. a new species with missing ancestors, ancestors first ---------
        p = fresh(src, tmp)
        sc = loaded(p)
        check("a plain read learns nothing", sc.learned == set(), f"{len(sc.learned)}")
        sc.addSpeciesLevel(Level.ORDER, 'testorder', '', '', 'insecta')
        sc.addSpeciesLevel(Level.FAMILY, 'testfamily', '', '', 'testorder')
        sc.addSpeciesLevel(Level.SPECIES, 'testfamily bugus', '', '', 'testfamily')
        written = sc.flushSpeciesLearned([p])[p]
        check("ancestors are written before their children",
              written == ['testorder', 'testfamily', 'testfamily bugus'], str(written))
        back = loaded(p)
        check("the whole chain links on re-read",
              back.sc_dict['testfamily']['Up'] == 'testorder'
              and 'testfamily bugus' in back.sc_dict['testfamily']['Leaves']
              and 'testorder' in back.sc_dict['insecta']['Leaves'])
        check("the buffer is cleared once written", sc.learned == set())

        # -- 2. enrichment of a row already on file ---------------------------
        p = fresh(src, tmp)
        sc = loaded(p)
        victim = next(n for n, r in sc.sc_dict.items()
                      if r['Level'] == Level.SPECIES and r['DutchName'] == '')
        sc.addSpeciesLevel(Level.SPECIES, victim, 'testnaam', '', sc.sc_dict[victim]['Up'])
        sc.flushSpeciesLearned([p])
        check("a filled-in name survives the round trip",
              loaded(p).sc_dict[victim]['DutchName'] == 'testnaam', victim)

        # -- 3. one broken row must not cost the rest (H1) --------------------
        p = fresh(src, tmp)
        with open(p, 'a', encoding='utf-8', newline='') as f:
            f.write('Level.SPECIES;orphanus lostus;;;nosuchparent;[]\r\n')
            f.write('Level.SPECIES;afterus orphanus;;;linyphiidae;[]\r\n')
        check("a row after a broken one still links",
              'afterus orphanus' in loaded(p).sc_dict['linyphiidae']['Leaves'])

        # -- 4. a file with no trailing newline -------------------------------
        p = fresh(src, tmp)
        data = open(p, encoding='utf-8').read().rstrip('\r\n')
        open(p, 'w', encoding='utf-8', newline='').write(data)
        sc = loaded(p)
        sc.addSpeciesLevel(Level.SPECIES, 'testus nonewline', '', '', 'linyphiidae')
        sc.flushSpeciesLearned([p])
        check("appending onto a file with no trailing newline keeps both rows",
              'testus nonewline' in loaded(p).sc_dict
              and len(namesInSpeciesCollection(p)) == base_rows + 1)

        # -- 5. nothing is written before the flush ---------------------------
        p = fresh(src, tmp)
        sc = loaded(p)
        sc.addSpeciesLevel(Level.SPECIES, 'testus buffered', '', '', 'linyphiidae')
        check("a learned species is NOT on file before the flush",
              len(namesInSpeciesCollection(p)) == base_rows)
        sc.flushSpeciesLearned([p])
        check("and is there afterwards, once",
              len(namesInSpeciesCollection(p)) == base_rows + 1)

        # -- 6. the master is required, and a failure keeps the buffer --------
        from gui.culling_species import flush_learned_species
        local = fresh(src, tmp)
        sc = loaded(local)
        sc._source_path = local
        sc.addSpeciesLevel(Level.SPECIES, 'testus unreachable', '', '', 'linyphiidae')
        import utils.config as ucfg
        ucfg._config.setdefault("master", {})
        try:
            ucfg._config.set("master", "masterbiotadatabase",
                             os.path.join(tmp, "no_such_master"))
        except Exception:
            ucfg._config["master"]["masterbiotadatabase"] = os.path.join(tmp, "no_such_master")
        ok, msg = flush_learned_species(sc)
        check("an unreachable master is an error", ok is False, (msg or "")[:60])
        check("...and the buffer is kept for the retry",
              'testus unreachable' in sc.learned)
        check("...and nothing was half-written locally",
              len(namesInSpeciesCollection(local)) == base_rows)

        # now point at a real master copy and retry
        master_root = os.path.join(tmp, "master", "metadata", "active")
        os.makedirs(master_root, exist_ok=True)
        master_csv = os.path.join(master_root, "speciescollection.csv")
        shutil.copy(src, master_csv)
        try:
            ucfg._config.set("master", "masterbiotadatabase", os.path.join(tmp, "master"))
        except Exception:
            ucfg._config["master"]["masterbiotadatabase"] = os.path.join(tmp, "master")
        ok, msg = flush_learned_species(sc)
        check("the retry saves it to BOTH master and local copy",
              ok and len(namesInSpeciesCollection(master_csv)) == base_rows + 1
              and len(namesInSpeciesCollection(local)) == base_rows + 1, msg)

        # -- 6b. a pipeline-loaded classification also reaches the trip --------
        # The species editor uses fpr.sc, which nothing had told where it came
        # from, so its flush reached only the master — and the next pipeline
        # step, reading the TRIP's list, looked the species up in GBIF again.
        local2 = fresh(src, tmp)
        sc = loaded(local2)                      # no _source_path set by hand
        sc.addSpeciesLevel(Level.SPECIES, 'testus pipeline', '', '', 'linyphiidae')
        ok, msg = flush_learned_species(sc)
        check("a pipeline-loaded classification knows its own file",
              getattr(sc, "_source_path", "") == local2)
        check("...so the flush reaches the trip copy too, not just the master",
              ok and len(namesInSpeciesCollection(local2)) == base_rows + 1
              and len(namesInSpeciesCollection(master_csv)) == base_rows + 2, msg)

        # -- 7b. an edit on the shared list mirrors into a trip's copy ---------
        # Edit Species List always edits the master; the trip's working copy is
        # brought along by appending just the touched rows.
        from engine.speciesclassification import appendRowsToSpeciesCollection
        p = fresh(src, tmp)
        sc = loaded(p)
        target = next(n for n, r in sc.sc_dict.items() if r['Level'] == Level.SPECIES)
        row = dict(sc.sc_dict[target])
        row['DutchName'] = 'gespiegelde naam'
        row['Level'] = str(row['Level'])
        appendRowsToSpeciesCollection(p, [row])
        check("an edited name mirrors into the other copy",
              loaded(p).sc_dict[target]['DutchName'] == 'gespiegelde naam', target)

        # -- 7. the negative cache is process-wide ----------------------------
        scmod._NAMES_NOT_FOUND.clear()
        scmod._NAMES_NOT_FOUND.add('definitely not a species')
        a, b = SpeciesClassification(None), SpeciesClassification(None)
        check("two classifications share the not-found cache",
              'definitely not a species' in scmod._NAMES_NOT_FOUND
              and not hasattr(a, 'names_not_found_list')
              and not hasattr(b, 'names_not_found_list'))

        print("\n" + "=" * 64)
        if failures:
            for f in failures:
                print(f"FAIL: {f}")
        else:
            print("PASS: species are learned once, buffered, and saved together.")
        return 1 if failures else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
