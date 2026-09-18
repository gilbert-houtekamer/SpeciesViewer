#!/usr/bin/env python3

# Process the EXIF metadata and analyze them per picture based on the UniquePictureName
import re
import csv
import os
import sys
import configparser
import time
import logging
import argparse
from datetime import datetime
from pathlib import Path

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import engine
import engine.projectlib
import engine.specieslib
import engine.speciesclassification
from engine.specieslib import Level
import engine.triplib
import engine.species_baseline
import engine.topics
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")
result_dict = {}
headers = []
counter = 1
newcounter = 1
total_species = 0
total_new = 0
topics_skipped = 0
jpgneeded = 0
reference_names = set()  # lowercase ScientificNames that are already in the master collection

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    read_ini(config, INI_LOCATION)
    return config

def scan_files(directory, search_string, report, sc=None):
    """Read each day's summary report and collect the species it lists.

    One column per day directory, in date order — a day that was never
    summarized keeps its column (all dots) instead of vanishing from the
    report. Returns (days_total, days_without_summary) so the caller can state
    the coverage: a partially-summarized trip yields a partial verdict, and
    that must be visible rather than merely plausible.
    """
    global topics_skipped
    print(f"File stats GPS and location data:\n\nFile           Count  NoGPS   Noloc  NoCity  NoSpec    Good     Bad  NoColor", file=report)
    days_total = 0
    days_missing = []
    records = sc.sc_dict if sc is not None else {}
    if not os.path.isdir(directory):
        return days_total, days_missing

    for name in sorted(os.listdir(directory)):
        day_dir = os.path.join(directory, name)
        if not os.path.isdir(day_dir):
            continue
        days_total += 1
        headers.append(name)
        # Only the day's own summary — never descend into frozen/.
        txt_files = sorted(f for f in os.listdir(day_dir)
                           if f.endswith('.txt')
                           and os.path.isfile(os.path.join(day_dir, f)))
        summarized = False
        for file in txt_files:
            file_path = os.path.join(day_dir, file)
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                NoGPS = 0
                NoLocation = 0
                NoCity = 0
                NoSpecies = 0
                Good = 0
                NotGood = 0
                NoColor = 0
                if search_string in content:
                    summarized = True
                    lines = content.split('\n')
                    for i, line in enumerate(lines):
                        # check counters
                        if "No GPS specified for" in line:
                            NoGPS = int(line[32:35])
                        if "No Location specified" in line:
                            NoLocation = int(line[32:35])
                        if "No City specified" in line:
                            NoCity = int(line[32:35])
                        if "No Species name specified" in line:
                            NoSpecies = int(line[32:35])
                        if "Interesting or Good quality" in line:
                            Good = int(line[32:35])
                        if "Duplicate or bad quality" in line:
                            NotGood = int(line[32:35])
                        if "No color label rating" in line:
                            NoColor = int(line[32:35])
                        if search_string in line:
                            # print summary
                            print(f"{name:12}  {Good+NotGood+NoColor:6} {NoGPS:6}  {NoLocation:6}  {NoCity:6}  {NoSpecies:6}  {Good:6}  {NotGood:6}  {NoColor:6}", file=report)
                            # get species info
                            for subsequent_line in lines[i+3:]:
                                if subsequent_line.strip():  # Check if the line is non-empty
                                    # numbers fixed
                                    numbers = re.findall(r'(\d+)', subsequent_line[0:30])
                                    # names not in same way
                                    tokens = re.split(r'[()/]', subsequent_line[31:])
                                    # Lowercased throughout: the taxonomy lookup
                                    # tests the lowered name, so the dict must be
                                    # keyed the same way or a capitalised name in
                                    # a summary raises KeyError further down.
                                    key = tokens[0].strip().lower()
                                    # Landscapes/topics come through the day
                                    # summaries looking exactly like species
                                    # ("2026"). Drop them here, at the door,
                                    # rather than relying on the taxonomy walk
                                    # further down to quietly not find them.
                                    if key and engine.topics.is_topic_node(records, tokens[0].strip()):
                                        topics_skipped += 1
                                        continue
                                    info = [name, numbers]
                                    if key in result_dict:
                                        result_dict[key].append(info)
                                    else:
                                        result_dict[key] = [info]
        if not summarized:
            days_missing.append(name)
    return days_total, days_missing

# create website based on current count setting, now to test iSpeciesThisRunCount
def scanSpecies(sc, speciesname, report):
    global counter, newcounter, jpgneeded
    global total_species, total_new
    global subheaderdone
    species = sc.sc_dict[speciesname]
    for leaf in species['Leaves']:
        species = sc.sc_dict[leaf]
        if str(species['Level']) == str(Level.SPECIES):
            # do we have this?
            scientific = species['ScientificName'].lower()
            if scientific in result_dict:
                best_rating = 0
                has_jpg = False
                if sc.sc_dict[sc.sc_dict[species['Up']]['Up']]['Up'] != 'aves' and not subheaderdone:
                    print(f"\nOther animals and plants:\n", file=report)
                    counter = 1
                    newcounter = 1
                    subheaderdone = True
                sci = species['ScientificName']
                if len(sci.split()) == 3:
                    parts = sci.split()
                    sci = f"{parts[0]} {parts[1]} ssp. {parts[2]}"
                english = species['EnglishName'] if species['EnglishName'] else f"*{sci}*"
                dutch = species['DutchName'] if species['DutchName'] else f"*{sci}*"
                data_line = str(counter).rjust(3) + ' ' + english.ljust(32) + dutch.ljust(40)
                if scientific in reference_names:
                    data_line += '     '
                else:
                    data_line += str(newcounter).rjust(3) + '  '
                    newcounter += 1
                    total_new += 1
                counter += 1
                # counter/newcounter restart at the "Other animals and plants"
                # subheader; these two never do, so the totals below cover the
                # whole trip and can be read back without column arithmetic.
                total_species += 1
                seen = 0
                # run by file for which we have data
                days_line = ""
                for header in headers:
                    # run through the list to see if we have this list
                    summary = ''
                    for info in result_dict[scientific]:
                        if info[0] == header:
                    # if header in result_dict[species['ScientificName']][0]:
                    #     info = result_dict[species['ScientificName']][header]
                            if info[1][1] > '0':        # JPGs
                                has_jpg = True
                                if info[1][2] > '0':    # best rating
                                    summary = str(info[1][2]) + '  '
                                    if int(info[1][2]) > best_rating: best_rating = int(info[1][2])
                                else:                   # JPGs, but none rated
                                    summary = 'j  '
                                seen += 1
                            elif int(info[1][0]) > 0:   # RAWs
                                if not any(c for c in days_line if c != '.' and not c.isspace()):
                                    summary = 'F  '
                                    jpgneeded += 1
                                else:
                                    summary = 'x  '
                                seen += 1                                                       
                            break
                    if summary != "":
                        days_line += summary
                    else:
                        days_line += '.  '

                data_line += f"{days_line} {seen:3} "               
                if best_rating > 0:
                    data_line += f" {best_rating:3}"
                elif has_jpg:
                    # A JPG exists, it just carries no stars. In the
                    # colours-only culling workflow that is normal (Blue/Purple
                    # and unlabelled keeps score zero), and the picture is there
                    # to publish - so this is NOT "No JPG". No JPG means no JPG
                    # at all: only RAW or HEIC.
                    data_line += '   j'
                elif scientific not in reference_names:
                    data_line += '   No JPG     '
                print(data_line, file=report)
        else:
            scanSpecies(sc, leaf, report)
    return

# caller
def print_headers(sc, report):
    global subheaderdone
    print(f'\n\n', file=report)
    print(f'Legend per day:  F = first day, RAW only (no JPG yet)  x = RAW only (JPG exists on earlier day)', file=report)
    print(f'                 j = JPG exists but unrated            2..5 = best star rating that day', file=report)
    print(f'Summary:         # = days seen, Best = overall best rating (j = JPG exists but unrated)', file=report)
    print(f'                 No JPG = new species with no JPG at all yet, only RAW or HEIC\n', file=report)
    counter = 0
    header_line = ' '.ljust(36+40) + 'New '
    for header in headers:
        header_line += header[-2:] + ' '
    header_line += '    #    Best'
    print(header_line, file=report)

    subheaderdone = False
    scanSpecies(sc, 'biota', report)

    return

def main(args, config):
    global starttime, SpeciesDebug, reference_names
    global result_dict, headers, counter, newcounter, jpgneeded
    global total_species, total_new, topics_skipped
    result_dict = {}
    headers = []
    counter = 1
    newcounter = 1
    total_species = 0
    total_new = 0
    topics_skipped = 0
    jpgneeded = 0
    reference_names = set()
    starttime = time.time()
    SpeciesDebug = 0        # default
    SpeciesDebug = int(config.get("debug","SpeciesFound"))

    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"),
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))


    # get repository-wide sc and per project sp.
    sc = engine.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)

    MyProject.initMasterAndTrip(config, inittrip=False)

    # read the list with all pre-defined species, as updated in each project using it
    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)

    # Build the "already known" reference set — the species you had already
    # photographed before this trip. speciescollection.csv was tried early on but
    # pre-loads most birds at startup, so a bird photographed for the first time
    # would still register as "known" and never surface as new.
    #
    # THE DATE RULE is the real answer: a species is already known when the
    # master holds a picture of it taken BEFORE this trip started. It does not
    # care when the trip repo was made or in what order trips were merged, and
    # the trip's own pictures fall inside its date range, so they never enter
    # its own baseline — the count survives the trip's own merge.
    #
    # The two fallbacks below are strictly worse and both say so in the report.
    # The frozen baseline file is right only if no older trip was merged after
    # this trip was set up; the live master is right only until this trip is
    # merged, after which every lifer silently stops being new.
    reference_note = ""
    fromdate = ""
    if MyProject.Trip:
        try:
            from utils.trip_config import read_trip_ini
            fromdate = (read_trip_ini(MyProject.getRepositoryPath()) or {}).get("fromdate", "")
        except Exception as e:
            logger.warning(f"svr2CountSpecies: could not read trip.ini: {e}")

    dated_names = None
    if MyProject.Trip and fromdate:
        dated_names, dated_topics = engine.species_baseline.collect_species_before(
            MyProject.getMasterRepository(), fromdate, sc_records=sc.sc_dict)
    if dated_names:
        reference_names = dated_names
        reference_note = (f"Lifer baseline: everything the master has a picture of from "
                          f"before {fromdate[:10]}, the trip's start date "
                          f"({len(reference_names)} species). Recomputed on every run, so "
                          f"merging this trip — or an older one — cannot change it.")
    else:
        baseline_file = (engine.species_baseline.baseline_path(MyProject.getRepositoryPath())
                         if MyProject.Trip else None)
        if baseline_file and os.path.exists(baseline_file):
            reference_names, meta = engine.species_baseline.read_baseline(baseline_file)
            origin = meta.get("reconstructed_from", "")
            how = (f"reconstructed afterwards from {origin}" if origin
                   else "frozen at trip creation")
            reference_note = (f"Lifer baseline: master as of {meta.get('taken_at', 'unknown date')}, "
                              f"{how} ({len(reference_names)} species). No trip start date "
                              f"available, so an older trip merged after this one was set up "
                              f"would show up as new here.")
        else:
            if MyProject.Trip:
                ref_csv = os.path.join(MyProject.getMasterRepository(),
                                       "metadata", "active", "species.csv")
            else:
                ref_csv = config.get("repository", "ReferenceSpecies")
            if os.path.exists(ref_csv):
                names, _zero, _topic = engine.species_baseline.collect_species_from_csv(ref_csv)
                reference_names = names
                reference_note = (f"Lifer baseline: the master AS IT IS TODAY "
                                  f"({len(reference_names)} species). Species already merged "
                                  f"into the master do not count as new.")
            else:
                logger.warning(f"svr2CountSpecies: reference file not found: {ref_csv}")
                reference_note = ("Lifer baseline: NONE FOUND — every species below is "
                                  "reported as new. Do not trust the New column.")

    # make output file
    reports_dir = Path(config.get("repository","BiotaDatabase")) / "metadata" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / "Species overview.txt"
    with open(report_path, "w", encoding="utf-8") as report:
        trip_title = MyProject.Trip.getTripName() if MyProject.Trip else "master"
        print(f"Species overview for {trip_title}", file=report)
        print(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}", file=report)
        print(reference_note, file=report)

         # Collect species
        directory_to_scan = os.path.join(MyProject.getMetaDir(), 'review')
        search_string = 'Species specified in this dir:'
        days_total, days_missing = scan_files(directory_to_scan, search_string, report, sc)

        # A day that was never summarized contributes no species at all. Say so
        # here rather than letting the report look complete.
        summarized = days_total - len(days_missing)
        print(f"\nDays: {summarized} of {days_total} summarized.", file=report)
        if days_missing:
            print(f"NOT summarized (no species counted for these days): "
                  f"{', '.join(days_missing)}", file=report)

        # print in order classification
        report.write("\nThe species found below, showing birds first.")
        print_headers(sc, report)
        if jpgneeded:
            print(f"\nThere are {jpgneeded} species that have no JPGs on the first occurrence of that species.", file=report)
        # The machine-readable bottom line, and the one worth reading first.
        # engine/trip_verdict.py reads it back rather than re-deriving the
        # counts from the report's fixed-width columns.
        print(f"\nTotals: {total_species} species, {total_new} new.", file=report)
        if topics_skipped:
            print(f"({topics_skipped} landscape/topic entries were ignored — they are "
                  f"not species.)", file=report)

    # print(f'{len(species_dict)} lines written for species data to "{config.get("directories","Tables").replace('"', '').replace("'", '')+'species.csv'}".')
    totaltime = time.time() - starttime      
    logger.info(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="svr2CountSpecies")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Module Configuration File",
    )
    parser.add_argument(
        "--dir", type=str, default=os.path.join(modpath, "data"), help="foto directory"
    )
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = args.config
    config = configparser.ConfigParser()
    read_ini(config, INI_LOCATION)
    # init the logging subsystem
    logging.basicConfig(level=logging.INFO)
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
    else:
        logger.info(f"{parser.description} succeeded")
