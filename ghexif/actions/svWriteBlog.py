import argparse
import configparser
import os
import io
from pathlib import Path
import shutil
import sys
import time
import csv
from datetime import datetime, date
from collections import OrderedDict
import ghexif.projectlib
import pysftp


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

from ghexif import COLUMN_NAMES, SELECTED_FIELDS
from ghexif.csv_columns import PICTURE_FIELDS

#from ghexif.specieslib import readSpeciesInfo, writeSpeciesInfo, computeRating
from ghexif.handling import read_file, read_pictures, parseListFromCSV
from ghexif.specieslib import Level
import ghexif.specieslib
from ghexif.websupport import getTemplateOutputString
import ghexif.translatelib

global online

class Blog:
    blog_dict = {} 
    blogday = ""
    blogname = ""
    blog_csv_headers = []
    mp = None
    files = 0
    pictures = 0

    new_blog_row = {'blog':"", 
                    'title_nl':"Foto's van ", 
                    'title_en':'Photos from ', 
                    'message_nl':'',
                    'message_en':'',
                    'Project':'',
                    'CreatedTime':'',
                    'SelectedGroup':'',
                    'OnlyNewSpecies':'',
                    'MinimumScore':'',
                    'BestJPGs':[]
                    }


    def __init__(self, MyProject, 
                 title_nl, title_en, message_nl, message_en, 
                 selectedgroup, onlynewspecies, minimumscore):
        MyProject.registerBlog(self)
        self.mp = MyProject
        # settings that need to be provided (nl/uk should be Googletranslated)
        # handle language versions
        self.TitleNl, self.TitleEn = self.mp.ct.getBothLanguages(title_nl, title_en)
        self.MessageNl, self.MessageEn = self.mp.ct.getBothLanguages(message_nl, message_en)
        self.SelectedSpeciesGroup = selectedgroup
        self.OnlyNewSpecies = onlynewspecies
        self.MinimumScore = minimumscore
        self.BestJPGs = []

        return 

    # keep name for blog
    def setBlogDay(self, today):
        if self.OnlyNewSpecies: newspec = "-1-" 
        else: newspec = "-0-"
        self.blogday = (today + '-'+ self.mp.ProjectName() + '-' + self.SelectedSpeciesGroup + newspec + str(self.MinimumScore))
        self.mp.setBlogDay(self.blogday)
    def getBlogDay(self):
        return self.blogday 
    def setBlogLanguageSwitch(self, name):
        self.blogname = name
    def getBlogLanguageSwitch(self):
        return self.blogname    
    def existBlog(self, newname):
        if newname in self.blog_dict: return True
        return False
    def getBlogDictSortedKeys(self):
        return reversed(sorted(self.blog_dict.keys()))
    def getBlog(self, blogname):
        return self.blog_dict[blogname]
    def addBlog(self, row):
        self.blog_dict[row['blog']] = row
    def setBlogHeader(self, headers):
        self.blog_csv_headers = self.new_blog_row.keys()
    def getBlogHeader(self):
        return self.blog_csv_headers 
    def getBlogAdminFile(self):
        return self.mp.getRepositoryPath() + 'admin/blog_admin.csv'
    def getBlogAdminWeb(self):
        return self.mp.getBiotaWebRoot() + 'admin/blog_admin.csv'
    def getBlogTitel(self):
        return self.TitleNl
    def getBlogTitle(self):
        return self.TitleEn
    def getBlogBericht(self):
        return self.MessageNl
    def getBlogMessage(self):     
        return self.MessageEn
    def getSelectedSpeciesGroup(self):
        return self.SelectedSpeciesGroup
    def getOnlyNewSpecies(self):
        return self.OnlyNewSpecies  
    def getMinimumScore(self):
        return self.MinimumScore 
    def addBestJPG(self, BestJPG):
        if BestJPG['File:FileName'] not in self.BestJPGs:
            self.BestJPGs.append(BestJPG['File:FileName'])

    def prepareBlogCreation(self, sftp):
        # get existing and new name, and store it
        today = self.getDateFromPictures(self.files)
        self.setBlogDay(today)
        self.read_BlogAdmin(sftp)
        # does it exist? do we want to update admin?
        if not self.existBlog(self.getBlogDay()):
            self.new_blog_row['blog'] = self.blogday
            self.new_blog_row['title_nl'] = "Foto's van "+today[14:]
            self.new_blog_row['title_en'] = "Photos from "+today[14:]
            self.new_blog_row['message_nl'] = self.MessageNl
            self.new_blog_row['message_en'] = self.MessageEn
            self.new_blog_row['Project'] = self.mp.ProjectName()
            self.new_blog_row['CreatedTime'] = datetime.today().strftime('%Y-%m-%d %H-%M')
            self.new_blog_row['SelectedGroup'] = self.SelectedSpeciesGroup
            self.new_blog_row['OnlyNewSpecies'] = self.OnlyNewSpecies
            self.new_blog_row['MinimumScore'] = self.MinimumScore
            self.new_blog_row['BestJPGs'] = self.BestJPGs
            # add dictionary to dictionary
            self.addBlog(self.new_blog_row)
            # remember
            self.write_BlogAdmin()
        else:
            row = self.getBlog(self.getBlogDay())
            row['title_nl'] = self.TitleNl 
            row['title_en'] = self.TitleEn 
            row['message_nl'] = self.MessageNl
            row['message_en'] = self.MessageEn            
            row['Project'] = self.mp.ProjectName()
            row['CreatedTime'] = datetime.today().strftime('%Y-%m-%d %H-%M')
            row['SelectedGroup'] = self.SelectedSpeciesGroup
            row['OnlyNewSpecies'] = self.OnlyNewSpecies
            row['MinimumScore'] = self.MinimumScore
            row['BestJPGs'] = self.BestJPGs

    # might want to get admin from web server?
    # 1. get the file from disk first
    # 2. next get the file from the FTP server
    # 3. add any rows that are not in Blog (blog_dict)
    def read_BlogAdmin(self, sftp):
        # 1. get the file from disk first
        try:
            with io.open(self.getBlogAdminFile(), mode="r", newline="", encoding='utf-8') as csvfile:
                # Read the first line (header) to get column names, and store for write
                header = csvfile.readline().strip().split(';')
                self.setBlogHeader(header)
                # columns that need special treatment
                # read line by line
                for line in csvfile:
                    # Split the line by semicolon
                    values = line.strip().split(';')
                    row_dict = {col: value for col, value in zip(header, values)}
                    if row_dict['blog'] != "":
                        row_dict['BestJPGs'] = parseListFromCSV(row_dict['BestJPGs'])
                        self.addBlog(row_dict)
        except:
            pass    # no blogs yet, that's fine
        # 2. next get the file from the FTP server
        # 3. add any rows that are not in Blog (blog_dict)
        # if self.mp.online:        
        #     try:
        #         self.downloadBlogAdminCopy(sftp)
        #         with io.open(self.getBlogAdminFile()+ '.webcopy', mode="r", newline="", encoding='utf-8') as csvfile:
        #             # Read the first line (header) to get column names, and store for write
        #             header = csvfile.readline().strip().split(';')
        #             for line in csvfile:
        #                 values = line.strip().split(';')
        #                 row_dict = {col: value for col, value in zip(header, values)}
        #                 if row_dict['blog'] not in self.blog_dict:
        #                     # add dictionary to dictionary
        #                     row_dict['BestJPGs'] = parseListFromCSV(row_dict['BestJPGs'])                            
        #                     self.addBlog(row_dict)
        #     except:
        #         pass    # no web blog admin yet, that's fine                   
        return

    # write back 
    def write_BlogAdmin(self):
        # Write Picture CSV file
        print(f'Write all blog data in CSV file "{self.getBlogAdminFile()}".')
        with open(self.getBlogAdminFile(), "w", newline="", encoding='utf-8') as csvfile:
            # needed to get column in order          
            writer = csv.DictWriter(csvfile, fieldnames=self.new_blog_row.keys(), quoting=csv.QUOTE_MINIMAL, delimiter=';')
            writer.writeheader()

            for blogitem in self.blog_dict:
                blog_object = self.blog_dict[blogitem]
                row = {}
                row['blog'] = blog_object['blog'] 
                row['title_nl'] = blog_object['title_nl']
                row['title_en'] = blog_object['title_en']
                row['message_nl'] = blog_object['message_nl']
                row['message_en'] = blog_object['message_en']
                row['Project'] = self.mp.ProjectName()
                row['CreatedTime'] = datetime.today().strftime('%Y-%m-%d %H-%M')
                row['SelectedGroup'] = blog_object['SelectedGroup'] 
                row['OnlyNewSpecies'] = blog_object['OnlyNewSpecies']
                row['MinimumScore'] = blog_object['MinimumScore']
                row['BestJPGs'] = blog_object['BestJPGs']
                writer.writerow(row)  
        return

    def getDateFromPictures(self, fromrecord):
        if len(files_dict) == fromrecord:
            fromrecord = 0
        for filedata in list(files_dict.values())[fromrecord:]:
            parsed_date = datetime.strptime(filedata["EXIF:DateTimeOriginal"][0:10], '%Y:%m:%d') 
            return parsed_date.strftime('Blog Pictures %Y-%m-%d')
        return ""
    
    # create updated index page
    def updateIndexPage(self):
        MyProject = self.mp
        speciesoftheday = MyProject.getTemplateLines("blogindex-nl.html")
        with open((MyProject.getBiotaBlogDir() + "blog index-nl.html").lower(), "w", encoding='UTF-8') as fileBlogIndex:
            for templateline in speciesoftheday:
                line = getTemplateOutputString(MyProject, None, '', '', templateline.rstrip('\n'))
                print(line, file=fileBlogIndex)
            fileBlogIndex.close()

        speciesoftheday = MyProject.getTemplateLines("blogindex-en.html")
        with open((MyProject.getBiotaBlogDir() + "blog index-en.html").lower(), "w", encoding='UTF-8') as fileBlogIndex:
            for templateline in speciesoftheday:
                line = getTemplateOutputString(MyProject, None, '', '', templateline.rstrip('\n'))
                print(line, file=fileBlogIndex)
            fileBlogIndex.close()
            

    def makeRunSummaryPost(self, speciesname, language):
        MyProject = self.mp
        # find date from first picture
        biotablog = MyProject.getBiotaBlogDir()
        MyProject.setLanguage(language)         
        # make a directory for pictures
        directorytouse = biotablog + self.getBlogDay() + '/' 
        os.makedirs(directorytouse, exist_ok=True)

        # read all lines from the HTML template
        if language.lower() == 'nl': 
            otherlanguage = 'en'
            template = "blogtext-nl.html"
        else: 
            otherlanguage = 'nl'
            template = "blogtext-en.html"  
        speciesoftheday = MyProject.getTemplateLines(template)
        with open((biotablog + self.getBlogDay() + '-' + language + '.html'), "w", encoding='UTF-8') as fileSummaryPost:
            for templateline in speciesoftheday:
                line = getTemplateOutputString(MyProject, MyProject.sp.species_dict[speciesname], '', '', templateline.rstrip('\n'))
                print(line, file=fileSummaryPost)
            fileSummaryPost.close()

        return
    
    def openSFTP(self, pwd):
        MyProject = self.mp
        if MyProject.online:        
            sftp = ghexif.sftplib.Sftp(
                hostname='houtfp.ssh.transip.me',
                username='houtekamercom',
                password=pwd,
            )
            # Connect to SFTP
            sftp.connect()
        else:
            sftp= None
        return sftp
    
    def uploadBlog(self, sftp):
        # blog post and pictures
        MyProject = self.mp     
        biotablog = MyProject.getBiotaBlogDir()
        fromfile = biotablog + self.getBlogDay() + '-nl.html'
        toweb = sftp.remotePrefix() + MyProject.getBiotaBlogWeb() + self.getBlogDay() + '-nl.html'
        # print(f'{fromfile} to {toweb}')
        sftp.upload(fromfile, toweb)
        sftp.upload(fromfile.replace('-nl','-en'), toweb.replace('-nl','-en'))
        fromfile = biotablog + 'blog index-nl.html'
        toweb = sftp.remotePrefix() + MyProject.getBiotaBlogWeb() + 'blog index-nl.html'
        # print(f'{fromfile} to {toweb}')
        sftp.upload(fromfile, toweb)
        sftp.upload(fromfile.replace('-nl','-en'), toweb.replace('-nl','-en'))
        fromdir = biotablog + self.getBlogDay()
        towebdir = sftp.remotePrefix() + MyProject.getBiotaBlogWeb() + self.getBlogDay()
        # print(f'{fromdir} to {towebdir}')
        sftp.safeMkdir(sftp.remotePrefix() + MyProject.getBiotaBlogWeb() + self.getBlogDay())
        sftp.put_r_portable(fromdir, towebdir, preserve_mtime=True)
        return
    
    def downloadBlogAdminCopy(self, sftp):
        MyProject = self.mp     
        tofile = self.getBlogAdminFile() + '.webcopy'
        fromweb = sftp.remotePrefix() + MyProject.Blog.getBlogAdminWeb()
        sftp.download(fromweb, tofile)                 
        return

    def uploadBlogAdmin(self, sftp):
        MyProject = self.mp     
        fromfile = self.getBlogAdminFile()
        toweb = sftp.remotePrefix() + MyProject.Blog.getBlogAdminWeb()
        sftp.upload(fromfile, toweb)                
        return        

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

    files, pictures = MyProject.readProject()

    thissteptime = time.time()    

    blog = Blog(MyProject, "Foto's", "", "" , "A short story about Spain.", "biota", False, 5)
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
     main(parser.parse_args())