import os
import csv
from ghexif import files_dict, UniquePictureName_dict
import logging
import ghexif

logger = logging.getLogger(__name__)

# strip all suffixes for files from Lightroom, DxO PureRaw, DxO Photolab, Capture etc
def deriveUniquePictureName(windows_filename):
    # start with filename.extensiom
    # strip extension, make it lower case
    uniquename, extension = os.path.splitext(windows_filename)
    if extension == "DOP":  # do it again for DOP files that have double extension
        uniquename, extension = os.path.splitext(windows_filename)
    uniquename = uniquename.lower()
    # look for common extensions
    uniquename = uniquename.replace(
        "-nef", ""
    )  # Remove "-NEF" from the uniquename (if present)
    # the following should be externalized!  BUGBUG
    uniquename = uniquename.split("-orf")[0]  # Remove "-ORF" from the uniquename (if present)
    # Remove "-DxO" and any characters after it (if present)
    uniquename = uniquename.split("-DxO")[0]  # PureRaw 4
    uniquename = uniquename.split("_DxO")[0]  # PureRaw 3
    uniquename = uniquename.split("_dxo")[0]  # PhotoLab
    # 
    uniquename = uniquename.split("-jpg")[0]  # PhotoLab
    uniquename = uniquename.split("-JPG")[0]  # PhotoLab
    # Windows? - Copy
    uniquename = uniquename.replace(
        " - copy", ""
    )  # Remove "-ORF" from the uniquename (if present)
    return uniquename

# Read EXIF data file, one record for each file (ORF, jpg, XMP, etc)
# For second call, additional data is appended
# Check if is does not create a duplicaete
def read_file(filename):
    from_time = ""
    last_time = ""
    filecount = len(files_dict)
    with open(filename, "r", newline="", encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile, delimiter=';')
        for row in reader:
            if row['File:FileName'] not in files_dict:
                files_dict[row['File:FileName']] = row
                if row['EXIF:DateTimeOriginal'] != "":
                    if from_time == "" or from_time > row['EXIF:DateTimeOriginal']: 
                        from_time = row['EXIF:DateTimeOriginal']
                    if last_time < row['EXIF:DateTimeOriginal']:
                        last_time = row['EXIF:DateTimeOriginal']
            else:
                logger.warning(f"Unexpected duplicate file {row['WindowsFileName']} not added, ")
                logger.warning(f"File was already found here {files_dict[row['File:FileName']]['WindowsFileName']}.")
    logger.info(f'Read EXIF data for {len(files_dict)-filecount} files from CSV file {filename}.')
    return len(files_dict), from_time, last_time

# read picture data file, one record for each picture, with links to files (ORF, jpg, XMP, etc)
# For second call, additional data is appended
# Check if is does not create a duplicaete
def read_pictures(picturefile):
    picturecount = len(UniquePictureName_dict)
    with open(picturefile, "r", newline="", encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile, delimiter=';')
        for row in reader:
            if row['UniquePictureName'] not in UniquePictureName_dict:
                UniquePictureName_dict[row['UniquePictureName']] = row
                # restore file list
                if row["Files"] == '[]':           # update in place
                    row["Files"] = []
                else:
                    try:
                        # remove additional quoting
                        liststring = row["Files"][1:-1].replace("'","")
                        listvalues = liststring.split(',')
                        # remove possible leading spaces
                        row["Files"] = []                    
                        for value in listvalues:
                            if value.lstrip() not in row["Files"]:
                                row["Files"].append(value.lstrip())
                    except ValueError:
                        pass  # Handle non-numeric values gracefully

    logger.info(f'Read data for {len(UniquePictureName_dict)-picturecount} pictures from CSV file {picturefile}.')
    return len(UniquePictureName_dict)

def read_Files_CSV():
    return

def parseListFromCSV(rawstring):
    if rawstring == '[]' or rawstring == '':           # update in place
        cleanListString = [] 
    else:
        try:
            # remove additional quoting
            liststring = rawstring[1:-1].replace("'","")
            listvalues = liststring.split(',')
            # remove possible leading spaces
            cleanListString = []                    
            for value in listvalues:
                if value.lstrip() not in cleanListString:
                    cleanListString.append(value.lstrip())
        except ValueError:
            cleanListString = [] 
            pass  # Handle non-numeric values gracefully

    return cleanListString