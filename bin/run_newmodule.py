# Process the EXIF metadata and analyze them per picture based on the UniquePictureName
import argparse
import csv
import os
import sys
import configparser
import time
import logging
import argparse

# add directory to python path (only when run as executable)
if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

import ghexif
import ghexif.projectlib
import ghexif.specieslib
import ghexif.speciesclassification
import ghexif.statemachine

logger = logging.getLogger(__name__)

def main(args, config):
    global starttime, SpeciesDebug
    starttime = time.time()
    SpeciesDebug = 0        # default   

    SpeciesDebug = int(config.get("debug","SpeciesFound"))

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"), 
                                          config.get("project","PictureDirectory"),
                                        config.get("repository","BiotaWebRoot"))

# new test
    statemachine = ghexif.statemachine.StateMachine(MyProject.getProjectPath())
    taskstate = statemachine.readState()
    ok = statemachine.stateOKtorun("ReadDirExif")
    print(f'statemachine.stateOKtorun("ReadDirExif") {ok}')
    statemachine.updateState("ReadDirExif", ok, "Last words from her wisdom.")
    print(f'statemachine.updateState("ReadDirExif", ok, "Last words from her wisdom.")')
    
    ok = statemachine.stateOKtorun("Freeze")
    print(f'statemachine.stateOKtorun("Freeze") {ok}')    
    if taskstate != {}:
        statemachine.writeState()

# end new test
    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv01addspecies")
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
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"run_newmodule failed: {e}")
    else:
        logger.info("sv01addspecies succeeded")