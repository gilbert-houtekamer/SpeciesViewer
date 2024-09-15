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
from ghexif.handling import parseListFromCSV        # for blog test
from ghexif import files_dict, UniquePictureName_dict 
import ghexif.specieslib
import ghexif.speciesclassification
import ghexif.bloglib

logger = logging.getLogger(__name__)

def main(args, config):
    global starttime, SpeciesDebug
    starttime = time.time()
    SpeciesDebug = 0        # default   

    SpeciesDebug = int(config.get("debug","SpeciesFound"))

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))

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
# new test: read blog index
    # Get the admin file from disk first
    blog_dict = {}
    try:
        adminfile = os.path.join(MyProject.getRepositoryPath(),  'admin/blog_admin.csv')
        with open(adminfile, mode="r", newline="", encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile, delimiter=';')
            #fieldnames=self.new_blog_row.keys(), 
            for row in reader:
                if row['blog'] != "":
                    row['BestJPGs'] = parseListFromCSV(row['BestJPGs'])
                    try:
                        row['ColumnWidth']
                    except:
                        row['ColumnWidth'] = 300
                    blog_dict[row['blog']] = row
    except:
        pass    # no blogs yet, that's fine
    # If we have something, create every blog page
    if len(blog_dict) == 0:
        logger.warning(f'No blog entries or blog admin found at {adminfile}')
    else:
        # get repository-wide sc and per project sp.
        sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
        MyProject.registerSpeciesClassification(sc)
        sp = ghexif.specieslib.Species(MyProject)
        MyProject.registerSpecies(sp)
        # read applied data
        files, pictures, from_time, last_time = MyProject.readProject()
        for blogrow in blog_dict:
            blogobj = blog_dict[blogrow]
            print(f"{blogrow}: {blog_dict[blogrow]}")
            for file in blogobj['BestJPGs']:
                print(f"{file}: {files_dict[file]['UniquePictureName']} {UniquePictureName_dict[files_dict[file]['UniquePictureName']]['EngSoortNaam']}")
            # # see if we can a blog object
            blog = ghexif.bloglib.Blog(MyProject, blogobj['title_nl'], blogobj['title_en'], 
                                    blogobj['message_nl'],blogobj['message_en'], 
                                    blogobj['SelectedGroup'], blogobj['OnlyNewSpecies'], blogobj['MinimumScore'])
            blog.blogday = blogobj['blog']
            blog.files = files
            blog.pictures = pictures
            blog.from_time = from_time
            blog.last_time = last_time    
            blog.setColumnWidth(300)        # todo: needs to be moved 
            blog.password = config.get("websettings","Sftppwd").strip("'")
            blog.BestJPGs = blogobj['BestJPGs']
            # well...
            MyProject.setSkipJPGs(True)            
            blog.makeRunSummaryPost('biota', 'nl')
            blog.makeRunSummaryPost('biota', 'en')
            MyProject.setSkipJPGs(False)
            sftp = blog.openSFTP(blog.password)
            if MyProject.online:
                blog.uploadBlog(sftp, sftpPictures = False)
                # Disconnect from SFTP
                sftp.disconnect()
            # free class        
            del blog

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
    statemachine = ghexif.statemachine.StateMachine(config.get("repository","BiotaDatebase"), config.get("project","PictureDirectory"))
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
        statemachine.updateState(parser.description, False, e.args[0])
    else:
        logger.info(f"{parser.description} succeeded")
        statemachine.updateState(parser.description, True, "Succesfully run.")
    # Store state in statemachine file for this project
    statemachine.writeState()
