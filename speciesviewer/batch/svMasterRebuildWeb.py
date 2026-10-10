"""
svMasterRebuildWeb — force a FULL re-render of every master website page, then upload.

Why this exists
---------------
Normal master builds (sv5BuildWebsite) render in "New" mode: only taxa/species
that gained pictures this run are re-rendered. That is right for incremental
publishing, but it means a change to a SHARED TEMPLATE (e.g. the species search
box added to allspecies-*/vogelordepage-*) reaches the master only piecemeal —
a family page updates only the next time that family happens to gain a photo.

This batch re-renders EVERY published taxon/species page (force_all) and uploads
the biota tree, so a template change lands everywhere at once.

What it deliberately does NOT do
--------------------------------
It is HTML-only and publish-only:
  * getSkipJPGs()=True during render — no full-size JPGs are copied into frozen;
    the master's biota JPGs already exist on the server, and cachedUpload skips
    unchanged files. We are refreshing HTML, not re-shipping images.
  * It NEVER freezes or commits data (no sv6Freeze / sv7 commitProjectFiles).
    The master's active CSVs are untouched — this only re-renders and uploads
    web pages. See project_species_csv_incident / project_ghost_row_incident for
    why we keep website re-render strictly separate from data commit.

Output routing is the standard "New" → ReviewFrozen()/biota; force_all only
changes SELECTION, not where pages land, so the existing upload path
(sv7WriteCommit.putRecursiveTreeWeb, which ships frozen/biota) is used unchanged.

That upload path also refreshes 'includes web/animals.css' + the map library
(sv7WriteCommit.putSharedWebAssets). It has to: the master's stylesheet was
written once when the master repository was created and no rebuild ever replaced
it, so a CSS fix reached every album (svv4BuildWebsite uploads it) but never the
master site itself.
"""

import argparse
import configparser
import os
import sys
import time
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine
import engine.projectlib
import engine.speciesclassification
import engine.specieslib
from engine.specieslib import Level
import engine.statemachine
import batch.sv7WriteCommit as sv7WriteCommit
from batch.sv5BuildWebsite import makeIntroPages
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

logger = logging.getLogger("speciesviewer")


def main(args, config):
    starttime = time.time()

    MyProject = engine.projectlib.Project(config.get("repository", "BiotaDatabase"),
                                          config.get("project", "PictureDirectory"),
                                          config.get("repository", "BiotaWebRoot"))
    MyProject.initMasterAndTrip(config, inittrip=True)

    # In master_mode there is no [trip] section, so MyProject.Trip is None (→ correct
    # "Alle soorten" master breadcrumbs). But projectName still defaults to the active
    # trip's name (PictureDirectory stem), which would land the rebuilt pages under
    # review/<trip>/. Pin a dedicated name so the local frozen tree is isolated at
    # <master>/metadata/review/master_rebuild/frozen.
    MyProject.projectName = "master_rebuild"

    sc = engine.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)

    # Read the FULL committed master collection from active/ — NOT readProject(),
    # which reads the species list from review/<projectName>/species.csv (the subset
    # for a trip being ingested, which does not exist for a standalone rebuild).
    MyProject.readMasterCommitted()

    # No recognized species → no 'biota' root; nothing to rebuild.
    if 'biota' not in MyProject.sp.species_dict:
        logger.info("svMasterRebuildWeb: no recognized species — nothing to rebuild.")
        return engine.statemachine.nothing_to_do("no species — nothing to rebuild.")

    # HTML-only: do not copy full-size JPGs into frozen (they already exist on
    # the server; the small-JPG resize/upload in putRecursiveTreeWeb still runs
    # and is cache-skipped for unchanged files).
    # Rough species-page total for the progress heartbeat (an upper bound: not
    # every species with pictures ends up with a renderable page, but close enough
    # to give the multi-minute render a sense of scale).
    species_total = sum(
        1 for s in MyProject.sp.species_dict.values()
        if s.get('Level') == Level.SPECIES
        and (int(s['iPicturesWithJPGsThisRun']) + int(s['iPicturesWithJPGsCollection'])) > 0
    )

    saveSkipJPGs = MyProject.getSkipJPGs()
    MyProject.setSkipJPGs(True)
    try:
        for language in ('nl', 'en'):
            MyProject.setLanguage(language)
            counter = {'n': 0}

            def _tick(lang=language, c=counter):
                c['n'] += 1
                if c['n'] % 250 == 0:
                    logger.info(f"  {lang.upper()}: rendered {c['n']} / ~{species_total} species pages…")

            # force_all=True: re-render every published taxon/species, not just
            # those with new pictures this run. Output stays in frozen/ ("New").
            MyProject.makeWebDirectories('biota', 'New', force_all=True, progress_cb=_tick)
            MyProject.makeAllSpeciesPage('biota', language)
            logger.info(f"  Re-rendered all {language.upper()} pages ({counter['n']} species + taxa).")
        makeIntroPages(MyProject)
    finally:
        MyProject.setSkipJPGs(saveSkipJPGs)

    logger.info(f"svMasterRebuildWeb: local re-render complete in {time.time() - starttime:.1f}s. Uploading…")

    # Upload only — the same publish path sv7 uses (frozen/biota + summary +
    # intro + small JPGs). Does NOT commit any data CSVs.
    sv7WriteCommit.putRecursiveTreeWeb(MyProject, config)

    logger.info(f"✅ svMasterRebuildWeb complete in {time.time() - starttime:.1f}s.")
    return "Successfully run."


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="svMasterRebuildWeb")
    parser.add_argument("--config", type=str, default=str(svconfig_path()))
    cli_args = parser.parse_args()
    cfg = configparser.ConfigParser()
    read_ini(cfg, cli_args.config)
    main(cli_args, cfg)
