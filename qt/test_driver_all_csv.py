from gui.image_viewer import ImagePreviewDialog
from engine.specieslib import Species
from engine.speciesclassification import SpeciesClassification
from engine.filecsv import Files
from engine.picturecsv import Pictures
import logging
import time

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

if __name__ == "__main__":
    import sys, os
    from PyQt6.QtWidgets import QApplication

    logging.basicConfig(level=logging.INFO)    
    logger = logging.getLogger("speciesviewer")
    app = QApplication(sys.argv)
    # dlg = ImagePreviewDialog(image_dir="D:/CurrentPictures/2017/Oostzee zeilzomer/2017-08-16")
    metadatadir = "E:/Dropbox/trips/2017 oostzeedb/metadata/"
    starttime = time.time()
    # read species
    
    # speciesfilename = "E:/Dropbox/trips/2017 oostzeedb/metadata/review/2017-08-09/species.csv"
    # read all species.  
    # Maybe want the 
    sp = Species(None)    
    # from log: E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\species.csv.
    speciesfilename = os.path.join(metadatadir, "review", "2017-08-09", "species.csv")
    rc =  sp.readSpeciesInfo(speciesfilename, False)    

    # read all knowlegde about species, including species we do not yet have.  
    # Maintain English and Dutch species list
    sc = SpeciesClassification(None)
    # E:\Dropbox\trips\2017 oostzeedb\metadata/active/speciescollection.csv.        
    sc.readSpeciesCollection(metadatadir + "active/speciescollection.csv", False)
    # read the individual files
    # Read EXIF data for 94 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_files.csv.
    fi = Files(None)
    filecount = fi.read_file(metadatadir+"active/data_files.csv")

    # Read data for 42 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_pictures.csv.
    upn = Pictures(None)
    picturecount = upn.read_pictures(metadatadir+"active/data_pictures.csv")

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
 
        # def readProject(self, getEarlierDays = False, silent=False):
        # starttime = time.time()
        # metadatadir = self.getMetaDir()
        # metadatadirreview = self.getMetaDirReview()

    # read other files and pictures
    RepositoryFileCount = filecount
    RepositoryPictureCount = picturecount
    metadatadirreview = os.path.join(metadatadir, 'review') 
    
    # Read EXIF data for 3 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\data_files.csv.
    # Read data for 0 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\data_pictures.csv.
    count, from_time, last_time = fi.read_file(os.path.join(metadatadirreview, '2017-08-09', 'data_files.csv'), silent=True)
    upn.read_pictures(os.path.join(metadatadirreview, '2017-08-09', 'data_pictures.csv'))      

    logger.info(f"Read all new data from project in {time.time()-thissteptime:.1f} seconds.")
