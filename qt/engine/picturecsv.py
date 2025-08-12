
import os
import csv
import logging

logger = logging.getLogger("speciesviewer")

# read pictures without MyProject
class Pictures:
    def __init__(self, MyProject):
        self.UniquePictureName_dict = {}      
        self.picturecount = 0

        if MyProject != None:
            MyProject.registerSpecies(self)
            self.mp = MyProject
        return 
    
    # read picture data file, one record for each picture, with links to files (ORF, jpg, XMP, etc)
    # For second call, additional data is appended
    # Check if is does not create a duplicaete
    def read_pictures(self, picturefile):
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

        logger.info(f'Read data for {len(self.UniquePictureName_dict)-self.picturecount} pictures from CSV file {picturefile}.')
        return len(self.UniquePictureName_dict)