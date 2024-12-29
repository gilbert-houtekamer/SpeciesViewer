# Process the EXIF metadata and analyze them per picture based on the UniquePictureName
import argparse
import csv
import logging
import os, io
import sys
import configparser
import time
import logging
import argparse
import requests
import exiftool
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import ghexif
import ghexif.projectlib
import ghexif.exceptions
import ghexif.statemachine
import ghexif.specieslib
import ghexif.speciesclassification
import ghexif.triplib
import ghexif.breadcrumb
from ghexif import SVException

logger = logging.getLogger(__name__)

class Waarneming:
    observations = []
    mp = None

    def __init__(self, MyProject):
        self.mp = MyProject
        return

    def read(self, filename=""):
        # 1. get the file from disk first
        if filename == "":
            filename = "C:\\Users\\gilbe\\OneDrive\\waarneming.csv"
        try:
            with io.open(filename, mode="r", newline="", encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile, delimiter=';')
                for row in reader:
                    self.observations.append(row)
        except:
            pass    # no blogs yet, that's fine

        return

    def download_observation_images(self, url, folder):
        urlsdone = []

        if not os.path.exists(folder):
            os.makedirs(folder)
        
        response = requests.get(url)
        soup = BeautifulSoup(response.text, 'html.parser')
        metas = soup.find_all('meta')
        species = soup.title.string.split(' - ')
        images  = soup.find_all('img')
        # main picture
        for obj in metas:
            try:
                if obj.attrs['property'] == 'og:image':
                    img_name= obj.attrs['content'].split('?')[0]
                    print(f"{os.path.split(img_name)}")
            except:
                pass
        # remember path
        waarnemingpath = os.path.split(img_name)[0]
        # all pictures (including the main)
        for obj in images:
            try:
                if obj.attrs['alt'] == 'foto bij waarneming':
                    try:
                        img_name = obj.attrs['src'].split('?')[0]
                        filename = os.path.split(img_name)[1]
                        imgurl = urljoin(waarnemingpath, 'photo/'+filename)
                        if url not in urlsdone:
                            urlsdone.append(imgurl)
                            print(f"{imgurl}")
                            img_response = requests.get(imgurl)
                            img_name = os.path.join(folder, imgurl.split('/')[-1])
                            with open(img_name, 'wb') as f:
                                f.write(img_response.content)
                            print(f'Downloaded: {img_name}')
                            # now add metadata (species first)
                            metadata = { 'IPTC:Keywords': species[0] + "," + species[1].split('\n')[0],
                                        'EXIF:GPSLatitude': 52.3650, # Example coordinates for Amsterdam 
                                        'EXIF:GPSLatitudeRef': 'N', 
                                        'EXIF:GPSLongitude': 4.8925, 
                                        'EXIF:GPSLongitudeRef': 'E' }
                            with exiftool.ExifTool() as et: 
                                # Write metadata to the file 
                                exifparm = b''
                                for tag, value in metadata.items(): 
                                    exifparm += b'-' + tag.encode() + b'=' + str(value).encode() + b' '
                                et.execute(exifparm, img_name.encode() )
                                # Confirm metadata added
                            with exiftool.ExifToolHelper() as et:
                                result = et.get_metadata(img_name.encode())
                                for tag in result[0]:
                                    print(f'{tag}: {result[0][tag]}')
                    except Exception as e:
                        logger.warning(f"{parser.description} failed: {e}")     
            except:
                pass            


def main(args, config):
    global starttime, SpeciesDebug
    starttime = time.time()
    SpeciesDebug = 0        # default   

    SpeciesDebug = int(config.get("debug","SpeciesFound"))

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    
        # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)

    # do not run this on masters
    if MyProject.MasterRepository == "":         
        raise SVException(f'Use svr6MergeBlogsForMaster to update the blog page for the master repository.')

    # get repository-wide sc and per project sp.
    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = ghexif.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)
        # read the list with all pre-defined species
    metadatadir = MyProject.getMetaDir()
    #sc.readSpeciesCollection(metadatadir + "active/speciescollection.csv", False)
    # also read list with all known species
    # and count the number of data file items we already have
    sp.readSpeciesInfo("C:/laptop/trips/safaridb/metadata/active/species.csv", False)

    waarnemingen = Waarneming(MyProject)
    waarnemingen.read("C:\\Users\\gilbe\\OneDrive\\waarneming.csv")

    # run through all observations and download pages
    for obs in waarnemingen.observations:
        url = obs['link']
        folder = 'downloaded_images'
        if obs['has photos'] == 'TRUE':
            print(f"{obs['date']} {obs['species name']} {obs['scientific name']} has photo(s).")
            waarnemingen.download_observation_images(url,folder)
        else:
            print(f"{obs['date']} {obs['species name']} {obs['scientific name']} has no photos.")

#     bc = ghexif.breadcrumb.Breadcrumb(MyProject)
#     # sp.getBiotaWebRoot
#     bc.addBCsimple("Nature", MyProject.getBiotaWebRoot() + "/intro-nl.html")
#     bc.addBCsimple("Trips", MyProject.getBiotaWebRoot() + "/trips-nl.html")
#     bc.addBCsimple("Blogs", MyProject.getBiotaWebRoot() + "/biotablog/blogindex-nl.html")
# #    bc.addBCsimple("2024-06-26", "https://houtekamer.com/trips/safari/biotablog/Blog%20Pictures%202024-06-26-2024-06-26-biota-0-5-nl.html")
#     workstr = bc.makeCrumb("2024-06-26")
#     print(workstr)
    
#     bc.clearBreadCrumb()
#     bc.addBCsimple("Nature", MyProject.getBiotaWebRoot() + "intro-nl.html")
#     bc.addBCsimple("Trips", MyProject.getBiotaWebRoot() + "trips-nl.html")      # if trips
#     bc.appendSpeciesBC('Geotrygon costaricensis', MyProject.getBiotaWebRoot())                                              # assume trip
#     workstr = bc.makeCrumb("Geotrygon costaricensis")

#     bc
#     bc.addBCsimple("Nature", MyProject.getBiotaWebRoot() + "intro-nl.html")
#     bc.addBCsimple("Trips", MyProject.getBiotaWebRoot() + "trips-nl.html")      # if trips
#     bc.appendSpeciesBC('026 columbiformes', MyProject.getBiotaWebRoot())                                              # assume trip
#     workstr = bc.makeCrumb("")


    print(workstr)
# new test: statemachine
    # if not statemachine.stateOKtorun("ReadDirExif"):
    #     statemachine.explainAbort("ReadDirExif")

    # # normal mainline 
    # statemachine.updateState("ReadDirExif", True, "Last words from her wisdom.")
    # print(f'statemachine.updateState("ReadDirExif", ok, "Last words from her wisdom.")')
    
    # not so good
    # ok = statemachine.stateOKtorun("Freeze")
    # print(f'statemachine.stateOKtorun("Freeze") {ok}') 
    # if not statemachine.stateOKtorun("Freeze"):
    #     statemachine.explainAbort("Freeze")
# end new test
    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="NewModule")
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
    # define state machine and see if we can run this step
    #statemachine = ghexif.statemachine.StateMachine(config.get("repository","BiotaDatabase"), config.get("project","PictureDirectory"))
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
        #statemachine.updateState(parser.description, False, e.args[0])
    else:
        logger.info(f"{parser.description} succeeded")
        #statemachine.updateState(parser.description, True, "Succesfully run.")
    # Store state in statemachine file for this project
    #statemachine.writeState()
