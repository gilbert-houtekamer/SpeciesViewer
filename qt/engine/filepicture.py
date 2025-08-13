from engine.specieslib import Species
from engine.speciesclassification import SpeciesClassification
from engine.filecsv import Files
from engine.picturecsv import Pictures
import logging
import time
import os
from pathlib import Path  

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
    
    # read just file and pictures for better experience
    def readTripDay(self, trippath, projectdirectory):
        metadatadir = os.path.join(trippath,  "metadata", "review", projectdirectory)
        self.fi = Files(None)
        filecount = self.fi.read_file(os.path.join(metadatadir, "data_files.csv"))

        # Read data for 42 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_pictures.csv.
        self.upn = Pictures(None)
        picturecount = self.upn.read_pictures(os.path.join(metadatadir, "data_pictures.csv"))
        
        return (filecount[0] > 0) and (picturecount > 0)  
    
    def readProject(self, trippath, projectdirectory, getEarlierDays = False, silent=False):
        # dlg = ImagePreviewDialog(image_dir="D:/CurrentPictures/2017/Oostzee zeilzomer/2017-08-16")
        metadatadir = os.path.join(trippath,  "metadata")
        starttime = time.time()
        # read species
        # speciesfilename = "E:/Dropbox/trips/2017 oostzeedb/metadata/review/2017-08-09/species.csv"
        self.sp = Species(None)    
        # from log: E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\species.csv.
        speciesfilename = os.path.join(metadatadir, "review", "2017-08-09", "species.csv")
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

