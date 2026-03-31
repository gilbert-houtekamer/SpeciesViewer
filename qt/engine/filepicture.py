from engine.specieslib import Species
from engine.speciesclassification import SpeciesClassification
from engine.filecsv import Files
from engine.picturecsv import Pictures
import logging
import time
import os
from pathlib import Path  
import subprocess
import json
import ast
import pdb  # For debugging


logger = logging.getLogger("speciesviewer")

# Write all trip data in CSV file "E:\Dropbox\naturedb\admin\trip_admin.csv".
# sv4WriteBlog: Create a blog for project 2017-08-09
# Read 15 predefined species from E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\species.csv.
# Read specification file, now create links.
# Read 19185 predefined species from E:\Dropbox\trips\2017 oostzeedb\metadata/active/speciescollection.csv.
# Read EXIF data for 94 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_files.csv.
# Read data for 42 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_pictures.csv.
# Read all committed data from the main repository in 0.1 seconds.
# Read EXIF data for 3 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\data_files.csv.
# Read data for 0 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\data_pictures.csv.
# Read all new data from project in 0.0 seconds.

def ensure_list(subject):
    if isinstance(subject, list):
        return subject
    elif isinstance(subject, str):
        return [token.strip() for token in subject.split(",")]
    else:
        raise TypeError(f"Unexpected subject type: {type(subject)}")
    

def format_subject_for_xmp(subject_text):
    try:
        parts = ast.literal_eval(subject_text)
        if isinstance(parts, list):
            return ", ".join(p.strip("'\"") for p in parts)
    except Exception:
        pass
    return subject_text.strip("'\"")    

# read files without MyProject
class FilePicture:
    def __init__(self, MyProject):
        self.sp = {}
        self.rs = {}
        self.sc = {}
        self.fi = {}
        self.upn = {}   

        if MyProject != None:
            MyProject.registerSpecies(self)
            self.mp = MyProject
        return 
    
    def clearCSVs(self):
        self.sp = {}
        self.rs = {}
        self.sc = {}
        self.fi = {}
        self.upn = {}        
        return
    
    # get color ode if any
    def getColor(self, filename):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            color = self.upn.UniquePictureName_dict[upn_name]["Label"]
            return color.lower()
        except Exception:
            return ""
        
    # returns a string
    def getErrorMsg(self, filename):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            errormsg = self.upn.UniquePictureName_dict[upn_name]["errormessage"]
            if errormsg != '':
                logger.info(f"getErrorMsg: {errormsg}")
            return errormsg
        except Exception:
            return ""
        
    # returns a list, eg ['bombylius venosus', 'zwartborstelwolzwever']
    # may need to handle  or simply knobbelzwaan
    def getSubject(self, filename):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            subject = self.upn.UniquePictureName_dict[upn_name]["subject"]
            return subject
        except Exception:
            return ""
        
    def getSpeciesName(self, filename):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            upn  = self.upn.UniquePictureName_dict[upn_name]
            if upn["NLSoortNaam"] != "":
                # logger.info(f"get: {upn["NLSoortNaam"]}")
                return upn["NLSoortNaam"]
            elif upn["EngSoortNaam"] != "":
                return upn["EngSoortNaam"]
            else:
                return upn["WetSoortNaam"]
        except Exception:
            return ""        
        
    def getUPN(self, filename):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            upn = self.upn.UniquePictureName_dict[upn_name]
            return upn
        except Exception:
            return None                
        
    def filteredFile(self, filename, allowed_colors = None):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            color = self.upn.UniquePictureName_dict[upn_name]["Label"]            
            if allowed_colors != None:
                if color.lower() in allowed_colors:
                    return True
            return False
        except Exception as e:
            logger.warning("Error in filteredFile for {filename} error {e}")
            return True

    
    # read just file and pictures for better experience
    def readTripDay(self, trippath, projectdirectory):
        metadatadir = os.path.join(trippath,  "metadata", "review", projectdirectory)
        self.fi = Files(None)
        filecount = self.fi.read_file(os.path.join(metadatadir, "data_files.csv"))

        # Read data for 42 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_pictures.csv.
        self.upn = Pictures(None)
        picturecount = self.upn.read_pictures(os.path.join(metadatadir, "data_pictures.csv"))
        
        return (filecount[0] > 0) and (picturecount > 0)  
    
    # replaceFileSubject.
    # 1. change actual file with EXIFtool
    # 2. Change the .csv file reflected in the internal structures
    # 3. Review Photolab and Lightroom
    def replaceFileSubject(self, filename, original_subject, correct_subject):
        filepath = Path(self.fi.files_dict[filename]['WindowsFileName'])
        original_subject_for_xmp = format_subject_for_xmp(original_subject)
        correct_subject_for_xmp = format_subject_for_xmp(correct_subject)        
        # Step 1: Read current XMP:Subject using exiftool
        try:
            result = subprocess.run(
                ['exiftool', '-j', '-XMP:Subject', str(filepath)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True
            )
            metadata = json.loads(result.stdout)[0]
            current_subjects = metadata.get('Subject', [])
            if isinstance(current_subjects, str):
                current_subjects = [current_subjects]
            # print(f"current_subjects in {str(filepath)} from {current_subjects} to {correct_subject}")

            # Step 3: Replace subject if original_subject is found
            try:
                original_subject_list = ast.literal_eval(original_subject)
                if not isinstance(original_subject_list, list):
                    raise ValueError
            except Exception:
                original_subject_list = [original_subject]
            if original_subject_list == current_subjects:
                cmd = ['exiftool', f'-XMP:Subject={correct_subject_for_xmp}', '-overwrite_original', str(filepath)]
                # print("Running command:", " ".join(cmd))

                subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=True,
                    text=True
                )
                # print(f"Updated subject in {filepath.name} to: {correct_subject}")
            else:
                print(f"Original subject '{original_subject_for_xmp}' not found in {filepath.name}")

        except subprocess.CalledProcessError as e:
            print(f"ExifTool error: {e.stderr}")

        # Step 2: update the internal data structure
        self.fi.files_dict[filename]['XMP:Subject'] = correct_subject



    # the UPN update is only reflected in the data_pictures file and the internal structures 
    def replaceUPNSubject(self, filename, original_subject, correct_subject, sc_object):
        upn_object = self.upn.UniquePictureName_dict[Path(filename).name]
        if upn_object != None:
            upn_object['subject'] = correct_subject
            upn_object['errormessage'] = ''   # missing species issue fixed, clear error message
            if sc_object is not None:
                upn_object['NLSoortNaam'] = sc_object['DutchName']
                upn_object['EngSoortNaam'] = sc_object['EnglishName']
                upn_object['WetSoortNaam'] = sc_object['ScientificName']
            # write updated data_pictures file 
            self.upn.write_project_summary()
        return upn_object

    # function to determine which files must be updated
    def updateSubject(self, filename, original_subject, corrected_subject):
        if original_subject == corrected_subject:
            logger.info(f"There are no changes for {filename}, no updates doen.")

        # print(f"Entered updateSubject self.fpr.updateSubject({filename}, {corrected_subject})")

        try:
            parts = ast.literal_eval(corrected_subject)
            lookup_name = max(parts, key=len) if isinstance(parts, list) and parts else corrected_subject
        except Exception:
            lookup_name = corrected_subject
        scientific_name = self.sc.getSpecies(lookup_name)
        if scientific_name == "":  # still no name found
            logger.warning(f"The species name is not recognized, but the updated keywords are stored in the files.")
            sc_object = None
        else:
            sc_object = self.sc.sc_dict[scientific_name]

        upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
        for f in self.fi.files_dict:
            if self.fi.files_dict[f]["UniquePictureName"] == upn_name:
                # print(f"File {f} has upn name {upn_name}")
                if self.fi.files_dict[f]["XMP:Subject"] == original_subject:
                    # print(f"File {f} has original subject {original_subject}")
                    self.replaceFileSubject(f, original_subject, corrected_subject)

        # update external CSV file (internal done on a per file basis)
        self.fi.write_project_files()
        # update internal and external data_pictures (just one per definition)
        return self.replaceUPNSubject(upn_name, original_subject, corrected_subject, sc_object)        
   

    # read all species
    def readMaster(self, trippath, projectdirectory, getEarlierDays = False, silent=False):
        metadatadir = os.path.join(trippath,  "metadata")
        starttime = time.time()
        self.sp = Species(None)
        speciesfilename = os.path.join(metadatadir, "active", "species.csv")
        self.rs = self.sp.readSpeciesInfo(speciesfilename, False)

        # read all knowlegde about species, including species we do not yet have.  
        # Maintain English and Dutch species list
        self.sc = SpeciesClassification(None)
        # E:\Dropbox\trips\2017 oostzeedb\metadata/active/speciescollection.csv.        
        self.sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)
        # Read EXIF data file, one record for each file (ORF, jpg, XMP, etc)        
        # Read EXIF data for 94 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_files.csv.
        self.fi = Files(None)
        filecount = self.fi.read_file(os.path.join(metadatadir, "active", "data_files.csv"))

        # Read data for 42 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_pictures.csv.
        self.upn = Pictures(None)
        picturecount = self.upn.read_pictures(os.path.join(metadatadir, "active", "data_pictures.csv"))

         # # see what files for non-commtted projected
        # if getEarlierDays and self.Trip != None:
        #     # Collect states
        #     directory_to_scan = os.path.join(self.getMetaDir(), 'review') 
        #     days_dict = getAllProjectStatus(self, directory_to_scan, silent=True)
        #     # read csv for files and pictures that are not yet committed
        #     for statemachine in days_dict:                      # projects
        #         if statemachine >= self.ProjectName(): 
        #             break
        #         if not days_dict[statemachine]['WriteCommit'][0]:  # this project is not committed
        #             logger.info(f"Reading data from project {statemachine} that has not yet been committed.")
        #             read_file(self, os.path.join(metadatadir, "review", statemachine, "data_files.csv"), silent=True)
        #             read_pictures(self, os.path.join(metadatadir, "review", statemachine, "data_pictures.csv"))

        thissteptime = time.time()
        logger.info(f"Read all committed data from the main repository in {thissteptime-starttime:.1f} seconds.")    
    
        # read other files and pictures
        RepositoryFileCount = filecount
        RepositoryPictureCount = picturecount
        
        return [RepositoryFileCount[0], RepositoryPictureCount, RepositoryFileCount[1], RepositoryFileCount[2]]
    
    def readProject(self, trippath, projectdirectory, getEarlierDays = False, silent=False):
        metadatadir = os.path.join(trippath,  "metadata")
        starttime = time.time()
        self.sp = Species(None)
        speciesfilename = os.path.join(metadatadir, "review", projectdirectory, "species.csv")
        self.rs = self.sp.readSpeciesInfo(speciesfilename, False)

        # read all knowlegde about species, including species we do not yet have.  
        # Maintain English and Dutch species list
        self.sc = SpeciesClassification(None)
        # E:\Dropbox\trips\2017 oostzeedb\metadata/active/speciescollection.csv.        
        self.sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)
        # Read EXIF data file, one record for each file (ORF, jpg, XMP, etc)        
        # Read EXIF data for 94 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_files.csv.
        self.fi = Files(None)
        filecount = self.fi.read_file(os.path.join(metadatadir, "active", "data_files.csv"))

        # Read data for 42 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_pictures.csv.
        self.upn = Pictures(None)
        picturecount = self.upn.read_pictures(os.path.join(metadatadir, "active", "data_pictures.csv"))

         # # see what files for non-commtted projected
        # if getEarlierDays and self.Trip != None:
        #     # Collect states
        #     directory_to_scan = os.path.join(self.getMetaDir(), 'review') 
        #     days_dict = getAllProjectStatus(self, directory_to_scan, silent=True)
        #     # read csv for files and pictures that are not yet committed
        #     for statemachine in days_dict:                      # projects
        #         if statemachine >= self.ProjectName(): 
        #             break
        #         if not days_dict[statemachine]['WriteCommit'][0]:  # this project is not committed
        #             logger.info(f"Reading data from project {statemachine} that has not yet been committed.")
        #             read_file(self, os.path.join(metadatadir, "review", statemachine, "data_files.csv"), silent=True)
        #             read_pictures(self, os.path.join(metadatadir, "review", statemachine, "data_pictures.csv"))

        thissteptime = time.time()
        logger.info(f"Read all committed data from the main repository in {thissteptime-starttime:.1f} seconds.")    
    
        # read other files and pictures
        RepositoryFileCount = filecount
        RepositoryPictureCount = picturecount
        metadatadirreview = os.path.join(metadatadir, 'review') 
        
        # Read EXIF data for 3 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\data_files.csv.
        # Read data for 0 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\data_pictures.csv.
        count, from_time, last_time = self.fi.read_file(os.path.join(metadatadirreview, '2017-08-09', 'data_files.csv'), silent=True)
        self.upn.read_pictures(os.path.join(metadatadirreview, '2017-08-09', 'data_pictures.csv'))      

        logger.info(f"Read all new data from project in {time.time()-thissteptime:.1f} seconds.")
        return [RepositoryFileCount[0], RepositoryPictureCount, RepositoryFileCount[1], RepositoryFileCount[2]]

