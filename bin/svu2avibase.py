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
from openpyxl.styles import PatternFill, Alignment, Border, Side
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

def create_excel_checklist(csv_path, xlsx_path, start_date, num_days):
    # Load CSV
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.reader(f, delimiter=';')
        rows = list(reader)

    header = rows[0]
    data = rows[1:]

    # Create workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Checklist"

    # Define fills
    weekend_fill = PatternFill(start_color="D3D3D3", end_color="D3D3D3", fill_type="solid")
    light_grey_row_fill = PatternFill(start_color="F0F0F0", end_color="F0F0F0", fill_type="solid")

    # Extend header with date columns
    date_headers = []
    weekend_columns = []
    for i in range(num_days):
        date = start_date + timedelta(days=i)
        date_str = date.strftime("%d/%m/%Y")
        date_headers.append(date_str)
        if date.weekday() >= 5:  # Saturday=5, Sunday=6
            weekend_columns.append(len(header) + i + 1)

    full_header = header + date_headers

    # Write header
    for col_idx, value in enumerate(full_header, 1):
        cell = ws.cell(row=1, column=col_idx, value=value)
        if col_idx > len(header):  # Date columns
            cell.alignment = Alignment(horizontal="left", vertical="center")
            if col_idx in weekend_columns:
                cell.fill = weekend_fill
        else:
            cell.alignment = Alignment(horizontal="center", vertical="center")

    # Write data rows — start at row 3 so row 2 is reserved for the column
    # summary formulas added below (otherwise the first species' E/F/H cells
    # are overwritten by the summary).
    for row_idx, row in enumerate(data, start=3):
        # Fill in original columns
        for col_idx, value in enumerate(row, 1):
            cell = ws.cell(row=row_idx, column=col_idx)

            if col_idx == 1:  # Column A: numeric
                try:
                    cell.value = int(value)
                except ValueError:
                    cell.value = value
            else:
                cell.value = value

        # Fill in date columns (empty but formatted)
        for i in range(num_days):
            col_idx = len(header) + i + 1
            cell = ws.cell(row=row_idx, column=col_idx)
            if row_idx == 1:
                cell.number_format = "DD/MM/YYYY"
            else:
                cell.number_format = "0"  # Format as number
            if col_idx in weekend_columns:
                cell.fill = weekend_fill

        # Shade row if Column B is empty
        if not ws.cell(row=row_idx, column=2).value:
            for col in range(1, len(full_header) + 1):
                ws.cell(row=row_idx, column=col).fill = light_grey_row_fill

    # Adjust column widths
    ws.column_dimensions['A'].width = 5  # Enough for 999
    ws.column_dimensions['E'].width = 5
    ws.column_dimensions['F'].width = 5
    ws.column_dimensions['G'].width = 8
    ws.column_dimensions['H'].width = 6
    for col in range(2, len(header) + 1):
        if col not in [5, 6, 7, 8]:  # E=5, F=6, G=7, H=8
            ws.column_dimensions[get_column_letter(col)].width = 24
    for col in range(len(header) + 1, len(full_header) + 1):
        ws.column_dimensions[get_column_letter(col)].width = 5  # Narrow for 2-digit day

    # Add formulas to columns E and F (data now occupies rows 3..len(data)+2)
    for row_idx in range(3, len(data) + 3):
        b_val = ws.cell(row=row_idx, column=2).value
        if b_val:  # Only if column B has a value
            # Column E formula
            formula_e = f'=COUNT(J{row_idx}:AH{row_idx})'
            ws.cell(row=row_idx, column=5, value=formula_e)

            # Column F formula — flag as "New" when species has not been
            # marked Known yet but at least one day-tick is present
            formula_f = f'=IF(AND(G{row_idx}<>"Known", E{row_idx}>0), "New", "")'
            ws.cell(row=row_idx, column=6, value=formula_f)

    # Add summary formulas to row 2
    summary_targets = [5, 6, 8]  # Columns E, F, H
    for col in summary_targets:
        col_letter = get_column_letter(col)
        formula = f'=COUNTIF({col_letter}3:{col_letter}{len(data)+2}, ">0")'
        ws.cell(row=2, column=col, value=formula)

    # Add summary formulas for all date columns (J to AH)
    for col in range(len(header) + 1, len(header) + num_days + 1):
        col_letter = get_column_letter(col)
        formula = f'=COUNT({col_letter}3:{col_letter}{len(data)+2})'
        ws.cell(row=2, column=col, value=formula)    

    # Define thin border style
    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    # Apply borders to all cells in A1:AH385
    for row in ws.iter_rows(min_row=1, max_row=385, min_col=1, max_col=34):  # AH = column 34
        for cell in row:
            cell.border = thin_border        

    # Save file
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
    sc.readSpeciesCollection(metadatadir + "active/speciescollection.csv", False)

    # get date from trip file
    if MyProject.Trip:
        # Source list format: 'avibase' (English in column 0) or
        # 'birdingbreaks' (sequence number in column 0). Both currently parse
        # the same way — column 0 is the dict key, column 1 scientific,
        # column 2 Dutch — but the choice is logged so source-format quirks
        # can be diagnosed and the dispatch is in place for future readers.
        source_format = config.get("avibase", "source_format", fallback="avibase")
        logger.info(f"Reading AviBase source list using '{source_format}' format.")
        readAviBase(MyProject.Repository, "avibase.csv")
        addSpeciesInfo(sc, Reference_sp)
        temp_csv_file = os.path.join(MyProject.Repository, "soorten-temp.csv")
        createRawCSV(temp_csv_file)
        xlsx_path = os.path.join(MyProject.Repository, "avibase-checklist.xlsx")

        fromdate_str = MyProject.Trip.trip_dict[MyProject.Trip.tripname]['fromdate']
        todate_str = MyProject.Trip.trip_dict[MyProject.Trip.tripname]['todate']        
        # Convert to datetime objects
        start_date = datetime.strptime(fromdate_str, "%Y-%m-%d")
        end_date = datetime.strptime(todate_str, "%Y-%m-%d")

        # Calculate number of days (inclusive)
        num_days = (end_date - start_date).days + 1

        create_excel_checklist(temp_csv_file, xlsx_path, start_date, num_days)
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
