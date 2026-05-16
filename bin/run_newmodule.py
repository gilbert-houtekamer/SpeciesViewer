# Process the EXIF metadata and analyze them per picture based on the UniquePictureName
import argparse
import csv
import logging
import os, io
import sys
import glob
import math
import configparser
import shutil
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

logger = logging.getLogger("speciesviewer")
# create file handler which logs even debug messages
fh = logging.FileHandler('sysprint')
fh.setLevel(logging.INFO)
logger.addHandler(fh)

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
            line = 0
            with io.open(filename, mode="r", newline="", encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile, delimiter=',')
                for row in reader:
                    line += 1
                    try:
                        self.observations.append(row)
                    except:
                        logger.warning(f"Line {line} not accepted : {row}")
                    # if line > 2000:
                    #     break
        except:
            pass    # no blogs yet, that's fine
        logger.info(f"{line} read from {filename} with warneming.nl export.")
        return

    def decimal_to_dms(self, decimal_degrees, direction):
        fractional_part, degrees = math.modf(float(decimal_degrees))
        #degrees = int(decimal_degrees)
        fractional_part, minutes = math.modf(fractional_part*60)
        seconds = fractional_part * 60
        return f"{int(degrees)}°{int(minutes)}'{seconds:.2f}\" "  #  {direction}

  
    def makeEXIFMetadata(self, obs):
        # species type (hybrid, aggregate species, species, subspecies, synonym, variety)
        # 	subspecies: like "Viola tricolor subsp. curtisii"
        # todo: for now we only support species, not subspecies or groups
        if str(obs['species type']).lower() not in ('species', 'soort'): return None
        # is certain, is escape: only support certains, not sure about escapes, probably ok
        # todo: handle "not certain" with rescan
        if str(obs['is certain']).upper() != 'TRUE': return None
        # has photos: do not process observations with pictures
        if str(obs['has photos']).upper() != 'TRUE': return None            
        # date and time: typically from the iPhone photos or Camera time stamp
        metadata = '-EXIF:DateTimeOriginal=' + str(obs['date']).replace('/',':').replace('-',':') + ' ' + obs['time'] + '\n'
        # species name	
        # scientific name
        # sex (U/M/F)
        # todo: life stage (adult, adult breeding, caterpillar, empty shell or single valve, flowering, fruit-bearing, gall, imago, larva/nymph, second calendar year, unknown)
        #                  (tweede kalenderjaar)
        metadata += '-IPTC:Keywords=' + str(obs['species name']).lower() + ',' + str(obs['scientific name']).lower()
        if obs['sex'] == 'M': metadata += ',male'
        elif obs['sex'] == 'F': metadata += ',female'

        metadata += '\n'
        # location: this the name that waarneming.nl uses to denote the area
        if obs['location'] != '': 
            metadata += '-XMP:Location=' + obs['location'] + '\n'
        # country division (provincie), country
        # might consider rename country, or somehow normalize it
        if obs['country division'] != '':  metadata += '-XMP:State=' + obs['country division'] + '\n'
        if obs['country'] != '':  metadata += '-XMP:Country=' + obs['country'] + '\n '
        # lat, lng.  Negative numbers means S and W respectivel 
        NS = 'N' if float(obs['lat']) >= 0 else 'S'
        EW = 'E' if float(obs['lng']) >= 0 else 'W'        
        metadata += '-EXIF:GPSLatitude=' +  self.decimal_to_dms(obs['lat'], NS) + '\n '
        metadata += '-EXIF:GPSLongitude=' + self.decimal_to_dms(obs['lng'], EW) + '\n '
        metadata += '-EXIF:GPSLatitudeRef=' + NS + '\n '
        metadata += '-EXIF:GPSLongitudeRef=' + EW + '\n '            
        # metadata += '-EXIF:GPSLatitude=' +  obs['lat'] + '\n '
        # metadata += '-EXIF:GPSLongitude=' + obs['lng'] + '\n '              
     
        # description
        if  obs['validation status'] != '':
            metadata += '-XMP:Description=<a href="https://waarneming.nl/observation/' + obs['id'] + '/">waarneming.nl</a>: '
            if obs['validation status'] == 'onbekend':
                metadata +=  'ingevoerd als gedetermineerd.' 
            else:
                metadata +=  obs['validation status'] + '.' 
        if obs['notes'] != '':
            metadata += ' ' + (obs['notes'] + ".").replace('..', '.')
        metadata += '\n'
        return metadata

    def download_observation_images(self, url, folder, metadataparm):
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
                    img_name = obj.attrs['content'].split('?')[0]
                    # print(f"{os.path.split(img_name)}")
            except:
                pass
        # remember path
        waarnemingpath = os.path.split(img_name)[0]
        # all pictures (including the main)
        for obj in images:
            try:
                if obj.attrs['alt'] in ('foto bij waarneming', 'photo for observation'):
                #if obj.attrs['alt'] in ('photo for observation'):
                    try:
                        img_name = obj.attrs['src'].split('?')[0]
                        filename = os.path.split(img_name)[1]
                        imgurl = urljoin(waarnemingpath, 'photo/'+filename)
                        if url not in urlsdone:
                            urlsdone.append(imgurl)
                            img_response = requests.get(imgurl)
                            img_name = os.path.join(folder, imgurl.split('/')[-1])
                            with open(img_name, 'wb') as f:
                                f.write(img_response.content)
                            logger.info(f'Downloaded: {img_name} from {imgurl}')
                            with exiftool.ExifTool() as et: 
                                # Write metadata to the file 
                                exifparm = metadataparm.encode()
                                et.execute(exifparm, img_name.encode() )
                            # Confirm metadata added
                            with exiftool.ExifToolHelper() as et:
                                result = et.get_metadata(img_name.encode())
                                for tag in result[0]:
                                    if tag == 'Composite:GPSPosition' or tag == 'Composite:ImageSize' or tag == 'IPTC:Keywords':
                                        logger.info(f'{tag}: {result[0][tag]}')
                    except Exception as e:
                        logger.warning(f"{parser.description} failed: {e}")     
            except:
                pass            

    # download images for observations that are approved
    def download_images(self):
        # run through all observations and download pages
        for obs in self.observations:
            url = obs['link']
            folder = 'downloaded_images'
            if str(obs['has photos']).upper() == 'TRUE':
                logger.info(f"{obs['date']} {obs['species name']} - {obs['scientific name']} has photo(s).")
                metadata = self.makeEXIFMetadata(obs)
                if metadata != None:
                    self.download_observation_images(url,folder,metadata)
            else:
                logger.info(f"{obs['date']} {obs['species name']} {obs['scientific name']} has no photos.")

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
    MyProject.addTemplate("blog-waarneming.ini")  

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
    waarnemingen.read("C:/Users/gilbe/OneDrive/foto/observations-gilbert-houtekamer_alles.csv")
    #waarnemingen.read("C:\\Users\\gilbe\\OneDrive\\waarneming.csv")
    waarnemingen.download_images()
    #return

    # run through all observations and and move images to directories per month
    #  Get a list of all .jpg files in the directory 
    jpg_files = glob.glob(os.path.join('downloaded_images', '*.jpg'))

    for file_path in jpg_files:
        try:
            # read metadata from this JPG file
            with exiftool.ExifToolHelper() as et:
                result = et.get_metadata(file_path.encode())
                if 'EXIF:DateTimeOriginal' in result[0]:
                    tag = result[0]['EXIF:DateTimeOriginal']
                    # target directory, make sure it exists
                    targetdir = os.path.join('C:\\CurrentPictures', 'waarneming.nl', str(tag[0:8]).replace(':','-') + 'xx')
                    #targetdir = os.path.join('C:\\CurrentPictures', 'waarneming.nl', str(tag[3:10]).replace(':','-') + '-xx')
                    # Create all directories in the path if they do not exist 
                    if not os.path.exists(targetdir):
                        os.makedirs(targetdir)
                    # make generic blog.ini
                    if not os.path.exists(os.path.join(targetdir, 'blog.ini')):
                        blog_lines = MyProject.getTemplateLines("blog-waarneming.ini")
                        with open(os.path.join(targetdir, 'blog.ini'), "w", encoding='UTF-8') as blog_file:
                            for line in blog_lines:
                                line = line.replace('%%200%%', str(tag[0:7])).replace(':','-').replace('\n','')
                                print(line, file=blog_file)
                        blog_file.close()
                    # copy pictures to the proper directory
                    targetstr =  os.path.join(targetdir, result[0]['File:FileName'])
                    sourcestr = os.path.join('downloaded_images', result[0]['File:FileName']) 
                    shutil.copy(sourcestr, targetstr)
                    logger.info(f"shutil.copy({sourcestr}, {targetstr})")
        except Exception as e:
            logger.warning(f"{parser.description} failed: {e}")
            pass

#     for obs in waarnemingen.observations:
#         url = obs['link']
#         folder = 'downloaded_images'
#         if str(obs['has photos']).upper() == 'TRUE':
#             logger.info(f"{obs['date']} {obs['species name']} - {obs['scientific name']} has photo(s).")
#             metadata = waarnemingen.makeEXIFMetadata(obs)
#             if metadata != None:
# #                waarnemingen.download_observation_images(url,folder,metadata)
#                 # create directory YYYY-MM-xx
#                 directoryname = os.path.join('C:/CurrentPictures', 'waarneming.nl', obs['date'][0:8] + 'xx')
#                 sourcestr = os.path.join('downloaded_images', obs['filename'])
# #               shutil.copy(sourcestr, targetstr)
#         targetstr = MyProject.getBiotaBlogDir() + MyProject.Blog.getBlogDay() + '/' + filedata['File:FileName']
#         #print(f'from {sourcestr} to {targetstr}')
#         if not MyProject.getSkipJPGs():
#             shutil.copy(sourcestr, targetstr)
#             jpgResizeCopy(targetstr, horizontalsize = 600)
#         else:
#             logger.info(f"{obs['date']} {obs['species name']} {obs['scientific name']} has no photos.")

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
    logger.info(f'Total time for all other work {totaltime:.1f} seconds.')

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
