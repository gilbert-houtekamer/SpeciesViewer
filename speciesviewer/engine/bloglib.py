import os
import io
from pathlib import Path
import csv
from datetime import datetime, date
from collections import OrderedDict
import engine.projectlib
import logging

import engine
import engine.projectlib
import engine.specieslib
import engine.speciesclassification
import engine.sftplib
from engine.websupport import getTemplateOutputString
import engine.translatelib
from engine.handling import parseListFromCSV
from engine.specieslib import Level

logger = logging.getLogger("speciesviewer")


# The blog_admin.csv row shape, in column order. This used to be a class-level
# dict on Blog (`new_blog_row`) that prepareBlogCreation filled in and then
# handed straight to addBlog — so every blog day registered in one process was
# THE SAME dict object, and blog_dict held N aliases of one row. Writing
# blog_admin.csv then emitted the last day's values for every line.
# svv3WriteBlog._register_day_blog already worked around this by hand.
# Build a fresh row per day with new_blog_row(); take the column order from
# BLOG_CSV_FIELDS.
_BLOG_ROW_DEFAULTS = {
    'blog':           "",
    'title_nl':       "Foto's van de laatste 12 jaar",
    'title_en':       'Photos from the last 12 years',
    'message_nl':     '',
    'message_en':     '',
    'blogjpg':        '',
    'Project':        '',
    'CreatedTime':    '',
    'SelectedGroup':  '',
    'OnlyNewSpecies': '',
    'MinimumScore':   '',
    'BestJPGs':       [],
}

BLOG_CSV_FIELDS = tuple(_BLOG_ROW_DEFAULTS)


def new_blog_row():
    """A fresh blog_admin row. Never share one row between blog days."""
    row = dict(_BLOG_ROW_DEFAULTS)
    row['BestJPGs'] = []          # own list, not the template's
    return row


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
    CountJPG = 1
    blogjpg = ""
    column_width = 300

    def __init__(self, MyProject):
        MyProject.registerBlog(self)
        self.mp = MyProject
        # blog_dict is declared as a CLASS attribute above, so without this every
        # Blog instance shared ONE dict for the whole app session. read_BlogAdmin
        # only ever ADDS (addBlog), never clears — so a blog pruned from
        # blog_admin.csv (e.g. an empty day) lingered in the shared dict from an
        # earlier instance and kept reappearing in every rebuilt index. Give each
        # instance its own dict so a rebuild reflects exactly the current CSV.
        self.blog_dict = {}
        return

    def initBlog(self, 
                 title_nl, title_en, message_nl, message_en, blogjpg,
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
        self.blogjpg = blogjpg
        self.BestJPGs = []
        self.excluded_species = {"no_jpg": [], "below_score": []}

        return

    def logExcludedSpecies(self):
        if self.excluded_species["below_score"]:
            logger.warning(f"{len(self.excluded_species['below_score'])} species excluded from blog "
                           f"(score below {self.MinimumScore}): "
                           + ", ".join(f"{name} ({score})" for name, score in self.excluded_species["below_score"]))
        if self.excluded_species["no_jpg"]:
            logger.info(f"{len(self.excluded_species['no_jpg'])} species have no corresponding JPGs "
                        f"and will therefore not be shown.")
    
    # keep name for blog
    def setBlogDay(self, today):
        if self.OnlyNewSpecies: newspec = "-1"
        else: newspec = "-0"
        self.blogday = (today + '-'+ self.mp.ProjectName() + '-' + self.SelectedSpeciesGroup + newspec)
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
        return sorted(self.blog_dict.keys())
    def getBlog(self, blogname):
        return self.blog_dict[blogname]
    def addBlog(self, row):
        self.blog_dict[row['blog']] = row
        return
    def setBlogHeader(self, headers):
        self.blog_csv_headers = BLOG_CSV_FIELDS
        return
    def getBlogHeader(self):
        return self.blog_csv_headers 
    def getBlogAdminFile(self):
        return os.path.join(self.mp.getRepositoryPath(),'admin/blog_admin.csv')
    def getBlogAdminWeb(self):
        return self.mp.getBiotaWebRoot() + '/admin/blog_admin.csv'
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
            # A FRESH row per blog day — see new_blog_row().
            row = new_blog_row()
            row['blog'] = self.blogday
            row['title_nl'] = self.TitleNl
            row['title_en'] = self.TitleEn
            row['message_nl'] = self.MessageNl
            row['message_en'] = self.MessageEn
            row['blogjpg'] = self.blogjpg
            row['Project'] = self.mp.ProjectName()
            row['CreatedTime'] = datetime.today().strftime('%Y-%m-%d %H-%M')
            row['SelectedGroup'] = self.SelectedSpeciesGroup
            row['OnlyNewSpecies'] = self.OnlyNewSpecies
            row['MinimumScore'] = self.MinimumScore
            # ⚠ A LIVE REFERENCE, deliberately not a copy. prepareBlogCreation runs
            # BEFORE makeRunSummaryPost, which is what fills BestJPGs (addBestJPG).
            # The row must see those later appends or write_BlogAdmin stores [] and
            # the blog index renders "(picture not found)". This is safe precisely
            # because each day now gets its own row (new_blog_row) and each day is
            # its own Blog instance — one list, one day.
            row['BestJPGs'] = self.BestJPGs
            # add dictionary to dictionary
            self.addBlog(row)
            # remember, but likely redundant
            self.write_BlogAdmin()
        else:
            row = self.getBlog(self.getBlogDay())
            row['title_nl'] = self.TitleNl 
            row['title_en'] = self.TitleEn 
            row['message_nl'] = self.MessageNl
            row['message_en'] = self.MessageEn  
            row['blogjpg'] = self.blogjpg         
            row['Project'] = self.mp.ProjectName()
            row['CreatedTime'] = datetime.today().strftime('%Y-%m-%d %H-%M')
            row['SelectedGroup'] = self.SelectedSpeciesGroup
            row['OnlyNewSpecies'] = self.OnlyNewSpecies
            row['MinimumScore'] = self.MinimumScore
            row['BestJPGs'] = self.BestJPGs   # live reference — see above

        return

    def dayHasContent(self):
        """True when THIS day's post would actually show something: at least one
        picture IN THE DAY'S TIME WINDOW that carries a recognized species, or a
        landscape 'blog extra' that has a JPG.

        Deliberately checks the day's own pictures, NOT 'biota' in the species
        tree — the tree also holds the master's committed species from other days,
        so it is True even for a species-less day. Conservative: a day with a
        species is kept even if its JPGs aren't rendered yet (it reappears fully
        once they are); only a day with NEITHER a species NOR a landscape photo is
        judged empty and dropped by the caller (removeEmptyDay)."""
        MyProject = self.mp
        from engine.speciesclassification import SpeciesClassification
        try:
            for upn in MyProject.UniquePictureName_dict.values():
                dto = upn.get('DateTimeOriginal', '')
                if not (self.from_time <= dto <= self.last_time):
                    continue
                if (upn.get('WetSoortNaam') or '').strip():
                    return True
                if upn.get('BestJPG') and SpeciesClassification.isBlogExtra(
                        upn.get('subject', '')):
                    return True
        except Exception as e:
            logger.warning(f"dayHasContent check failed: {e}")
            return True   # on doubt, keep the day — never drop content on an error
        return False

    def removeEmptyDay(self):
        """Drop the current content-less day from the blog admin and rebuild the
        index so it no longer appears as an empty post. Server-side post/picture
        artifacts are cleared by the caller via removeBlogArtifacts. Returns True
        when the day was actually registered (this run or a prior one)."""
        present = self.blogday in self.blog_dict
        self.blog_dict.pop(self.blogday, None)
        self.write_BlogAdmin()
        self.updateIndexPage()
        return present

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
            writer = csv.DictWriter(csvfile, fieldnames=BLOG_CSV_FIELDS, delimiter=';')
            writer.writeheader()

            for blogitem in self.blog_dict:
                blog_object = self.blog_dict[blogitem]
                row = {}
                row['blog'] = blog_object['blog'] 
                row['title_nl'] = blog_object['title_nl']
                row['title_en'] = blog_object['title_en']
                row['message_nl'] = blog_object['message_nl']
                row['message_en'] = blog_object['message_en']
                if 'blogjpg' in blog_object:
                    row['blogjpg'] = blog_object['blogjpg']
                else:
                    row['blogjpg'] = ''
                # Keep each row's OWN project and creation time. These used to be
                # overwritten with the current project and "now" for every row on
                # every write, so one write stamped the day being processed onto
                # all the other days' rows (all of kretadb read "2026-03-21").
                # Only a row that has never carried them falls back to current.
                row['Project'] = blog_object.get('Project') or self.mp.ProjectName()
                row['CreatedTime'] = (blog_object.get('CreatedTime')
                                      or datetime.today().strftime('%Y-%m-%d %H-%M'))
                row['SelectedGroup'] = blog_object['SelectedGroup']
                row['OnlyNewSpecies'] = blog_object['OnlyNewSpecies']
                row['MinimumScore'] = blog_object['MinimumScore']
                row['BestJPGs'] = blog_object['BestJPGs']
                writer.writerow(row)  
        return

    def getDateFromPictures(self, fromrecord):
        if len(self.mp.files_dict) == fromrecord:
            fromrecord = 0
        for filedata in list(self.mp.files_dict.values())[fromrecord:]:
            try:
                parsed_date = datetime.strptime(filedata["EXIF:DateTimeOriginal"][0:10], '%Y:%m:%d') 
                return parsed_date.strftime('Blog Pictures %Y-%m-%d')
            except:
                pass
        return ""
    
    # create updated index page
    def updateIndexPage(self):
        MyProject = self.mp
        speciesoftheday = MyProject.getTemplateLines("blogindex-nl.html")
        MyProject.setLanguage('nl')

        MyProject.breadcrumb.clearBreadCrumb()
        MyProject.breadcrumb.addBCsimple("/nature/intro-nl.html", "Natuur")
        MyProject.breadcrumb.addBCsimple("/nature/tripindex-nl.html", "Trips")
        MyProject.breadcrumb.addBCsimple(MyProject.Trip.web_prefix + MyProject.Trip.tripname + "/biotablog/blog index-nl.html", "Blogs Index")

        with open((MyProject.getBiotaBlogDir() + "blog index-nl.html").lower(), "w", encoding='UTF-8') as fileBlogIndex:
            for templateline in speciesoftheday:
                line = getTemplateOutputString(MyProject, None, '', '', templateline.rstrip('\n'))
                print(line, file=fileBlogIndex)
            fileBlogIndex.close()

        speciesoftheday = MyProject.getTemplateLines("blogindex-en.html")
        MyProject.setLanguage('en')
        
        MyProject.breadcrumb.clearBreadCrumb()
        MyProject.breadcrumb.addBCsimple("/nature/intro-en.html", "Nature")
        MyProject.breadcrumb.addBCsimple("/nature/tripindex-en.html", "Trips")
        MyProject.breadcrumb.addBCsimple(MyProject.Trip.web_prefix + MyProject.Trip.tripname + "/biotablog/blog index-en.html", "Blogs Index")

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

        MyProject.breadcrumb.clearBreadCrumb()

        # read all lines from the HTML template
        if language.lower() == 'nl': 
            template = "blogtext-nl.html"
            slidertemplate = "blogslider-nl.html"
            MyProject.breadcrumb.addBCsimple("/nature/intro-nl.html", "Natuur")
            MyProject.breadcrumb.addBCsimple("/nature/tripindex-nl.html", "Trips")
            MyProject.breadcrumb.addBCsimple(MyProject.Trip.web_prefix + MyProject.Trip.tripname + "/biotablog/blog index-nl.html", "Blog Index")
            MyProject.breadcrumb.addBCsimple(MyProject.Trip.web_prefix + MyProject.Trip.tripname + "/biotablog/" +  self.getBlogDay() + "-nl.html",  "Blog " + self.getBlogDay()[25:35] + ' - ' + MyProject.Blog.TitleNl)
        else: 
            template = "blogtext-en.html"
            slidertemplate = "blogslider-en.html"
            MyProject.breadcrumb.addBCsimple("/nature/intro-en.html", "Nature")
            MyProject.breadcrumb.addBCsimple("/nature/tripindex-en.html", "Trips")
            MyProject.breadcrumb.addBCsimple(MyProject.Trip.web_prefix + MyProject.Trip.tripname + "/biotablog/blog index-en.html", "Blog Index")
            MyProject.breadcrumb.addBCsimple(MyProject.Trip.web_prefix + MyProject.Trip.tripname + "/biotablog/" +  self.getBlogDay() + "-en.html",  "Blog " + self.getBlogDay()[25:35] + ' - ' + MyProject.Blog.TitleNl)
          
        self.CountJPG = 1
        speciesoftheday = MyProject.getTemplateLines(template)
        # A day can have JPGs but no recognized species yet (culling-first, or a
        # landscape-only day), so the species tree has no 'biota' root. The direct
        # lookup used to crash the whole website build with KeyError: 'biota'. Fall
        # back to a minimal root instead: the summary token still folds in the
        # landscape "blog extras" (collectBlogExtras) and the species walk degrades
        # gracefully, so the day renders — its landscape photos, or an empty grid —
        # rather than killing the run. Mirrors projectlib's own 'biota'-missing guard.
        species_node = MyProject.sp.species_dict.get(speciesname)
        if species_node is None:
            logger.warning(
                f"makeRunSummaryPost: no '{speciesname}' in the species tree for "
                f"{self.getBlogDay()} (JPG/landscape-only day) — rendering a "
                f"species-less blog post.")
            species_node = {"ScientificName": speciesname, "Leaves": []}
        with open((biotablog + self.getBlogDay() + '-' + language + '.html'), "w", encoding='UTF-8') as fileSummaryPost:
            for templateline in speciesoftheday:
                line = getTemplateOutputString(MyProject, species_node, '', '', templateline.rstrip('\n'))
                print(line, file=fileSummaryPost)
            fileSummaryPost.close()

        
        # also create the sliders, first breadcruumb
        if language.lower() == 'nl':
            titel = MyProject.Blog.TitleNl
        else:
            titel = MyProject.Blog.TitleEn            
        MyProject.breadcrumb.addBCsimple(MyProject.Trip.web_prefix + MyProject.Trip.tripname + "/biotablog/" +  self.getBlogDay() + "-slider-" + language + ".html",  "Slider")
        sliders = MyProject.getTemplateLines(slidertemplate)
        with open((biotablog + self.getBlogDay() + '-slider-' + language + '.html'), "w", encoding='UTF-8') as fileSlider:
            for templateline in sliders:
                line = getTemplateOutputString(MyProject, species_node, '', '', templateline.rstrip('\n'))
                print(line, file=fileSlider)
            fileSlider.close()


        return
    
    def openSFTP(self, pwd):
        MyProject = self.mp
        if MyProject.online:        
            sftp = engine.sftplib.Sftp(
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
        logger.info(f'Create HTML files for blog in slider and text versions.')
        # print(f'{fromfile} to {toweb}')
        sftp.upload(fromfile, toweb)
        sftp.upload(fromfile.replace('-nl','-en'), toweb.replace('-nl','-en'))
        sftp.upload(fromfile.replace('-nl', '-slider-nl'), toweb.replace('-nl', '-slider-nl'))
        sftp.upload(fromfile.replace('-nl','-slider-en'), toweb.replace('-nl','-slider-en'))        

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

    def removeBlogArtifacts(self, sftp, blog_key):
        """Server-side cleanup for one blog: removes the four rendered HTML
        files and the picture folder. Used when pruning blog_admin.csv via
        the Manage Blogs dialog so the published site no longer references
        a blog that the user just removed locally."""
        MyProject = self.mp
        web_root = sftp.remotePrefix() + MyProject.getBiotaBlogWeb()
        for suffix in ("-nl.html", "-en.html", "-slider-nl.html", "-slider-en.html"):
            sftp.remove(web_root + blog_key + suffix)
        sftp.rmtree(web_root + blog_key)
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