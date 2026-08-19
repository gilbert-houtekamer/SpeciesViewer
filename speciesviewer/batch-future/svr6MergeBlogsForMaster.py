# need module to make a blog index for master (all trips)
#!/usr/bin/env python3

# svr6MergeBlogsForMaster.py: Merge blogs from all trips into the master blog index.
# This script scans through the trip directories, reads the 'blog_admin.csv' files for each
# trip, and merges the blog information into the master 'blog_admin.csv' in the master database.
# It also updates the blog index page and uploads it to the web if the project is online.
# This is a one-off script to merge blogs from all trips into the master blog index.
# In the GUI, this function is not directly available, but it can be used as part of the maintenance process 
# after adding trips and their blogs to the master database.

# this relates to the "Trip Editor" in the Project Settings, which allows adding or updating trip 
# information with more control and immediate feedback. 
# The difference is that this script merges the blogs from all trips into the master blog index, 
# while the Trip Editor allows managing trip information on a per-trip basis.

# the trip-editor is safe to use on a laptop, that has only a few trips.  
# svr6MergeBlogsForMaster is needed to merge the blogs from all trips into the master blog index, 
# and is best run on the server where the master database is located, to avoid issues with file \
# paths and permissions.

# why do I need this? Because the blogs are stored per trip, and the master blog index needs to be updated  
# with the blogs from all trips, to be able to show them in the master blog index page.  
# the trip-editor allows adding or updating trip information, but does not merge the blogs from all trips into the master blog index, 
# which is needed to show the blogs in the master blog index page.

# why not just use trip-editor?  Do we need to merge the blogs from all trips into the master blog index?  
# Yes, because the blogs are stored per trip, and the master blog index needs to be updated with the blogs 
# from all trips, to be able to show them in the master blog index page.  
# The trip-editor allows adding or updating trip information, but does not merge the blogs from all trips 
# into the master blog index, which is needed to show the blogs in the master blog index page.

# Process the EXIF metadata and analyze them per picture based on the UniquePictureName
import re
import csv
import os
import sys
import configparser
import time
import logging
import argparse
from pathlib import Path
from datetime import datetime

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    reporoot = os.path.abspath(os.path.join(scriptpath, os.pardir, os.pardir))
    os.chdir(reporoot)
    sys.path.append(os.path.join(reporoot, 'speciesviewer'))

import engine
import engine.projectlib
import engine.specieslib
import engine.speciesclassification
from engine.specieslib import Level
import engine.triplib
import engine.bloglib
from utils.ini_io import read_ini

logger = logging.getLogger("speciesviewer")
result_dict = {}
headers = []
counter = 1
newcounter = 1
jpgneeded = 0
Reference_sp = None

# read parameters, but will be overwritten probably
def readConfig():
    config = configparser.ConfigParser()
    read_ini(config, INI_LOCATION)
    return config

# convert text with special characters
def getUTF8(ansistring):
    workstr = ansistring
    try:
        workstr = workstr.encode('latin1').decode('utf-8')          # we should validate this upon input
        workstr = workstr.replace("<female>", "♀")
        workstr = workstr.replace("<male>", "♂")
    except:
        pass  # too bad, we tried
    return workstr

def main(args, config):
    global starttime, SpeciesDebug, Reference_sp
    starttime = time.time()
    SpeciesDebug = 0        # default   
    SpeciesDebug = int(config.get("debug","SpeciesFound"))

    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))

    blog = engine.bloglib.Blog(MyProject)
    trip = engine.triplib.Trip(MyProject, config)

    # run through the projects, and add the blogs from the trips
    countBlogs  = {}
    countTrips = {}
    directory_to_scan = os.path.join(Path(config.get("repository","BiotaDatabase")).parent, "trips")
    for root, dirs, files in os.walk(directory_to_scan):
        for file in files:
            if file.endswith('blog_admin.csv'):
                file_path = os.path.join(root, file)        # fully qualified name
                # blog.read_BlogAdmin(file_path)              # read in 
                with open(file_path, mode="r", newline="", encoding='utf-8') as csvfile:
                    reader = csv.DictReader(csvfile, delimiter=';')
                    #fieldnames=self.new_blog_row.keys(), 
                    for row in reader:
                        if row['blog'] != "":
                            if Path(root).parent not in countBlogs:
                                countBlogs[Path(root).parent] = 1
                            else:
                                countBlogs[Path(root).parent] += 1
            elif file.endswith('trip.ini'):
                file_path = os.path.join(root, file)        # fully qualified name
                tripdict = trip.readTrip(file_path)
                if Path(root) not in countTrips:
                    countTrips[Path(root)] = (tripdict['trip_name'], '/trips/'+tripdict['trip_name']+'/')
    # def getTrip(self, tripname):
    #     return self.trip_dict[tripname]
    # def addTrip(self, row):
    #     self.trip_dict[row['trip_name']] = row
    #     parseddir = root.split('\\')
    #     name = parseddir[-1]        
    #     return
    # read trip csv
    trip.read_TripAdmin()
    # get the "master blogs" 
    blog.read_BlogAdmin()
    # all trips that have blogs
    for blogs in countBlogs:
        tripname = countTrips[blogs][0]
        tripobj = trip.trip_dict[tripname]
        blog.blog_dict[tripname] = {
            'blog':tripname,
            'title_nl':getUTF8(tripobj['title_nl'])+' reis met '+str(countBlogs[blogs])+' blogs',
            'title_en':getUTF8(tripobj['title_en'])+' trip with '+str(countBlogs[blogs])+' blogs',
            'message_nl':getUTF8(tripobj['message_nl']),
            'message_en':getUTF8(tripobj['message_en']),
            'Project':tripobj['trip_name'],
            'SelectedGroup':'',
            'CreatedTime':datetime.today().strftime('%Y-%m-%d %H-%M'),
            'OnlyNewSpecies':'',
            'MinimumScore':0,            
            'BestJPGs':countTrips[blogs][1]+tripobj['trip_picture']         
        }
        print(f'Trip {tripobj} has {blog.blog_dict[tripname]} blogs')  
    
    blog.updateIndexPage()
    blog.write_BlogAdmin()

    if MyProject.online:
        blog.password = config.get("websettings","Sftppwd").strip("'")
        sftp = blog.openSFTP(blog.password)
        blog.uploadBlogIndex(sftp)
        sftp.disconnect()    

    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="svr6MergeBlogsForMaster")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Module Configuration File",
    )
    parser.add_argument(
        "--dir", type=str, default=os.path.join(reporoot, "data"), help="foto directory"
    )
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = args.config
    config = configparser.ConfigParser()
    read_ini(config, INI_LOCATION)
    # init the logging subsystem
    logging.basicConfig(level=logging.INFO)
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
    else:
        logger.info(f"{parser.description} succeeded")
