import argparse
import configparser
import os
from pathlib import Path
import sys
import time
import engine.projectlib
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from engine import SVException
import engine
import engine.projectlib
import engine.specieslib
import engine.speciesclassification
import engine.sftplib
import engine.specieslib
import engine.translatelib
import engine.bloglib
import engine.exceptions
import engine.statemachine
import engine.triplib
from engine import SVException
from utils.ini_io import read_ini
from utils.runtime_files import svconfig_path

logger = logging.getLogger("speciesviewer")

global online

def getUTF8(blogconfig, ansistring):
    workstr = blogconfig.get("blog_text", ansistring)
    try:
        workstr = workstr.encode('latin1').decode('utf-8')          # we should validate this upon input
        workstr = workstr.replace("<female>", "♀")
        workstr = workstr.replace("<male>", "♂")
    except:
        pass  # too bad, we tried
    return workstr

def main(args, config):
    global starttime
    starttime = time.time()

    MyProject = engine.projectlib.Project(config.get("repository","BiotaDatabase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # if trip, initialze master
    MyProject.initMasterAndTrip(config, inittrip=True)

    # do not run this on masters
    if MyProject.MasterRepository == "":         
        raise SVException(f'Use svr6MergeBlogsForMaster to update the blog page for the master repository.')

    # get repository-wide sc and per project sp.
    sc = engine.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)

    logger.info(f"WriteBlog: Create a blog for project {MyProject.ProjectName()}")

    if not MyProject.online:
        raise SVException(f"sv4WriteBlog: blog cannot be created without internet access")


    files, pictures, from_time, last_time = MyProject.readProject()
    thissteptime = time.time()

    # INSERT TESTBED CODE HERE
    MyProject.setSkipJPGs(False)
    MyProject.makeAllSpeciesPage('mammalia','nl')
    # END OF TESTBED CODE
     
    totaltime = time.time() - starttime
    logger.info(f'Total time for all other work {totaltime:.1f} seconds.')



if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="Testbed")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG") or str(svconfig_path()),
        help="Testbed Driver",
    )
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = args.config
    config = configparser.ConfigParser()
    read_ini(config, INI_LOCATION)
    main(args, config)
