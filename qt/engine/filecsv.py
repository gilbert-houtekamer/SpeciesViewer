
import os
import csv
import logging

logger = logging.getLogger("speciesviewer")

# strip all suffixes for files from Lightroom, DxO PureRaw, DxO Photolab, Capture etc
def deriveUniquePictureName(windows_filename):
    # start with filename.extensiom
    # strip extension, make it lower case
    uniquename, extension = os.path.splitext(windows_filename)
    if extension == "DOP":  # do it again for DOP files that have double extension
        uniquename, extension = os.path.splitext(windows_filename)
    uniquename = uniquename.lower()
    # look for common extensions
    uniquename = uniquename.split("-small")[0]  # SpeciesViewer
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


# read files without MyProject
class Files:
    def __init__(self, MyProject):
        self.files_dict = {}      
        self.filecount = 0

        if MyProject != None:
            MyProject.registerSpecies(self)
            self.mp = MyProject
        return 
    
    # Read EXIF data file, one record for each file (ORF, jpg, XMP, etc)
    # For second call, additional data is appended
    # Check if is does not create a duplicaete
    def read_file(self, filename, silent=False):
        from_time = ""
        last_time = ""
        runningcounter = len(self.files_dict)
        with open(filename, "r", newline="", encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile, delimiter=';')
            for row in reader:
                if row['File:FileName'] not in self.files_dict:
                    self.files_dict[row['File:FileName']] = row
                    if row['EXIF:DateTimeOriginal'] != "":
                        if from_time == "" or from_time > row['EXIF:DateTimeOriginal']: 
                            from_time = row['EXIF:DateTimeOriginal']
                        if last_time < row['EXIF:DateTimeOriginal']:
                            last_time = row['EXIF:DateTimeOriginal']
                elif not silent:
                    logger.warning(f"Unexpected duplicate file {row['WindowsFileName']} not added, ")
                    logger.warning(f"File was already found here {self.files_dict[row['File:FileName']]['WindowsFileName']}.")
        logger.info(f'Read EXIF data for {len(self.files_dict)-runningcounter} files from CSV file {filename}.')
        return len(self.files_dict), from_time, last_time