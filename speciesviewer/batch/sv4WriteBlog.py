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

    logger.info(f"sv4WriteBlog: Create a blog for project {MyProject.ProjectName()}")

    import engine.connectivity as connectivity
    if not connectivity.publish_reachable():
        raise SVException(connectivity.PUBLISH_UNREACHABLE_MSG)


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
    blog = engine.bloglib.Blog(MyProject)
    blog.initBlog(titel, titel_en, message, message_en, blogjpg, selectspecies, only_new, minimum_score)
    blog.files = files
    blog.pictures = pictures
    blog.from_time = from_time
    blog.last_time = last_time    
    blog.setColumnWidth(blog_column_width)
    blog.password = config.get("websettings","Sftppwd").strip("'")
    sftp = blog.openSFTP(blog.password)

    blog.prepareBlogCreation(sftp)

    # A day can hold JPGs but no recognized species (and no landscape photos) —
    # that renders an EMPTY post. Rather than publish a placeholder, make the day
    # disappear: drop it from the blog admin + index (and remove any post already
    # published for it on the server).
    empty = not blog.dayHasContent()
    result_msg = None
    if empty:
        was_present = blog.removeEmptyDay()
        logger.info(f"{blog.getBlogDay()}: no recognized species and no landscape "
                    f"photos — day removed from the blog"
                    f"{' (prior post pruned)' if was_present else ''}.")
        # A distinct GUI message: nothing was created, so don't claim it was.
        # The prefix makes the trip overview show 🚫 rather than ✅ for this day.
        result_msg = engine.statemachine.nothing_to_do("no species this day — no blog created.")
    else:
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
        if empty:
            # Remove any previously-published post for this now-empty day; harmless
            # when it was never published (the removes just 404).
            try:
                blog.removeBlogArtifacts(sftp, blog.getBlogDay())
            except Exception as e:
                logger.info(f"removeBlogArtifacts (empty day; may not exist yet): {e}")
        else:
            blog.uploadBlog(sftp)
        # Always push the rebuilt index + admin so the removed day drops off the site.
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

    # Write a URL so the GUI can open something after completion: the day's post
    # normally, or the blog index when the (empty) day was removed.
    if empty:
        blog_url = MyProject.getBiotaBlogWeb() + 'blog index-nl.html'
    else:
        blog_url = MyProject.getBiotaBlogWeb() + blog.getBlogDay() + '-nl.html'
    with open("last_blog_url.txt", "w") as f:
        f.write(blog_url)

    # Overrides the step's static "✅ Blog successfully created." message in the GUI
    # (None → the static message is used, for a normally-created blog).
    return result_msg


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
    statemachine = engine.statemachine.StateMachine(config.get("repository","BiotaDatabase"), config.get("project","PictureDirectory"))
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
