import argparse
import configparser
import os
from pathlib import Path
import sys
import time
import ghexif.projectlib

INI_LOCATION = "svconfig.ini"

# pass ini file to module
parser = argparse.ArgumentParser(description="Web site Demo.")
parser.add_argument(
    "--config",
    type=str,
    default=os.environ.get("GHCONFIG", "/etc/ghconfig.ini"),
    help="Gilbert Houtekamer configuration file",
)
args = parser.parse_args()
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
from ctypes import *

global online

def getUTF8(blogconfig, ansistring):
    workstr = blogconfig.get("blog_text", ansistring)
    workstr = workstr.encode('latin1').decode('utf-8')
    workstr = workstr.replace("<female>", "♀")
    workstr = workstr.replace("<male>", "♂")        
    return workstr

def main(args):
    global starttime, config
    starttime = time.time()
    # read ini file
    config = configparser.ConfigParser()
    config.read(INI_LOCATION)

    MyProject = ghexif.projectlib.Project(config.get("repository","BiotaDatebase"), 
                                          config.get("project","PictureDirectory"),
                                          config.get("repository","BiotaWebRoot"))
    # get repository-wide sc and per project sp.
    sc = ghexif.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = ghexif.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)   

    files, pictures, from_time, last_time = MyProject.readProject()

    thissteptime = time.time() 

    bloginifile = MyProject.getProjectPath() + "/blog.ini"
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
    print(f'sv4WriteBlog: Total time for all other work {totaltime:.1f} seconds.')

if __name__ == "__main__":
     main(parser.parse_args())