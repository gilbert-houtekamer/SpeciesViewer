
import os
import csv
import logging
from collections import OrderedDict
from engine.csv_columns import PICTURE_FIELDS

logger = logging.getLogger("speciesviewer")

# read pictures without MyProject
class Pictures:
    def __init__(self, MyProject):
        self.UniquePictureName_dict = {}      
        self.picturecount = 0
        self.picturefile = ""

        if MyProject != None:
            MyProject.registerSpecies(self)
            self.mp = MyProject
        return 
    
    # read picture data file, one record for each picture, with links to files (ORF, jpg, XMP, etc)
    # For second call, additional data is appended
    # Check if is does not create a duplicaete
    def read_pictures(self, picturefile):
        self.picturefile = picturefile
        self.picturecount = len(self.UniquePictureName_dict)
        with open(picturefile, "r", newline="", encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile, delimiter=';')
            for row in reader:
                if row['UniquePictureName'] not in self.UniquePictureName_dict:
                    self.UniquePictureName_dict[row['UniquePictureName']] = row
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

        logger.debug(f'Read data for {len(self.UniquePictureName_dict)-self.picturecount} pictures from CSV file {picturefile}.')
        return len(self.UniquePictureName_dict)
    
    # intended to only write project sumary, not all pictures
    def write_project_summary(self):
        # print(f"Update project CSV {self.picturefile}")
        # Write Picture CSV file
        # check if the output can be written, avoid annoying wait
        # No file of its own: the merged, all-days table of a Trip-scope viewer.
        # Its days are saved through their own tables; nothing to do here. It
        # used to try open('') and blame Excel: "Picture file () is locked…".
        if not self.picturefile:
            logger.debug("write_project_summary: no file — the merged all-days "
                         "table is saved per day.")
            return
        try:
            pictures_out_csvfile = open(self.picturefile, "w", encoding='utf-8', newline="")
        except OSError as e:
            logger.warning(f"Could not save the picture table ({self.picturefile}): {e}. "
                           f"If it is open in Excel, close it there and try again.")
            return

        with pictures_out_csvfile as csvfile:
            # logger.info(f'Write Picture data CSV file {self.picturefile}.')
            # needed to get column in order
            ordered_fieldnames = OrderedDict((field, None) for field in PICTURE_FIELDS)                 
            writer = csv.DictWriter(csvfile, fieldnames=ordered_fieldnames, quoting=csv.QUOTE_MINIMAL, delimiter=';')
            writer.writeheader()
            for upn_record in self.UniquePictureName_dict:    
                row = self.UniquePictureName_dict[upn_record]        
                writer.writerow(row)        