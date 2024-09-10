# Process the EXIF metadata and analyze them per picture based on the UniquePictureName
import argparse
import csv
import logging
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
import ghexif.exceptions
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
    # if not statemachine.stateOKtorun("ReadDirExif"):
    #     statemachine.explainAbort("ReadDirExif")

    # # normal mainline 
    # statemachine.updateState("ReadDirExif", True, "Last words from her wisdom.")
    # print(f'statemachine.updateState("ReadDirExif", ok, "Last words from her wisdom.")')
    
    # not so good
    ok = statemachine.stateOKtorun("Freeze")
    print(f'statemachine.stateOKtorun("Freeze") {ok}') 
    if not statemachine.stateOKtorun("Freeze"):
        statemachine.explainAbort("Freeze")
       



# end new test
    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')

if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="Freeze")
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
    statemachine = ghexif.statemachine.StateMachine(config.get("repository","BiotaDatebase"), config.get("project","PictureDirectory"))
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
        statemachine.updateState(parser.description, False, e.args[0])
    else:
        logger.info(f"{parser.description} succeeded")
        statemachine.updataState(parser.description, True, "Succesfully run.")
    # Store state in statemachine file for this project
    statemachine.writeState()
