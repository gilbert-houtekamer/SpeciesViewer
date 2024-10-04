import os
import io
from pathlib import Path
import csv
from datetime import datetime, date
from collections import OrderedDict
import ghexif.projectlib
import logging

from ghexif import files_dict
import ghexif
import ghexif.projectlib
import ghexif.specieslib
import ghexif.speciesclassification
import ghexif.sftplib
from ghexif.websupport import getTemplateOutputString
import ghexif.translatelib
from ghexif.handling import parseListFromCSV
from ghexif.specieslib import Level

logger = logging.getLogger(__name__)

class Blog:
    blog_dict = {} 
    blogday = ""
    blogname = ""
    blog_csv_headers = []
    mp = None
    files = 0
    pictures = 0
    from_time = ""
    last_time = ""

    new_blog_row = {'blog':"", 
                    'title_nl':"Foto's van de laatste 12 jaar", 
                    'title_en':'Photos from the last 12 years', 
                    'message_nl':'',
                    'message_en':'',
                    'Project':'',
                    'CreatedTime':'',
                    'SelectedGroup':'',
                    'OnlyNewSpecies':'',
                    'MinimumScore':'',
                    'BestJPGs':[]
                    }


    def __init__(self, MyProject):
        MyProject.registerBlog(self)
        self.mp = MyProject
        return 

    def initBlog(self, 
                 title_nl, title_en, message_nl, message_en, 
                 selectedgroup, onlynewspecies, minimumscore):
        # settings that need to be provided (nl/uk should be Googletranslated)
        # handle language versions
        # TODO allow user to edit proposed translation
        if self.mp.ct == None:
            self.TitleNl = title_nl
            if title_en == "": self.TitleEn = title_nl
            else: self.TitleEn = title_en
            self.MessageNl = title_nl
            if message_en == "": self.MessageEn = message_nl
            else: self.MessageEn = message_nl
        else:
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
        return
    def setBlogHeader(self, headers):
        self.blog_csv_headers = self.new_blog_row.keys()
        return
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
    def setColumnWidth(self, cw):
        self.column_width = cw
        return
    def getColumnWidth(self):
        return self.column_width

    def prepareBlogCreation(self, sftp):
        # get existing and new name, and store it
        today = self.getDateFromPictures(self.files)
        self.setBlogDay(today)
        self.read_BlogAdmin()
        # does it exist? do we want to update admin?
        if not self.existBlog(self.getBlogDay()):
            self.new_blog_row['blog'] = self.blogday
            self.new_blog_row['title_nl'] = self.TitleNl
            self.new_blog_row['title_en'] = self.TitleEn
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
            # remember, but likely redundant
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

        return

    def read_BlogAdmin(self, filename=""):
        # 1. get the file from disk first
        if filename == "":
            filename = self.getBlogAdminFile()
        try:
            with io.open(filename, mode="r", newline="", encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile, delimiter=';')
                #fieldnames=self.new_blog_row.keys(), 
                for row in reader:
                    if row['blog'] != "":
                        row['BestJPGs'] = parseListFromCSV(row['BestJPGs'])
                        self.addBlog(row)
        except:
            pass    # no blogs yet, that's fine
   
        return

    # write back 
    def write_BlogAdmin(self):
        # Write Picture CSV file
        logger.info(f'Write all blog data in CSV file "{self.getBlogAdminFile()}".')
        with open(self.getBlogAdminFile(), "w", newline="", encoding='utf-8') as csvfile:
            # needed to get column in order          
            writer = csv.DictWriter(csvfile, fieldnames=self.new_blog_row.keys(), delimiter=';')
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
            
    def getBestJPGinList(self, blogname):
        BestJPG = self.mp.getBestJPGinList(self.blog_dict[blogname]['BestJPGs'])
        return BestJPG

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
    
    def uploadBlogIndex(self, sftp):
        MyProject = self.mp     
        biotablog = MyProject.getBiotaBlogDir()        
        fromfile = biotablog + 'blog index-nl.html'
        toweb = sftp.remotePrefix() + MyProject.getBiotaBlogWeb() + 'blog index-nl.html'
        # print(f'{fromfile} to {toweb}')
        sftp.upload(fromfile, toweb)
        sftp.upload(fromfile.replace('-nl','-en'), toweb.replace('-nl','-en'))
        return        


    def uploadBlog(self, sftp, sftpPictures = True):
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
        if sftpPictures:
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
    
    #
    def countBlogPictures(self, speciesname, minimumrating):
        count = 1
        species = self.sp.species_dict['biota']
        for leaf in species['Leaves']:
            species = self.sp.species_dict[leaf]
            if int(species['iPictureThisRunCount']) > 0:            # brute force scan
                if species['Level'] == Level.SPECIES:
                    if speciesname in species['TargetPath'] and \
                    (self.getOnlyNewSpecies() and int(species['iSpeciesThisRunCount']) > 0) or (not self.OnlyNewSpecies) and \
                    minimumrating <= species['currentHighestRating']:                 # Last is BUGBUG if high score is from past??
                        count += 1
                else:
                    textCreated = self.countBlogPictures(speciesname, minimumrating)
        return textCreated