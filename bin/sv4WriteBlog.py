import argparse
import configparser
import os
from pathlib import Path
import sys
import time
import ghexif.projectlib
import logging

if __name__ == "__main__":
    scriptpath = os.path.dirname(__file__)
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
    os.chdir(modpath)
    sys.path.append(modpath)

from ghexif import files_dict, UniquePictureName_dict, SVException
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

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # get repository-wide sc and per project sp.
    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = ghexif.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)

    logger.info(f"sv4WriteBlog: Create a blog for project {MyProject.ProjectName()}")


    files, pictures, from_time, last_time = MyProject.readProject()

    thissteptime = time.time()

    # bloginifile = MyProject.getProjectPath() + "/blog.ini"
    bloginifile = os.path.join(MyProject.getProjectPath(), "blog.ini")
    if os.path.exists(bloginifile):
        # create a separete config instance
        blogconfig = configparser.ConfigParser()
        blogconfig.read(bloginifile)
        # set the parameters
        only_new = blogconfig.getboolean("blog_config","only_new")
        minimum_score = blogconfig.getint("blog_config","minimum_score")
        blog_column_width = blogconfig.getint("blog_config","column_width")
        selectspecies= blogconfig.get("blog_selection","species")
        titel = getUTF8(blogconfig,"titel_nl")
        message = getUTF8(blogconfig,"message_nl")
        titel_en = getUTF8(blogconfig,"titel_en")
        message_en = getUTF8(blogconfig,"message_en")

    else:
        only_new = False
        minimum_score = 25
        blog_column_width = 300        
        selectspecies = "biota"

        titel = "Standaard titel"
        message = "Hier komt het verhaaltje"
        titel_en = ""
        message_en = ""
    blog = ghexif.bloglib.Blog(MyProject, titel, titel_en, message, message_en, selectspecies, only_new, minimum_score)
    blog.files = files
    blog.pictures = pictures
    blog.from_time = from_time
    blog.last_time = last_time    
    blog.setColumnWidth(blog_column_width)
    blog.password = config.get("websettings","Sftppwd").strip("'")
    sftp = blog.openSFTP(blog.password)

    blog.prepareBlogCreation(sftp)

    # make them
    blog.makeRunSummaryPost('biota', 'nl')
    MyProject.setSkipJPGs(True)
    blog.makeRunSummaryPost('biota', 'en')
    MyProject.setSkipJPGs(False)

    blog.write_BlogAdmin()
    logger.info(f"""Created summary blog in "{MyProject.getBiotaBlogWeb() + blog.getBlogDay() + '/'}" in Dutch and English versions.""")
    blog.updateIndexPage()

    if MyProject.online:
        blog.uploadBlog(sftp)
        blog.uploadBlogAdmin(sftp)
        # Disconnect from SFTP
        sftp.disconnect()

    # remember translations
    blog.mp.ct.writeTranslationCache()
     
    totaltime = time.time() - starttime      
    logger.info(f'Total time for all other work {totaltime:.1f} seconds.')


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="sv4WriteBlog.")
    parser.add_argument(
        "--config",
        type=str,
        default=os.environ.get("SVCONFIG", "svconfig.ini"),
        help="Module Configuration File",
    )
    args = parser.parse_args()
    # get configuration
    INI_LOCATION = args.config
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)
    # call the actual code
    try:
        main(args, config)
    except Exception as e:
        logger.warning(f"sv4WriteBlog failed: {e}")
    else:
        logger.info("sv4WriteBlog succeeded")
