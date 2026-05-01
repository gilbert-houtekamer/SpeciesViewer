#!/usr/bin/env python3

# Read the potential species list region
import re
import csv
import os
import sys
import configparser
import time
import logging
import argparse
from datetime import datetime, timedelta
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Alignment, Border, Side, Font
from openpyxl.utils import get_column_letter
from openpyxl.formula.translate import Translator

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import ghexif
import ghexif.projectlib
import ghexif.specieslib
import ghexif.speciesclassification
from ghexif.specieslib import Level
import ghexif.triplib

logger = logging.getLogger("speciesviewer")
avibase_dict = {}
headers = []
counter = 1
newcounter = 1
jpgneeded = 0
Reference_sp = None

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    return config

# veel selectie mogelijkheden
# https://avibase.bsc-eoc.org/checklist.jsp?lang=EN
# voorbeeld brazilie:
# https://avibase.bsc-eoc.org/checklist.jsp?lang=EN&p2=1&list=ioc&synlang=NL&region=BRms&version=text&lifelist=&highlight=0
def readAviBase(triproot, filename):
    #file_path = "C:/Users/gilbe/OneDrive/reizen/gambia/soorten.csv"
    #file_path = "C:/Users/gilbe/OneDrive/reizen/madagascar/avibase.csv"
    file_path  = os.path.join(triproot, filename)
    number = 1
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read().split("\n")
        for lines in content:
            tokens = lines.split(";")
            try:
                if tokens[0] == '\ufeffColumn1':
                    continue
                    # header line, ignore it
                elif tokens[0] == '' or tokens[0] == ' ':
                    continue
                elif tokens[1] not in ['Wet','']:
                    listspecies = {}
                    listspecies['nlname'] = tokens[2]
                    listspecies['enname'] = tokens[0]      # column 0 is the dict key (e.g. species number)
                    listspecies['english'] = ''             # actual English name, filled by addSpeciesInfo
                    listspecies['scname'] = tokens[1]
                    listspecies['status'] = tokens[3]
                    if tokens[1] not in ["Wet", ""]:
                        listspecies['number'] = number
                        number += 1 
                    elif tokens[1] == "Wet":
                        listspecies['number'] = "Wet"                   
                    else:
                        listspecies['number'] = ""
                    listspecies['gezien'] = 0
                    listspecies['foto'] = 0
                    listspecies['knownspecies'] = False
                    avibase_dict[listspecies['enname']] = listspecies
                elif tokens[0] not in ['\ufeffEngels']:
                    listspecies = {}
                    listspecies['number'] = -1
                    listspecies['enname'] = tokens[0]
                    listspecies['scname'] = ""
                    listspecies['status'] = ""                
                    avibase_dict[listspecies['enname']] = listspecies            
            except:
               pass
    return

def addSpeciesInfo(sc,sp):
    for enname in avibase_dict:
        listspecies = avibase_dict[enname]
        original_scname = listspecies.get('scname', '')

        if listspecies['scname'] != "":     # exclude headers
            if enname.lower() in sc.english_dict:
                listspecies['scname'] = sc.english_dict[enname.lower()]
                if listspecies['scname'] in sp.species_dict:
                    listspecies['knownspecies'] = True
            else:
                if listspecies['nlname'].lower() == "":
                    if listspecies['scname'].lower() in sc.sc_dict:
                        listspecies['nlname'] = sc.sc_dict[listspecies['scname'].lower()]['DutchName']
                if listspecies['nlname'].lower() in sc.dutch_dict:
                    listspecies['scname'] = sc.dutch_dict[listspecies['nlname'].lower()]
                    if listspecies['scname'] in sp.species_dict:
                        listspecies['knownspecies'] = True
        # Fill the actual English name from the master classification when we
        # have a scientific name (the source list often only carries Dutch +
        # Scientific). Keeps the dict key (enname) free for things like a
        # species number that the user reads off in the field.
        if listspecies.get('scname') and not listspecies.get('english'):
            sc_entry = sc.sc_dict.get(listspecies['scname'].lower())
            if sc_entry and sc_entry.get('EnglishName'):
                listspecies['english'] = sc_entry['EnglishName']
        # Source lists often carry outdated scientific names. When the
        # English/Dutch lookup produced a different canonical scientific name,
        # log it so the user can correct the source list.
        if original_scname and listspecies.get('scname') and \
           listspecies['scname'].lower() != original_scname.lower():
            label = listspecies.get('nlname') or listspecies.get('english') or enname
            logger.info(f"Scientific name updated for '{label}': "
                        f"'{original_scname}' → '{listspecies['scname']}'")
    return


# caller
def createRawCSV(temp_csv_file):
    groep = 0
    fotos = 0
    nieuwe = 0

    with open(temp_csv_file, 'w', encoding='utf-8') as f:
        print("No;Engels;NL;Wet;seen?;New?;Known;Photo;Notes", file=f)

        for enname in avibase_dict:
            listspecies = avibase_dict[enname]
            if listspecies['number'] == -1:
                line = f"{listspecies['enname']}"
            else:
                english_display = listspecies.get('english') or listspecies['enname']
                line = f"{listspecies['number']};{english_display};{listspecies['nlname']};{listspecies['scname']};"
                if listspecies['gezien'] != 0:
                    line += "Yes;"
                    groep += 1
                else:
                    line += ";"
                if listspecies['knownspecies']:
                    line += ';Known;'
                elif not listspecies['knownspecies'] and listspecies['gezien'] != 0:
                    line += 'New;;'
                else:
                    line += ';;'
                line += f";{listspecies['status']};"                     
            print(line, file=f)

        f.close()
    return

# CSV column indices (0-based) emitted by createRawCSV
CSV_NO, CSV_EN, CSV_NL, CSV_WET = 0, 1, 2, 3
CSV_SEEN, CSV_NEW, CSV_KNOWN, CSV_PHOTO, CSV_NOTES = 4, 5, 6, 7, 8


def _build_column_layout(csv_header, start_date, num_days,
                         phone_layout, first_column_lang):
    """Return the ordered list of Excel columns. Each column dict carries:
       header, kind, csv_idx (when sourced from CSV), width, and for date
       columns also weekend/date_obj/day_index. The renderer walks this list.
       kind: 'csv' | 'csv_int' | 'count_dates' | 'new_flag' | 'date' |
             'is_new_helper' | 'cum_helper' (the helpers are hidden)."""
    no_col    = {'header': csv_header[CSV_NO],    'kind': 'csv_int',     'csv_idx': CSV_NO,    'width': 5}
    en_col    = {'header': csv_header[CSV_EN],    'kind': 'csv',         'csv_idx': CSV_EN,    'width': 24}
    nl_col    = {'header': csv_header[CSV_NL],    'kind': 'csv',         'csv_idx': CSV_NL,    'width': 24}
    wet_col   = {'header': csv_header[CSV_WET],   'kind': 'csv',         'csv_idx': CSV_WET,   'width': 24}
    seen_col  = {'header': csv_header[CSV_SEEN],  'kind': 'count_dates', 'csv_idx': CSV_SEEN,  'width': 5}
    new_col   = {'header': csv_header[CSV_NEW],   'kind': 'new_flag',    'csv_idx': CSV_NEW,   'width': 5}
    known_col = {'header': csv_header[CSV_KNOWN], 'kind': 'csv',         'csv_idx': CSV_KNOWN, 'width': 8}
    photo_col = {'header': csv_header[CSV_PHOTO], 'kind': 'csv',         'csv_idx': CSV_PHOTO, 'width': 6}
    notes_col = {'header': csv_header[CSV_NOTES], 'kind': 'csv',         'csv_idx': CSV_NOTES, 'width': 12}

    date_cols = []
    for i in range(num_days):
        d = start_date + timedelta(days=i)
        date_cols.append({
            'header':    d.strftime("%d/%m/%Y"),
            'kind':      'date',
            'width':     5,
            'weekend':   d.weekday() >= 5,
            'date_obj':  d,
            'day_index': i,
        })

    # Hidden helper columns. is_new = 1 when species is not yet "Known";
    # cum_k counts ticks across day_1..day_k for that species. The totals
    # rows reference these helpers to compute Day New and the trip-wide
    # cumulative species/new counts without needing array formulas.
    helpers = [{'header': '_is_new', 'kind': 'is_new_helper', 'width': 5}]
    for i in range(num_days):
        helpers.append({
            'header':    f'_cum_{i+1}',
            'kind':      'cum_helper',
            'day_index': i,
            'width':     5,
        })

    if phone_layout:
        # Phone-friendly: seq# + chosen-language name pinned at the very
        # left (both freeze with the row), then the day ticks, then the
        # remaining language columns and the rest of the metadata.
        lang_lookup = {'nl': nl_col, 'en': en_col}
        primary = lang_lookup.get(first_column_lang, nl_col)
        other_langs = [c for c in (en_col, nl_col, wet_col) if c is not primary]
        return ([no_col, primary]
                + date_cols
                + other_langs
                + [seen_col, new_col, known_col, photo_col, notes_col]
                + helpers)

    # Standard desktop layout — original ordering, dates last.
    return ([no_col, en_col, nl_col, wet_col,
             seen_col, new_col, known_col, photo_col, notes_col]
            + date_cols
            + helpers)


def create_excel_checklist(csv_path, xlsx_path, start_date, num_days,
                           phone_layout=False, first_column_lang="nl",
                           trip_name=""):
    # Load CSV
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter=';')
        rows = list(reader)

    csv_header = rows[0]
    data = rows[1:]

    columns = _build_column_layout(csv_header, start_date, num_days,
                                   phone_layout, first_column_lang)

    # Resolve the Excel column index for each csv_idx we'll reference from
    # formulas / fills. Returned indices are 1-based (Excel convention).
    def excel_col_for_csv(csv_idx):
        for i, col in enumerate(columns, 1):
            if col.get('csv_idx') == csv_idx:
                return i
        return None

    seen_col_idx      = excel_col_for_csv(CSV_SEEN)
    new_col_idx       = excel_col_for_csv(CSV_NEW)
    known_col_idx     = excel_col_for_csv(CSV_KNOWN)
    photo_col_idx     = excel_col_for_csv(CSV_PHOTO)
    date_col_idxs     = [i for i, c in enumerate(columns, 1) if c['kind'] == 'date']
    is_new_helper_idx = next((i for i, c in enumerate(columns, 1)
                              if c['kind'] == 'is_new_helper'), None)
    cum_helper_idxs   = [None] * num_days
    for i, c in enumerate(columns, 1):
        if c['kind'] == 'cum_helper':
            cum_helper_idxs[c['day_index']] = i
    # First helper column = end of the visible block (used for autofilter
    # range and border painting so we don't drag the hidden columns in).
    first_helper_col  = next((i for i, c in enumerate(columns, 1)
                              if c['kind'] in ('is_new_helper', 'cum_helper')),
                             len(columns) + 1)
    visible_last_col  = first_helper_col - 1

    # Layout —
    #   row 1 : trip-banner title
    #   rows 2..5 : Day Totals / Day New / Trip Totals / Trip New
    #   row 6 : blank separator
    #   row 7 : column headers (bold)
    #   row 8+ : data
    # Keeping totals above a blank gap means Excel's autofilter on the
    # header row leaves the banner + totals untouched.
    TITLE_ROW           = 1
    DAY_TOTALS_ROW      = 2
    DAY_NEW_ROW         = 3
    TRIP_TOTALS_ROW     = 4
    TRIP_NEW_ROW        = 5
    HEADER_ROW          = 7
    DATA_START_ROW      = 8
    last_data_row       = DATA_START_ROW + len(data) - 1

    wb = Workbook()
    ws = wb.active
    ws.title = "Checklist"

    weekend_fill  = PatternFill(start_color="D3D3D3", end_color="D3D3D3", fill_type="solid")
    grey_row_fill = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")
    unseen_fill   = PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid")

    # Header row
    header_font = Font(bold=True)
    for col_idx, col in enumerate(columns, 1):
        cell = ws.cell(row=HEADER_ROW, column=col_idx, value=col['header'])
        cell.font = header_font
        if col['kind'] == 'date':
            cell.alignment = Alignment(horizontal="left", vertical="center")
            if col.get('weekend'):
                cell.fill = weekend_fill
        else:
            cell.alignment = Alignment(horizontal="center", vertical="center")

    # Data rows
    for offset, row in enumerate(data):
        excel_row = DATA_START_ROW + offset
        # A row coming from the CSV with only one field is a section /
        # group header (createRawCSV emits just the enname with no ';').
        is_section_row = len(row) < 2 or not row[CSV_EN].strip()

        if is_section_row:
            title = row[0] if row else ''
            ws.cell(row=excel_row, column=1, value=title)
            for col_idx in range(1, len(columns) + 1):
                ws.cell(row=excel_row, column=col_idx).fill = grey_row_fill
            continue

        for col_idx, col in enumerate(columns, 1):
            cell = ws.cell(row=excel_row, column=col_idx)
            if col['kind'] == 'date':
                cell.number_format = "0"
                if col.get('weekend'):
                    cell.fill = weekend_fill
            elif col['kind'] == 'csv_int':
                v = row[col['csv_idx']] if col['csv_idx'] < len(row) else ''
                try:
                    cell.value = int(v) if v else v
                except (ValueError, TypeError):
                    cell.value = v
            elif col['kind'] == 'csv':
                v = row[col['csv_idx']] if col['csv_idx'] < len(row) else ''
                cell.value = v
            # 'count_dates' / 'new_flag' filled in as formulas below.

        # Light-green tint for species the user has not yet seen — matches
        # the Extramadura sheet style (column A intentionally left clean).
        known_value = row[CSV_KNOWN].strip() if len(row) > CSV_KNOWN else ''
        if known_value != 'Known':
            for col_idx in range(2, visible_last_col + 1):
                col = columns[col_idx - 1]
                if col['kind'] == 'date' and col.get('weekend'):
                    continue  # keep weekend grey readable
                ws.cell(row=excel_row, column=col_idx).fill = unseen_fill

    # Per-row formulas: visible "seen?" / "New?" plus the hidden helpers
    # used by the totals rows.
    if date_col_idxs and seen_col_idx and known_col_idx and new_col_idx:
        first_date_letter = get_column_letter(date_col_idxs[0])
        last_date_letter  = get_column_letter(date_col_idxs[-1])
        seen_letter       = get_column_letter(seen_col_idx)
        known_letter      = get_column_letter(known_col_idx)
        for excel_row in range(DATA_START_ROW, last_data_row + 1):
            row = data[excel_row - DATA_START_ROW]
            if len(row) < 2 or not row[CSV_EN].strip():
                continue  # section header
            ws.cell(row=excel_row, column=seen_col_idx,
                    value=f'=COUNT({first_date_letter}{excel_row}:{last_date_letter}{excel_row})')
            ws.cell(row=excel_row, column=new_col_idx,
                    value=f'=IF(AND({known_letter}{excel_row}<>"Known", {seen_letter}{excel_row}>0), "New", "")')
            if is_new_helper_idx:
                ws.cell(row=excel_row, column=is_new_helper_idx,
                        value=f'=IF({known_letter}{excel_row}<>"Known", 1, 0)')
            for k, cum_idx in enumerate(cum_helper_idxs):
                if not cum_idx:
                    continue
                day_k_letter = get_column_letter(date_col_idxs[k])
                ws.cell(row=excel_row, column=cum_idx,
                        value=f'=COUNT({first_date_letter}{excel_row}:{day_k_letter}{excel_row})')

    # ── Title banner (row 1) and totals area (rows 2..5) ────────────────
    title_text = (f"Total counters for {trip_name}" if trip_name
                  else "Total counters")
    title_cell = ws.cell(row=TITLE_ROW, column=1, value=title_text)
    title_cell.font = Font(bold=True)
    ws.cell(row=DAY_TOTALS_ROW,  column=1, value="Day Totals")
    ws.cell(row=DAY_NEW_ROW,     column=1, value="Day New")
    ws.cell(row=TRIP_TOTALS_ROW, column=1, value="Trip Totals")
    ws.cell(row=TRIP_NEW_ROW,    column=1, value="Trip New")

    is_new_letter = get_column_letter(is_new_helper_idx) if is_new_helper_idx else None
    is_new_range  = (f'{is_new_letter}{DATA_START_ROW}:{is_new_letter}{last_data_row}'
                     if is_new_letter else None)

    for k, day_col_idx in enumerate(date_col_idxs):
        day_letter = get_column_letter(day_col_idx)
        day_range  = f'{day_letter}{DATA_START_ROW}:{day_letter}{last_data_row}'
        # Day Totals — number of species ticked on day k.
        ws.cell(row=DAY_TOTALS_ROW, column=day_col_idx,
                value=f'=COUNT({day_range})')
        # Day New — of the species ticked on day k, how many are "new"
        # (not yet marked Known) — i.e. how many greens.
        if is_new_range:
            ws.cell(row=DAY_NEW_ROW, column=day_col_idx,
                    value=f'=COUNTIFS({is_new_range}, 1, {day_range}, ">0")')
        # Trip cumulative — uses the per-day cum_k helper.
        cum_idx = cum_helper_idxs[k] if k < len(cum_helper_idxs) else None
        if cum_idx:
            cum_letter = get_column_letter(cum_idx)
            cum_range  = f'{cum_letter}{DATA_START_ROW}:{cum_letter}{last_data_row}'
            ws.cell(row=TRIP_TOTALS_ROW, column=day_col_idx,
                    value=f'=COUNTIF({cum_range}, ">0")')
            if is_new_range:
                ws.cell(row=TRIP_NEW_ROW, column=day_col_idx,
                        value=f'=COUNTIFS({is_new_range}, 1, {cum_range}, ">0")')

    # Trip-wide aggregates in the seen?/New?/Photo columns. The seen? and
    # New? Excel cells hold strings ("New"/"") and a formula result, so
    # COUNTIF(..., ">0") doesn't match them — instead just point to the
    # last-day cumulative cell (which already equals the trip-wide count).
    # Photo is user-filled with numeric markers so COUNTIF still applies.
    if date_col_idxs:
        last_day_letter = get_column_letter(date_col_idxs[-1])
        if seen_col_idx:
            ws.cell(row=TRIP_TOTALS_ROW, column=seen_col_idx,
                    value=f'={last_day_letter}{TRIP_TOTALS_ROW}')
        if new_col_idx:
            ws.cell(row=TRIP_NEW_ROW, column=new_col_idx,
                    value=f'={last_day_letter}{TRIP_NEW_ROW}')
    if photo_col_idx:
        photo_letter = get_column_letter(photo_col_idx)
        ws.cell(row=TRIP_TOTALS_ROW, column=photo_col_idx,
                value=f'=COUNTIF({photo_letter}{DATA_START_ROW}:'
                      f'{photo_letter}{last_data_row}, ">0")')

    # Column widths + hide helper columns.
    for col_idx, col in enumerate(columns, 1):
        letter = get_column_letter(col_idx)
        ws.column_dimensions[letter].width = col['width']
        if col['kind'] in ('is_new_helper', 'cum_helper'):
            ws.column_dimensions[letter].hidden = True

    # Borders — keep the original ~385-row printable grid so the user's
    # print template is not surprised by a shorter species list. Skip the
    # hidden helper columns (no need for borders no one will see).
    last_row = max(last_data_row, 385)
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin'),
    )
    for row in ws.iter_rows(min_row=1, max_row=last_row,
                            min_col=1, max_col=visible_last_col):
        for cell in row:
            cell.border = thin_border

    # Activate Excel autofilter on the header row over the visible block.
    # The four totals rows sit above the filter range so they stay
    # visible (and unaffected) regardless of filtering.
    ws.auto_filter.ref = (
        f"A{HEADER_ROW}:{get_column_letter(visible_last_col)}{last_data_row}"
    )

    # Freeze the totals + header rows. In phone layout pin both the
    # sequence number and the chosen-language name (guides refer to
    # species by number); in standard layout pin all 4 leading info cols.
    freeze_col = 3 if phone_layout else 5
    ws.freeze_panes = ws.cell(row=DATA_START_ROW, column=freeze_col)

    wb.save(xlsx_path)

def main(args, config):
    global starttime, SpeciesDebug, Reference_sp
    starttime = time.time()
    SpeciesDebug = 0        # default   
    SpeciesDebug = int(config.get("debug","SpeciesFound"))

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    
    
    # get repository-wide sc and per project sp.
    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = ghexif.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)   

    # reference project
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)
    if MyProject.Trip:
        referenceSpecies = os.path.join(MyProject.getMasterRepository(), 'metadata/active/species.csv')
    else:
        referenceSpecies = config.get("repository","ReferenceSpecies")
    Reference_sp = ghexif.specieslib.Species(None)
    Reference_sp.readSpeciesInfo(referenceSpecies, False)
    # test 
    speciesok = Reference_sp.species_dict['biota']

    # read the list with all pre-defined species, as updated in each project using it
    metadatadir = MyProject.getMetaDir()
    sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)

    # get date from trip file
    if MyProject.Trip:
        # Source list format: 'avibase' (English in column 0) or
        # 'birdingbreaks' (sequence number in column 0). Both currently parse
        # the same way — column 0 is the dict key, column 1 scientific,
        # column 2 Dutch — but the choice is logged so source-format quirks
        # can be diagnosed and the dispatch is in place for future readers.
        source_format = config.get("avibase", "source_format", fallback="avibase")
        logger.info(f"Reading AviBase source list using '{source_format}' format.")
        phone_layout = config.getboolean("avibase", "phone_layout", fallback=False)
        first_column_lang = config.get("avibase", "first_column_lang", fallback="nl").strip().lower()
        if first_column_lang not in ("nl", "en"):
            first_column_lang = "nl"
        readAviBase(MyProject.Repository, "avibase.csv")
        addSpeciesInfo(sc, Reference_sp)
        temp_csv_file = os.path.join(MyProject.Repository, "soorten-temp.csv")
        createRawCSV(temp_csv_file)
        trip_name = MyProject.Trip.tripname
        # Filesystem-safe trip name for the output file
        safe_trip_name = re.sub(r'[<>:"/\\|?*]+', '-', trip_name).strip(' .-')
        if not safe_trip_name:
            safe_trip_name = "avibase"
        xlsx_path = os.path.join(MyProject.Repository,
                                 f"{safe_trip_name}-checklist.xlsx")

        fromdate_str = MyProject.Trip.trip_dict[trip_name]['fromdate']
        todate_str = MyProject.Trip.trip_dict[trip_name]['todate']
        # Convert to datetime objects
        start_date = datetime.strptime(fromdate_str, "%Y-%m-%d")
        end_date = datetime.strptime(todate_str, "%Y-%m-%d")

        # Calculate number of days (inclusive)
        num_days = (end_date - start_date).days + 1

        create_excel_checklist(temp_csv_file, xlsx_path, start_date, num_days,
                               phone_layout=phone_layout,
                               first_column_lang=first_column_lang,
                               trip_name=trip_name)
        logger.info(f"Checklist created for {MyProject.Trip.tripname} trip in {xlsx_path}.")
        os.startfile(xlsx_path)
    else:
        logger.warning(f"This project does not refer to a trip, no checklist is created.")
    os.remove(temp_csv_file)

    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="svu2AviBase")
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
    config.read(INI_LOCATION)
    # init the logging subsystem
    logging.basicConfig(level=logging.INFO)
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
    else:
        logger.info(f"{parser.description} succeeded")
