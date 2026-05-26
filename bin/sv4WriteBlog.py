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

from ghexif import SVException
import ghexif
import ghexif.projectlib
import ghexif.specieslib
import ghexif.speciesclassification
import ghexif.sftplib
import ghexif.specieslib
import ghexif.translatelib
import ghexif.bloglib
import ghexif.exceptions
import ghexif.statemachine
import ghexif.triplib
from ghexif import SVException

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

    logger.info(f"sv4WriteBlog: Create a blog for project {MyProject.ProjectName()}")

    if not MyProject.online:
        raise SVException(f"sv4WriteBlog: blog cannot be created without internet access")


    files, pictures, from_time, last_time = MyProject.readProject()

    thissteptime = time.time()

    # bloginifile = MyProject.getProjectPath() + "/blog.ini"
    bloginifile = os.path.join(MyProject.getProjectPath(), "blog.ini")
    if os.path.exists(bloginifile):
        # RawConfigParser disables %-interpolation so prose like "40% less"
        # in blog text doesn't trip InvalidInterpolationError. Encoding is
        # independent of the parser class — UTF-8 (é, à, etc.) handled fine.
        blogconfig = configparser.RawConfigParser()
        try:
            blogconfig.read(bloginifile, encoding="utf-8")
        except UnicodeDecodeError:
            blogconfig.read(bloginifile, encoding="latin-1")
        # set the parameters
        only_new = blogconfig.getboolean("blog_config","only_new")
        minimum_score = blogconfig.getint("blog_config","minimum_score")
        blog_column_width = blogconfig.getint("blog_config","column_width")
        selectspecies= blogconfig.get("blog_selection","species")
        if "blogjpg" in blogconfig["blog_text"]:
            blogjpg = blogconfig.get("blog_text","blogjpg")
        else:
            blogjpg = ""
        titel = getUTF8(blogconfig,"titel_nl")
        message = getUTF8(blogconfig,"message_nl")
        titel_en = getUTF8(blogconfig,"titel_en")
        message_en = getUTF8(blogconfig,"message_en")

    else:
        only_new = False
        minimum_score = 25
        blog_column_width = 300        
        selectspecies = "biota"
        blogjpg = ""
        titel = "Standaard titel"
        message = "Hier komt het verhaaltje"
        titel_en = ""
        message_en = ""
    blog = ghexif.bloglib.Blog(MyProject)
    blog.initBlog(titel, titel_en, message, message_en, blogjpg, selectspecies, only_new, minimum_score)
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
        location_cache = os.path.join(MyProject.getRepositoryPath(), 'admin/FTPcache.json')
        sftp.openFTPCache(location_cache)
        blog.uploadBlog(sftp)
        blog.uploadBlogIndex(sftp)
        blog.uploadBlogAdmin(sftp)
        # Disconnect from SFTP
        sftp.closeFTPCache(location_cache)
        sftp.disconnect()

    # remember translations
    blog.mp.ct.writeTranslationCache()
     
    totaltime = time.time() - starttime
    logger.info(f'Total time for all other work {totaltime:.1f} seconds.')
    blog.logExcludedSpecies()

    # Write the blog post URL so the GUI can open it after completion
    blog_url = MyProject.getBiotaBlogWeb() + blog.getBlogDay() + '-nl.html'
    with open("last_blog_url.txt", "w") as f:
        f.write(blog_url)


if __name__ == "__main__":
    # get commandline parameters
    parser = argparse.ArgumentParser(description="WriteBlog")
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
    # define state machine and see if we can run this step
    statemachine = ghexif.statemachine.StateMachine(config.get("repository","BiotaDatabase"), config.get("project","PictureDirectory"))
    # call the actual code
    try:
        if not statemachine.stateOKtorun(parser.description):
            statemachine.explainAbort(parser.description)
        main(args, config)
    except Exception as e:
        logger.warning(f"{parser.description} failed: {e}")
        statemachine.updateState(parser.description, False, e.args[0])
    else:
        logger.info(f"{parser.description} succeeded")
        statemachine.updateState(parser.description, True, "Succesfully run.")
    # Store state in statemachine file for this project
    statemachine.writeState()
