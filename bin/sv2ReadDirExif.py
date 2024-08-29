#!/usr/bin/env python3

# Scan the files in a directory, and keep selected EXIF properties in a CSV file.
#
# The files are specified in "svReadDir.ini" file, 
# currently stored in INI_LOCATION relative to program code.
#

import argparse
import configparser
import csv
import logging
import os
import sys
import time

import exiftool

# configuration
# relative to .py file
INI_LOCATION = "svconfig.ini"
# files to exclude because they are not related to pictures
IGNOREEXTENSIONS = ['txt', 'ico', 'html']

# code init
scriptpath = os.path.dirname(__file__)
modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
os.chdir(modpath)
sys.path.append(modpath)

from ghexif import COLUMN_NAMES, SELECTED_FIELDS
import ghexif.projectlib

from ghexif.handling import deriveUniquePictureName

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    return config

def main(args):
    config = readConfig()

    starttime = time.time()
    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))  
    
    print(f'Will read files recursively from {MyProject.getProjectPath()}.')    
    # logging.basicConfig(level=logging.DEBUG)
    # exiftool.ExifTool.__init__(None, common_args=['-G'])
    with exiftool.ExifToolHelper(
        logger=logging.getLogger(__name__), common_args=["-G"]
    ) as et:
        if not os.path.isdir(MyProject.getProjectPath()):
            print(f"Directory {MyProject.getProjectPath()} cannot be found, or refers to a file.")
            sys.exit

        print(f"Reading directories (takes about 1 minute for every 4000 files)...")
        file_extension_count = {}        
         
        try:
            metadata = et.get_metadata(
                MyProject.getProjectPath(), 
                params=SELECTED_FIELDS)
        except:
            print(f"No files found in the directory {MyProject.getProjectPath()}, or error processing them.")
            sys.exit(12)

 
        datafilelocation = MyProject.getMetaDirReview()
        os.makedirs(datafilelocation, exist_ok=True) 
        datafilelocation += "data_files.csv"

        with open(datafilelocation, "w", newline="") as csvfile:
            metadatareadertime = time.time() - starttime
            print(
                f"Reading EXIF metadata took {metadatareadertime:.1f} seconds for {len(metadata)} files."
            )
            writer = csv.DictWriter(
                csvfile, fieldnames=COLUMN_NAMES.values(), quoting=csv.QUOTE_MINIMAL, delimiter=';'
            )
            writer.writeheader()
            for d in metadata:
                row = {}
                for selected_field in SELECTED_FIELDS:
                    if (
                        selected_field in COLUMN_NAMES
                        and COLUMN_NAMES[selected_field] in d
                    ):
                        row[COLUMN_NAMES[selected_field]] = d[
                            COLUMN_NAMES[selected_field]
                        ]
                    # some fields are merged
                    elif selected_field == "-IFD0:Model" and "EXIF:Model" in d:
                        row["CameraModel"] = d["EXIF:Model"]
                        if row["CameraModel"] == "OM-1MarkII":
                            row["CameraModel"] = "OM-1 Mark II"
                    elif selected_field == "-XMP-tiff:Model" and "XMP:Model" in d:
                        row["CameraModel"] = d["XMP:Model"]
                    elif selected_field == "-IFD0:Make" and "EXIF:Make" in d:
                        row["CameraMake"] = d["EXIF:Make"]
                    elif selected_field == "-XMP-tiff:Make" and "XMP:Make" in d:
                        row["CameraMake"] = d["XMP:Make"]
                    elif (
                        selected_field == "-lensserialnumber"
                        and "MakerNotes:LensSerialNumber" in d
                    ):
                        row["LensSerialNumber"] = d["MakerNotes:LensSerialNumber"]
                    elif (
                        selected_field == "-exifIFD:lensserialnumber"
                        and "EXIF:LensSerialNumber" in d
                    ):
                        row["LensSerialNumber"] = d["EXIF:LensSerialNumber"]
                    elif selected_field == "-ImageSize" and "Composite:ImageSize" in d:
                        row["ImageSize"] = d["Composite:ImageSize"]
                    elif (
                        selected_field == "-imagesize" and "Composite:ImageSize_1" in d
                    ):
                        row["ImageSize"] = d["Composite:ImageSize_1"]

                # find unique identifier for all files based on a file click
                row["UniquePictureName"] = deriveUniquePictureName(
                    row[COLUMN_NAMES["-filename"]]
                )
                # derived values for easy of use
                name, extension = os.path.splitext(row["File:FileName"])
                row["WindowsExtension"] = extension[1:].lower()
                row["WindowsFileName"] = (
                    row["File:Directory"] + "/" + row["File:FileName"]
                )
                # do not introduce noise
                if row["WindowsExtension"] not in IGNOREEXTENSIONS:
                    writer.writerow(row)
                    # maintain counters per type
                    file_extension_count[extension[1:].lower()] = (
                        file_extension_count.get(extension[1:].lower(), 0) + 1
                    )

    print(f"\nThe files counted by filetype are:")
    for extent, count in file_extension_count.items():
        print(f"   {extent.upper()} files: {count}")
    totaltime = time.time() - starttime
    print(
        f'\nCSV file written to {datafilelocation}, total time {totaltime:.1f} seconds.'
    )


if __name__ == "__main__":
    # get commandline parameters and mix everything
    parser = argparse.ArgumentParser(description="Handle photos.")
    parser.add_argument(
        "--dir", type=str, default=os.path.join(modpath, "data"), help="foto directory"
    )
    main(parser.parse_args())
