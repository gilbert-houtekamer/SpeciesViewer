import argparse
import configparser
import os
from pathlib import Path
import sys
import time
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from ghexif import files_dict, UniquePictureName_dict
import ghexif
import ghexif.projectlib
import ghexif.specieslib
import ghexif.speciesclassification
import ghexif.sftplib
import ghexif.specieslib
import ghexif.translatelib
import ghexif.bloglib

logger = logging.getLogger(__name__)

global online


def main(args, config):
    global starttime
    starttime = time.time()

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # get repository-wide sc and per project sp.
    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = ghexif.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)   

    files, pictures = MyProject.readProject()

    thissteptime = time.time()    

    blog = ghexif.bloglib.Blog(MyProject, "Foto's", "", "" , "", "biota", False, 5)
    blog.files = files
    blog.pictures = pictures
    blog.password = config.get("websettings","Sftppwd").strip("'")
    sftp = blog.openSFTP(blog.password)

    blog.prepareBlogCreation(sftp)

    # make them
    blog.makeRunSummaryPost('biota', 'nl')
    MyProject.setSkipJPGs(True)
    blog.makeRunSummaryPost('biota', 'en')
    MyProject.setSkipJPGs(False)

    blog.write_BlogAdmin()
    print(f"""Created summary blog in "{MyProject.getBiotaBlogWeb() + blog.getBlogDay() + '/'}" in Dutch and English versions.""")
    blog.updateIndexPage()

    if MyProject.online:
        blog.uploadBlog(sftp)
        blog.uploadBlogAdmin(sftp)
        # Disconnect from SFTP
        sftp.disconnect()

    # remember translations
    blog.mp.ct.writeTranslationCache()
     
    totaltime = time.time() - starttime      
    print(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv4WriteBlog.")
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = "svconfig.ini"
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"sv4WriteBlog failed: {e}")
    else:
        logger.info("sv4WriteBlog succeeded")
