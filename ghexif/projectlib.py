

import os
import re
from pathlib import Path
from urllib.parse import urljoin
import time
import shutil
from ghexif.exceptions import SVException
from ghexif.handling import read_file, read_pictures
from ghexif.websupport import getTemplateOutputString
from ghexif.specieslib import Level
from ghexif.statemachine import getAllProjectStatus
import ghexif.breadcrumb
import ghexif.translatelib
import requests
import logging

logger = logging.getLogger("speciesviewer")

# provide easy access to all the locations that are referenced without
# globals
class Project:
    # environment
    scriptpath = os.path.dirname(__file__).replace('"', "").replace("'", "")
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir)).replace('"', "").replace("'", "")
    online = False

    # breadcrumb
    breadcrumb = {}
    # blog admin
    Blog = None
    blogday = ""
    # tripadmin
    Trip = None
    MasterRepository = ""
    # web building
    RepositoryBanner = ""
    templates_dict = {}
    all_or_new = "All"
    SkipJPGs = False
    AllSpeciesFiles = {}            # keep source / from for all files used in all species report
    # SpeciesClassification
    sc = None
    sp = None
    ct = None
    files_dict = {}         # all files
    UniquePictureName_dict = {}      
    # debug copies
    SpeciesDebug = 0

    def __init__(self, repository, newpictdir, biotawebroot):
        self.Repository = repository.replace('"', "").replace("'", "")
        self.NewPictDir = newpictdir.replace('"', "").replace("'", "")
        self.BiotaWebRoot = biotawebroot.replace('"', "").replace("'", "")
        self.projectName = Path(self.NewPictDir.replace('*','')).stem
        # global files
        self.files_dict = {}         # all files
        self.UniquePictureName_dict = {}      
        # all pictures, with references to files_dict and species_dict
    	# global flag
        self.online = self.internet_connection()
        if self.online:
            self.ct = ghexif.translatelib.CachedTranslate(self.Repository)
        # build site
        self.addTemplate("vogelordepage-nl.html")    
        self.addTemplate("vogelordepage-en.html")
        self.addTemplate("vogelsoortpage-nl.html")
        self.addTemplate("vogelsoortpage-en.html")        
        self.addTemplate("vogelfoto.html")
        self.addTemplate("intro-nl.html")
        self.addTemplate("intro-en.html")
        self.addTemplate("intro-link-nl.html")
        self.addTemplate("intro-link-en.html")        
        self.addTemplate("allspecies-nl.html")
        self.addTemplate("allspecies-en.html")     

        # blog
        self.addTemplate("blogindex-nl.html")
        self.addTemplate("blogindex-en.html")
        self.addTemplate("oneblogphoto-nl.html")
        self.addTemplate("oneblogphoto-en.html")
        self.addTemplate("blogtext-nl.html")                   
        self.addTemplate("blogtext-en.html")
        self.addTemplate("blogslider-nl.html") 
        self.addTemplate("blogslider-en.html") 
        # trip
        self.addTemplate("tripindex-nl.html")
        self.addTemplate("tripindex-en.html")
        # breadcrumb
        self.breadcrumb = ghexif.breadcrumb.Breadcrumb(self)

    def internet_connection(self):
        try:
            response = requests.get("https://houtekamer.com", timeout=75)
            return True
        except requests.ConnectionError:
            return False         
        
    # custimazation for 
    def getRepositoryBanner(self):
        return self.RepositoryBanner
    def setRepositoryBanner(self, name):
        self.RepositoryBanner = name
    def registerBlog(self, Blog):
        self.Blog = Blog
    def setBlogDay(self, today):
        self.blogday = today + '-'+ self.ProjectName()
    def getBlogDay(self):
        return self.blogday         
    # trips
    def registerTrip(self, Trip):
        self.Trip = Trip
    def getMasterDir(self):
        return self.Trip.trip_dict[0]['master_repository_disk']
    def getRepositoryPath(self):
        return self.Repository    
    def getProjectPath(self):
        return self.NewPictDir
    def ProjectName(self): 
        return self.projectName
    # make a directory for pictures    
    def getMetaDir(self):
        return os.path.join(self.Repository, 'metadata/')
    def getMetaDirReview(self):
        return os.path.join(self.Repository, 'metadata/review/', self.projectName)
    # references to the Web
    def getBiotaDir(self):
        return os.path.join(self.Repository, 'web/')
    def getBiotaBlogDir(self):                          # points to project on disk
        return os.path.join(self.Repository, 'web/biotablog/')
    def setAllOrNew(self, all_or_new):
        self.all_or_new = all_or_new
    def getBiotaAllorNewDir(self):
        if self.all_or_new == "All":
            return self.getBiotaDir()
        else:
            return self.ReviewFrozen()   
    # on the Website
    def getBiotaWebRoot(self):
        return self.BiotaWebRoot
    def getBiotaBlogWeb(self):
        return self.BiotaWebRoot + '/biotablog/'
    # reference to installed software
    def HTMLDirectory(self):
        return os.path.join(self.modpath, "HTMLtemplates/")
    def ReviewFrozen(self):
        return os.path.join(self.Repository, 'metadata' , 'review', self.projectName, 'frozen')
    def setMasterRepository(self, directory):
        self.MasterRepository = directory
    def getMasterRepository(self):
        if self.MasterRepository == '':
            return self.getRepositoryPath()
        else:
            return self.MasterRepository
    def isMasterRepository(self):
        if self.MasterRepository == '':
            return True
        else:
            return False
    # Variables in passable way 
    def setSpeciesDebug(self, value):
        SpeciesDebug = value
        return
    # species_dict (read-only)
    def getSpeciesCount(self, species):
        try:
            spec = self.sp.species_dict[species]
            sum = int(spec['iSpeciesCollectionCount'])+int(spec['iSpeciesThisRunCount'])
        except:
            sum = 0
        return str(sum)
    # give instance from name
    def getSpecies(self, speciesname):
        # call to get scientific name
        return self.sp.species_dict[self.sp.getSpecies(speciesname)]
    
    # Templates for HTML pages
    # Add one file per call
    def addTemplate(self, template_file_name):
        templatefile = open(self.modpath + "/HTMLtemplates/" + template_file_name, "r", encoding='UTF-8')
        soort_template_lines = templatefile.readlines()
        templatefile.close()
        self.templates_dict[template_file_name] = soort_template_lines
    # 
    def getTemplateLines(self, template_file_name):
        return self.templates_dict[template_file_name]
    def setLanguage(self, language):
        self.language = language
    def getLanguage(self):
        return self.language
    def setSkipJPGs(self, skipjpgs):
        self.SkipJPGs = skipjpgs
    def getSkipJPGs(self):
        return self.SkipJPGs       
    # 
    def registerSpeciesClassification(self, sc):
        self.sc = sc
    def registerSpecies(self, sp):
        self.sp = sp
        self.sp.register_sc_with_sp(self.sc)

    #
    def writeSpeciesInfo(self, speciesfilename, commit_counters):
        self.sp.writeSpeciesInfo(speciesfilename, commit_counters)
    def readSpeciesInfo(self, speciesfilename, reset_all_counters):
        self.sp.readSpeciesInfo(speciesfilename, reset_all_counters)

    def getBlogDateFromPictures(self):
        return self.Blog.getBlogDay()
    # keep track of all species JPGs
    def resetAllSpeciesFiles(self):
        self.AllSpeciesFiles = {}
        return
    def addAllSpeciesFiles(self, sourcefile, webtarget):
        self.AllSpeciesFiles[sourcefile] = webtarget
        return
    def getAllSpeciesFiles(self):
        return self.AllSpeciesFiles
    
    def initMasterAndTrip(self, config, inittrip=True):
        try:
            self.MasterRepository = config.get("master","MasterBiotaDatabase")
        except:
            self.MasterRepository = ""
        # if this is a sub-repository, read trip.ini
        if self.MasterRepository != "":
            self.Trip = ghexif.triplib.Trip(self, config)
            if inittrip:
                self.Trip.readAndWriteTripAdmin()                   # not sure we want this for every step?  maybe only 4 and 9?
        return
    
    def readProject(self, getEarlierDays = False, silent=False):
        starttime = time.time()
        metadatadir = self.getMetaDir()
        metadatadirreview = self.getMetaDirReview()

        # get species an existing committed files and picture
        self.readSpeciesInfo(os.path.join(metadatadirreview, "species.csv"), False)
        self.sc.readSpeciesCollection(metadatadir + "active/speciescollection.csv", False)
        read_file(self, metadatadir+"active/data_files.csv")
        read_pictures(self, metadatadir+"active/data_pictures.csv")

        # see what files for non-commtted projected
        if getEarlierDays and self.Trip != None:
            # Collect states
            directory_to_scan = os.path.join(self.getMetaDir(), 'review') 
            days_dict = getAllProjectStatus(self, directory_to_scan, silent=True)
            # read csv for files and pictures that are not yet committed
            for statemachine in days_dict:                      # projects
                if statemachine >= self.ProjectName(): 
                    break
                if not days_dict[statemachine]['WriteCommit'][0]:  # this project is not committed
                    logger.info(f"Reading data from project {statemachine} that has not yet been committed.")
                    read_file(self, os.path.join(metadatadir, "review", statemachine, "data_files.csv"), silent=True)
                    read_pictures(self, os.path.join(metadatadir, "review", statemachine, "data_pictures.csv"))
                    
        thissteptime = time.time()
        logger.info(f"Read all committed data from the main repository in {thissteptime-starttime:.1f} seconds.")

        # read other files and pictures
        RepositoryFileCount = len(self.files_dict)
        RepositoryPictureCount = len(self.UniquePictureName_dict)
        count, from_time, last_time = read_file(self, os.path.join(metadatadirreview, 'data_files.csv'), silent=True)
        read_pictures(self, os.path.join(metadatadirreview, 'data_pictures.csv'))      

        logger.info(f"Read all new data from project in {time.time()-thissteptime:.1f} seconds.")
        return [RepositoryFileCount, RepositoryPictureCount, from_time, last_time]
    
    def makeSpeciesDirectory(self, leaf):
        global debugfile, count
        species = self.getSpecies(leaf)
        biotadatabase = self.getRepositoryPath()
        directoryname = (biotadatabase + species['TargetPath']).lower()
        os.makedirs(directoryname, exist_ok=True)    

        # find all files in this directory, this is an expensive scan, let's find out how slow it is
        for picture in self.UniquePictureName_dict:
            if self.UniquePictureName_dict[picture]['WetSoortNaam'] == leaf:        # we want this
                for filename in self.UniquePictureName_dict[picture]['Files']:
                    try:
                        print(f"File {filename:40}: {self.files_dict[filename]['WindowsFileName']:64} to {biotadatabase+self.UniquePictureName_dict[picture]['TargetPath']+self.files_dict[filename]['File:FileName']}",
                            file=debugfile)
                        sourcefile = self.files_dict[filename]['WindowsFileName']
                        targetfile = biotadatabase+self.UniquePictureName_dict[picture]['TargetPath']+self.files_dict[filename]['File:FileName']                                      
                        shutil.copy(sourcefile, targetfile)
                        #also delete
                        # os.remove(sourcefile)
                        count += 1
                    except:
                        logger.info(f"Species: Directory {(biotadatabase + species['TargetPath']).lower()} of file {species['BestJPG']} could not be copied.")                    
            #print(f"Species: Directory {directoryname} created for {species['ScientificName'] + '.html'}.")
        return

    # create website based on current count setting, now to test iSpeciesThisRunCount
    def makeDirectories(self, speciesname):
        species = self.getSpecies(speciesname)
        # create database to store pictures as needed
        biotadatabase = self.getRepositoryPath()
        directoryname = (biotadatabase + species['TargetPath']).lower()
        os.makedirs(directoryname, exist_ok=True)
        for leaf in sorted(species['Leaves']):
            species = self.getSpecies(leaf)
            if int(species['iPicturesWithJPGsThisRun']) > 0:
                if species['Level'] != Level.SPECIES:
                    self.makeDirectories(leaf)
                else: # only species store actual data
                    self.makeSpeciesDirectory(leaf)
        return

    # web support 
    def makeWebLevel(self, species, all_or_new):
        # Routine works for any level with children, like all Orders in a Class.
        # The steps are: Kingdom, Class, Order, Family, Species
        # So for a report about Birds Orders, must be called from Class Aves. 
        # Hence, this is where the HTML is placed.
        #  class Level(Enum): SPECIES, FAMILY, ORDER, CLASS, PHYLUM, KINGDOM, BIOTA
        #
        try:    
            biota = self.getBiotaDir()
            # find or create directory with Windows API
            directoryname = os.path.join(self.getBiotaAllorNewDir(), species['TargetPath']).lower()
            os.makedirs(directoryname, exist_ok=True)
            # Dutch version
            language = self.getLanguage()
            # breadcrumb for the group pages, from kingdom to family
            self.breadcrumb.clearBreadCrumb()
            if language == 'nl':
                self.breadcrumb.addBCsimple("/nature/intro-nl.html", "Natuur")
                if self.Trip:
                    self.breadcrumb.addBCsimple("/nature/tripindex-nl.html", "Trips")
                    self.breadcrumb.addBCsimple("/trips/" + self.Trip.tripname + "/summaries/all biota species-nl.html", "Trip soorten")
                else:
                    self.breadcrumb.addBCsimple("/nature/summaries/all biota species-nl.html", "Alle soorten")
            else:
                self.breadcrumb.addBCsimple("/nature/intro-en.html", "Nature")
                if self.Trip:
                    self.breadcrumb.addBCsimple("/nature/tripindex-en.html", "Trips")
                    self.breadcrumb.addBCsimple("/trips/" + self.Trip.tripname + "/summaries/all biota species-en.html", "Trip species")
                else:
                    self.breadcrumb.addBCsimple("/nature/summaries/all biota species-en.html", "All species")
            if self.Trip:
                self.breadcrumb.appendLevelBC(species, "/trips/" + self.Trip.tripname + "/")
            else:
                self.breadcrumb.appendLevelBC(species,"/nature/")

            templatefile = self.getTemplateLines("vogelordepage-" + language + ".html")
            with open((directoryname + species['ScientificName'] + '-' + language + '.html').lower(), "w", encoding='UTF-8') as SpeciesHTMLfile:
                for rawline in templatefile:
                    line = getTemplateOutputString(self, species, '', '', rawline.rstrip('\n'))
                    print(line, file=SpeciesHTMLfile)
            SpeciesHTMLfile.close()
        except:
            logger.warning(f"Level:   Directory {(biota + species['TargetPath']).lower()} of file {species['BestJPG']} could not be created.")
        return

    # create website based on current count setting, now to test iSpeciesThisRunCount
    def makeWebDirectories(self, speciesname, all_or_new):
        species = self.getSpecies(speciesname)
        self.setAllOrNew(all_or_new)
        self.makeWebLevel(species, all_or_new)
        for leaf in sorted(species['Leaves']):
            species = self.sp.species_dict[leaf]
            if (int(species['iPicturesWithJPGsThisRun']) + int(species['iPicturesWithJPGsCollection'])) > 0:
                if species['Level'] != Level.SPECIES:
                    # only create directory if is new", or all
                    if all_or_new == 'All' or (int(species['iPicturesWithJPGsThisRun']) > 0):
                        self.makeWebDirectories(leaf, all_or_new)
                # species page updated just in case it is needed
                elif all_or_new == 'All' or (int(species['iPicturesWithJPGsThisRun']) > 0):
                    self.makeSpeciesWebDirectory(leaf, all_or_new)
        return

    def makeSpeciesWebDirectory(self, leaf, all_or_new):
        try:
            species = self.getSpecies(leaf)
            # create file in the species directory
            biota = self.getBiotaDir()
            directoryname = os.path.join(self.getBiotaAllorNewDir(), species['TargetPath']).lower()            
            os.makedirs(directoryname, exist_ok=True)
            language = self.getLanguage()
            # breadcrumb for the species pages
            # breadcrumb
            self.breadcrumb.clearBreadCrumb()
            if language == 'nl':
                self.breadcrumb.addBCsimple("/nature/intro-nl.html", "Natuur")
                if self.Trip:
                    self.breadcrumb.addBCsimple("/nature/tripindex-nl.html", "Trips")
                    self.breadcrumb.addBCsimple("/trips/" + self.Trip.tripname + "/summaries/all biota species-nl.html", "Trip soorten")
                else:
                    self.breadcrumb.addBCsimple("/nature/summaries/all biota species-nl.html", "Alle soorten")
            else:
                self.breadcrumb.addBCsimple("/nature/intro-en.html", "Nature")
                if self.Trip:
                    self.breadcrumb.addBCsimple("/nature/tripindex-en.html", "Trips")
                    self.breadcrumb.addBCsimple("/trips/" + self.Trip.tripname + "/summaries/all biota species-en.html", "Trip species")
                else:
                    self.breadcrumb.addBCsimple("/nature/summaries/all biota species-en.html", "All species")
            if self.Trip:
                self.breadcrumb.appendLevelBC(species, "/trips/" + self.Trip.tripname + "/")
            else:
                self.breadcrumb.appendLevelBC(species,"/nature/")

            vogelsoortpage_lines = self.getTemplateLines("vogelsoortpage-" + language + ".html")
            with open((directoryname + species['ScientificName'] +'-' + language + '.html').lower(), "w", encoding='UTF-8') as SpeciesHTMLfile:
                for rawline in vogelsoortpage_lines:
                    line = getTemplateOutputString(self, species, '', '', rawline.rstrip('\n'))
                    print(line, file=SpeciesHTMLfile)
            SpeciesHTMLfile.close()

            #print(f"Species: Directory {directoryname} created for {species['ScientificName'] + '.html'}.")
        except:
            logger.info(f"Species: Directory {('e:/tempbiota/' + species['TargetPath']).lower()} of file {species['BestJPG']} could not be created.")
        return

    def makeAllSpeciesPage(self, speciesname, language):
        biota = self.getBiotaDir()
        self.setLanguage(language)
        directorytouse = biota + 'summaries/' 
        os.makedirs(directorytouse, exist_ok=True)

        # breadcrumb
        self.breadcrumb.clearBreadCrumb()
        if language == 'nl':
            self.breadcrumb.addBCsimple("/nature/intro-nl.html", "Natuur")
            if self.Trip:
                self.breadcrumb.addBCsimple("/nature/tripindex-nl.html", "Trips")
                self.breadcrumb.addBCsimple("/trips/" + self.Trip.tripname + "/summaries/all biota species-nl.html", "Trip soorten")
            else:
                self.breadcrumb.addBCsimple("/nature/summaries/all biota species-nl.html", "Alle soorten")
        else:
            self.breadcrumb.addBCsimple("/nature/intro-en.html", "Nature")
            if self.Trip:
                self.breadcrumb.addBCsimple("/nature/tripindex-en.html", "Trips")
                self.breadcrumb.addBCsimple("/trips/" + self.Trip.tripname + "/summaries/all biota species-en.html", "Trip species")
            else:
                self.breadcrumb.addBCsimple("/nature/summaries/all biota species-en.html", "All species")
        #biota is implied, do not show this
        #self.breadcrumb.appendLevelBC(self.sp.species_dict[speciesname], "/trips/" + self.Trip.tripname)

        speciesOverview = self.getTemplateLines("allspecies-" + language + ".html")
        with open((biota + 'summaries/all ' + speciesname + ' species-' + language + '.html').lower(), "w", encoding='UTF-8') as speciesOverviewFile:
            for templateline in speciesOverview:
                line = getTemplateOutputString(self, self.sp.species_dict[speciesname], '', '', templateline.rstrip('\n'))
                print(line, file=speciesOverviewFile)
            
            speciesOverviewFile.close()        

        logger.info(f"""Written all Species data to "{(biota + 'summaries/all ' + speciesname + ' species-' + language + '.html').lower()}".""")
        return
    
    # Intro pages, can be used generically
    # 
    def makeIntro(self, simplepage, language):
        # beadcrumb
        self.breadcrumb.clearBreadCrumb()        
        if language == 'nl': natuur = 'natuur' 
        else:  natuur = 'nature'
        if simplepage == "intro":
            self.breadcrumb.addBCsimple("/nature/" + simplepage + '-' + language + ".html", natuur)
            if self.Trip:
                self.breadcrumb.addBCsimple("/nature/tripindex-" + language + ".html", "Trips")
                self.breadcrumb.addBCsimple("/trips/" + self.Trip.tripname + "/" + simplepage + '-' + language + ".html", "Intro")

        simplepage += '-' + language
        speciesOverview = self.getTemplateLines(simplepage + ".html")
        with open((self.getBiotaDir() + simplepage + ".html").lower(), "w", encoding='UTF-8') as IntroFile:
            for templateline in speciesOverview:
                species = self.sp.species_dict["biota"] if self.sp != None else ""
                line = getTemplateOutputString(self, species, '', '', templateline.rstrip('\n'))
                print(line, file=IntroFile)
        
            IntroFile.close()

        logger.info(f"""Written page webpage to "{(self.getBiotaDir() + simplepage + ".html").lower()}".""")
        return
    
    # find the JPGs with highest Rating
    def getBestJPGinList(self, list):
        BestJPG = ""
        BestRating = 0
        try:
            for jpg in list:
                upn = self.files_dict[jpg]['UniquePictureName']
                rate = self.UniquePictureName_dict[upn]['Rate']
                if int(rate) > BestRating:
                    BestRating = int(rate)
                    BestJPG = jpg
        except:
            # no proper contaxt, return first entry
            BestJPG = list[0]
        return BestJPG
    
    #
    # Lots of functions moved to projectlib
    #
    # Find the best JPG for a single picture.  
    # Typical caes are:
    # 1. No JPGs, this most likely
    # 2. One JPG from DxO tool or Lightroom
    # 3. One JPG from DxO tool AND one from Lightroom.
    # In case 3. the DxO JPGs are better (and have a long file name)
    def upnBestJPG(self, filelist):
        bestjpg = ""
        for file in filelist:
            ext = self.files_dict[file]['WindowsExtension']
            if ext in ("jpg", "jpeg"):
                # first JPG always good
                if not bestjpg:
                    bestjpg = file
                # typically PureRaw output has a longer file name and is better than plain Lightroom
                elif len(file)>len(bestjpg):        
                    bestjpg = file            
        return bestjpg


    # Find the most descriptive value for a column (PICTURE_FIELDS) for a single picture.
    def findBestItem(self, filelist, column, preferredtypes):
        bestresult = ""
        # preferred location
        for file in filelist:
            ext = self.files_dict[file]['WindowsExtension']
            if ext.upper() in preferredtypes:
                bestresult = self.files_dict[file].get(column,"")
                if bestresult != "":
                    return bestresult

        return ""

    # first look for a CameraRaw format ("ORF", "NEF", "NRW", "HEIC")
    def findAnySourceItem(self, filelist, column):
        return self.findBestItem(filelist, column, ("ORF", "NEF", "NRW", "HEIC", "JPG", "JPEG", "XMP"))
    def findRawOrJPGItem(self, filelist, column):
        return self.findBestItem(filelist, column, ("ORF", "NEF", "NRW", "HEIC", "JPG", "JPEG"))
    def findCameraOrXMPItem(self, filelist, column):
        return self.findBestItem(filelist, column, ("ORF", "NEF", "NRW", "HEIC", "XMP"))
    def findCameraItem(self, filelist, column):
        return self.findBestItem(filelist, column, ("ORF", "NEF", "NRW", "HEIC"))
    # check for manual input by PhotoMechanic or Lightroom (mostly)
    def findXMPItem(self, filelist, column):
        return self.findBestItem(filelist, column, ("XMP", "JPG"))
    def findDNGItem(self, filelist, column):
        return self.findBestItem(filelist, column, ("DNG"))
    # should look at best JPG first!
    def findJPGItem(self, filelist, column):
        return self.findBestItem(filelist, column, ("JPG", "JPEG"))
    
    def CreatePicture(self, upn):
        PictureRecord = {
            "UniquePictureName": upn,
            "Files":            [],
            "subject":          "",
            "Rating":           "",
            "Label":            "",
            "Rate":             0,
            "WetSoortNaam":     "",
            "EngSoortNaam":     "",
            "NLSoortNaam":      "",
            "ImageSize":        "",     
            "JPGSize":          "",
            "GPSAltitude":      "",
            "GPSPosition":      "",
            "Location":         "",
            "City":             "",
            "State":            "",
            "Country":          "", 
            "CameraCreator":    "", 
            "JPGCreator":       "", 
            "CameraModel":      "", 
            "LensShortName":    "", 
            "LensLongName":     "", 
            "Settings":         "", 
            "Kingdom":          "",
            "Phylum":           "",
            "Class":            "",
            "Order":            "",
            "Family":           "",
            "BestPictureFile":  "", # obsolete?
            "currentHighestRating": 0,
            "BestJPG":      	"",
            "BestJPGPathName":  "",                
            "TargetPath":       "",
            "Description":      "",
            "DateTimeOriginal": "",
            "malefemale":       "",
            "ageintext":        "",
            "summerwinter":     "",
            "errormessage":     "",
        }
        return PictureRecord

    def UpdatePicture(self, upn):
        # files already assigned
        files = upn['Files']
        # fill stuff from the EXIF from the files       
        upn["subject"] =           self.findXMPItem(files, "XMP:Subject")
        if upn["subject"] == "":    # for waarneming.nl
            upn["subject"] =       self.findXMPItem(files, "IPTC:Keywords")
        upn["Rating"] =            self.findXMPItem(files, "XMP:Rating")
        upn["Label"] =             self.findXMPItem(files, "XMP:Label")
        if upn["Rating"] == "" and upn["Label"] == "":  # for waarneming.nl
            upn["Label"] = "Green"
        if upn["ImageSize"] == "": upn["ImageSize"] = self.findCameraOrXMPItem(files, "ImageSize")
        upn["JPGSize"] =           self.findJPGItem(files, "ImageSize")
        if upn["GPSAltitude"] == "": upn["GPSAltitude"] =       self.findAnySourceItem(files, "EXIF:GPSAltitude")
        if upn["GPSPosition"] == "": upn["GPSPosition"] =       self.findAnySourceItem(files, "Composite:GPSPosition")
        upn["Location"] =          self.findXMPItem(files, "XMP:Location")
        upn["City"] =              self.findXMPItem(files, "XMP:City")
        upn["State"] =             self.findXMPItem(files, "XMP:State") 
        upn["Country"] =           self.findXMPItem(files, "XMP:Country") 
        upn["CameraCreator"] =     self.readableCameraModel(self.findXMPItem(files, "CameraModel"))  + " Microcode " + self.findCameraItem(files, "EXIF:Software")
        upn["JPGCreator"] =        self.findJPGItem(files, "XMP:CreatorTool")
        upn["CameraModel"] =       self.readableCameraModel(self.findXMPItem(files, "CameraModel"))
        if upn["CameraModel"] == "": 
            upn["CameraModel"] = self.findRawOrJPGItem(files, "CameraMake")
            if upn["CameraModel"] == "OM Digital Solutions": upn["CameraModel"] = "OM-1"
        if upn["LensShortName"] == "": upn["LensShortName"] =     self.findRawOrJPGItem(files, "EXIF:LensModel")
        if upn["LensLongName"] == "": upn["LensLongName"] =      self.findRawOrJPGItem(files, "Composite:LensID")
        upn["Settings"] = self.findRawOrJPGItem(files, "EXIF:ExposureTime") + " sec at F" + self.findRawOrJPGItem(files, "EXIF:FNumber")  \
                + ", ISO " + self.findRawOrJPGItem(files, "EXIF:ISO") + ", distance " + self.getDistance(files)
        upn["Description"] =       self.findXMPItem(files, "XMP:Description")
        if upn["DateTimeOriginal"] == "": upn["DateTimeOriginal"] =  self.findRawOrJPGItem(files, "EXIF:DateTimeOriginal")
        
        if upn["GPSAltitude"] == "undef": 
            upn["GPSAltitude"] = ""

        upn["malefemale"] = ""
        upn["ageintext"] = ""
        upn["summerwinter"] = ""    
        upn["errormessage"] = ""

        return upn


    # not too many decimals, no more than 3 significant digits
    def getDistance(self, files):
        textdistance = self.findRawOrJPGItem(files, "MakerNotes:FocusDistance")
        if textdistance != '' and textdistance != 'inf':
            try:
                numerictext = textdistance.strip(' m')
                periodposition = numerictext.find('.')
                if periodposition > 0:
                    meters = textdistance[:periodposition]
                    if int(meters) < 100:
                        if int(meters) < 10:
                            if int(meters) < 2:
                                meters = meters + numerictext[periodposition:]
                            else:
                                meters = meters + numerictext[periodposition:periodposition+3]                    
                        else:
                            meters = meters + numerictext[periodposition:periodposition+2]
                    meters += ' m'
                else:
                    return textdistance
                #print(f"{textdistance} -> {meters}")
                return meters
            except:
                return textdistance
        return textdistance


    # more descriptive names
    def readableCameraModel(self, rawname):
        if rawname == "OM-1MarkII":
            rawname = "OM-1 Mark II"
        return rawname

    # addSpeciesInformation for each UniquePictureName
    #
    # - Add information from the individual files (UpdatePicture(upn))
    # - Compute the rating for the picture (computeRating(upn))
    # - Read species data and put in the UniquePictureName (upn)
    #   - this includes finding the species name, possibly using the GBIF (getSpecies)
    # - Compute the directory where the picture needs to be moved to 
    #   - this requires a WetSoortNaam, since the path is based on it
    #
    def addSpeciesInformation(self, sp, firstUPNtoProcess):
        ClassificationGroupName = ["Species","Family", "Order", "Class", "Phylum", "Kingdom", "Biota"]
        MyMagic = sp.getMP()
        # Add properties for picture
        if self.SpeciesDebug > 0:  
            filebug = open("addSpeciesInformation.txt", "w")
        recognized_species = 0
        for upn in list(self.UniquePictureName_dict.values())[firstUPNtoProcess:]:
            picture = self.UpdatePicture(upn)
            picture['Rate'] = self.computeRating(upn)
            scientificname = ""
            # Read species data and put in the UniquePictureName (upn)
            for filename in picture['Files']:
                thisfile = self.files_dict[filename]
                if thisfile['XMP:Subject'] != "":
                    subject_in_jpg = thisfile['XMP:Subject']
                else:
                    subject_in_jpg = thisfile['IPTC:Keywords']                    
                if subject_in_jpg != "":
                    scientificname = sp.getSpecies(subject_in_jpg)
                    if scientificname == "":       
                        logger.warning(f"""The species "{subject_in_jpg}" is not found in the GBIF database as a scientific name for "{upn['UniquePictureName']}".""")
                        picture['errormessage'] = f"""The species "{subject_in_jpg}" is not found in the GBIF database as a scientific name for "{upn['UniquePictureName']}"."""
                    elif scientificname == None:
                        logger.warning(f"""Species "{subject_in_jpg}" may have a typo in "{thisfile['WindowsFileName']}", correct it.""")
                        picture['errormessage'] = f"""Species "{subject_in_jpg}" may have a typo in "{thisfile['WindowsFileName']}", correct it."""
                    else:
                        try:           
                            species = MyMagic.getSpecies(scientificname)
                            # these columns are in picture
                            picture['WetSoortNaam'] = species['ScientificName']
                            picture['NLSoortNaam'] = species['DutchName']
                            picture['EngSoortNaam'] = species['EnglishName']
                            picture['Family'] = species['Up']
                            next = sp.species_dict[species['Up']]
                            while next['Up']:       # may need to skip levels
                                picture[ClassificationGroupName[next['Level'].value-1]] = next['Up']
                                next = sp.species_dict[next['Up']]
                            # 
                            upn["malefemale"], upn["ageintext"], upn["summerwinter"] = sp.getBirdInfo(thisfile['XMP:Subject'])

                            # picture['Class'] = next['Up']
                            # next = species_dict[next['Up']]
                            # picture['Phylum'] = next['Up']
                            # next = species_dict[next['Up']]                    
                            # picture['Kingdom'] = next['Up']
                            recognized_species += 1                                   
                            break                        
                        except:
                            logger.info(f"KEY NOT FOUND {scientificname} for file {filename}")
                            scientificname = ""

            # now compute path when we have a scientific name
            if picture['WetSoortNaam'] != "":
                picture['TargetPath'] = sp.getPathToSpecies(picture['WetSoortNaam'])
                

            # find JPG (if there is more than 1, find the best JPG)
            for filename in picture['Files']:
                thisfile = self.files_dict[filename]
                rating = 0
                if thisfile['WindowsExtension'] in ['jpg','jpeg']:
                    if filename.find('-small') != -1: continue                          # do not -small picture for BestJPG etc.
                    if (picture['BestJPG'] == "") or ('dxo' in thisfile['File:FileName'].lower()):
                        picture['BestJPGPathName'] = upn['TargetPath']
                        picture['BestJPG'] = thisfile['File:FileName']
                    if (scientificname != ""):
                        if (species['BestJPG'] == "") or \
                            (int(species['currentHighestRating']) < int(picture['Rate'])):
                            species['BestJPGPathName'] = upn['TargetPath']
                            species['BestJPG'] = thisfile['File:FileName']
                            species['BestUPN'] = upn['UniquePictureName']
                            species['TargetPath'] = upn['TargetPath']
                            species['currentHighestRating'] = int(picture['Rate'])      # so we know if there are better pictures
                            if self.SpeciesDebug > 0:
                                    print(f"{picture['WetSoortNaam']:28}: Added file {thisfile['WindowsFileName']} to best {species['BestJPGPathName']}/{species['BestJPG']} {species['JPGmap']}", file=filebug)
                        species['JPGmap'].append(thisfile['File:FileName'])        # at some point this was the highest

        logger.info(f"Species information added for {recognized_species} pictures.")
        return 0

    # Collect the JPGs, the best file per picture.
    # Also update counters while we are at it, for all pictures, also the ones without JPGs 
    def addPicturesToSpecies(self, sp, firstUPNtoProcess, SpeciesDebug):
        if SpeciesDebug > 0:  
            debugfile = open("Species print.txt", "w")
            print(f"Debug enabled for Species analysis at level {SpeciesDebug}.", file=debugfile)
        else:
            debugfile = ""
        # Analyze the current set of pictures, update the statistics
        for upn in list(self.UniquePictureName_dict.values())[firstUPNtoProcess:]:
            #logger.info(f"addPicturesToSpecies: {upn['UniquePictureName']}  {upn['WetSoortNaam']} ")
            if upn['WetSoortNaam'] != "":               # did we find a species
                species = sp.species_dict[upn['WetSoortNaam']]
                species['iPictureThisRunCount'] = int(species['iPictureThisRunCount']) + 1        # add the pictures from this run, starting from 0
                if (species['iSpeciesCollectionCount'] == 0): species['iSpeciesThisRunCount'] = int(1)                        
                if upn['BestJPG'] != "":                # do we have a picture?
                    species['iPicturesWithJPGsThisRun'] = int(species['iPicturesWithJPGsThisRun']) + 1    # add the JPGs from this run, starting from 0

        # at least biota must be present
        if 'biota' not in sp.species_dict:
            raise SVException(f"None of the files have the names of the animals or plants in the keywords, so no pictures can be processed.")

        # Add counters from species to higher levels, tail-recursion works fine
        counters = []
        counters = sp.summarizeSpeciesCounters('biota', counters, debugfile)

        # debug ouput, using Option 2
        sp.printDataForSpecies('biota', debugfile)

        return 0

    # Add statistics file (sf)
    def addMetricsToLog(self, sp, firstUPNtoProcess, filename):
        # open file
        logger.info(f'Write report to "{filename}"')
        with open(filename, "w", newline="", encoding='utf-8') as sf:
            # Add properties for picture
            processedUPNs = 0
            NoGPSPos = 0
            NoLocation = 0
            NoCity = 0
            NoSpeciesGiven = 0
            JPGpresent = 0
            GoodImage = 0
            BadOrDuplicate = 0
            NoColorLabel = 0
            SpeciesNameSet = 0
            SpeciesJPGCount = {}
            # count them
            for upn in list(self.UniquePictureName_dict.values())[firstUPNtoProcess:]:
                processedUPNs += 1
                if upn['GPSPosition'] in ["", None]:
                    NoGPSPos += 1
                if upn['Location'] in ["", None]:
                    NoLocation += 1
                if upn['City'] in ["", None]:
                    NoCity += 1                
                if upn['BestJPG'] != "":
                    JPGpresent += 1
                if upn['Label'] in ["Red", "Yellow", "Green"]:
                    GoodImage += 1
                elif upn['Label'] in ["Blue", "Purple"]:
                    BadOrDuplicate += 1
                else:
                    NoColorLabel += 1
                if upn['WetSoortNaam'] in ["", None]:
                    NoSpeciesGiven += 1
                else:
                    # species given, make sure we add it to dictionary 
                    if upn['WetSoortNaam'] not in SpeciesJPGCount:
                        SpeciesJPGCount[upn['WetSoortNaam']] = [0,0,0,0]
                    pictures, jpgs, starred, yellow_or_red = SpeciesJPGCount[upn['WetSoortNaam']]
                    if upn['Label'] in ["Red", "Yellow"]: yellow_or_red += 1
                    if upn['BestJPG'] != "": jpgs += 1
                    if upn['Rating'] > starred: starred = upn['Rating'] 
                    SpeciesJPGCount[upn['WetSoortNaam']] = [pictures+1, jpgs, starred, yellow_or_red]
            # report
            if processedUPNs:
                logger.info("")
                if NoGPSPos:
                    print(f"No GPS specified for          {NoGPSPos:5} ({int(float(NoGPSPos)/float(processedUPNs)*100)}%) of all images", file=sf)
                else:
                    print(f"GPS specified for all         {processedUPNs:5} images", file=sf)
                if NoLocation:
                    print(f"No Location specified for     {NoLocation:5} ({int(float(NoLocation)/float(processedUPNs)*100)}%) of all images", file=sf)
                else:
                    print(f"Location specified for all    {processedUPNs:5} images", file=sf)
                if NoCity:
                    print(f"No City specified for         {NoCity:5} ({int(float(NoCity)/float(processedUPNs)*100)}%) of all images", file=sf)
                else:
                    print(f"City specified for all        {processedUPNs:5} images", file=sf)
                if NoSpeciesGiven:
                    print(f"No Species name specified for {NoSpeciesGiven:5} ({int(float(NoSpeciesGiven)/float(processedUPNs)*100)}%) of all images", file=sf)
                else:
                    print(f"Species name specified for all {processedUPNs} images", file=sf)
                print(f"JPG specified for             {JPGpresent:5}  ({int(float(JPGpresent)/float(processedUPNs)*100)}%) of all images", file=sf)
                print(f"Species information added for {processedUPNs-NoSpeciesGiven:5} pictures.", file=sf)
                print(f"Interesting or Good quality   {GoodImage:5} ({int(float(GoodImage)/float(processedUPNs)*100)}%)", file=sf)
                print(f"Duplicate or bad quality      {BadOrDuplicate:5} ({int(float(BadOrDuplicate)/float(processedUPNs)*100)}%)", file=sf)
                print(f"No color label rating set     {NoColorLabel:5} ({int(float(NoColorLabel)/float(processedUPNs)*100)}%)", file=sf)

                # verify if JPGs are present for each species
                if len(SpeciesJPGCount) > 0:
                    print("\nSpecies specified in this dir:\n", file=sf)
                    print(f" #Raw  #JPG  Best Color    old Name                             (Engels/Nederlands)", file=sf)
                    for species in SpeciesJPGCount:
                        pictures, jpgcount, starred, yellow_or_red = SpeciesJPGCount[species]
                        knownjpg = sp.species_dict[species]['iPicturesWithJPGsCollection']
                        print(f"{pictures:5} {jpgcount:5} {starred:5} {yellow_or_red:5} {knownjpg:6} {species:32} ({sp.species_dict[species]['EnglishName']} / {sp.species_dict[species]['DutchName']})", file=sf)

        return 0

    def getPictureAgeSexePlumage(self, upn):
        try:
            str = upn['malefemale'] + upn['ageintext'] + upn['summerwinter']
        except:
            str = ""
        return str

    def computeRating(self, upn):
        computation = 0
        DxOUsed = False
        # file type logic
        for filename in upn['Files']:
            file = self.files_dict[filename]
            # copy Label to Rating
            if file['XMP:Rating'] in ['0', ''] and file['XMP:Label'] != '':
                label = file['XMP:Label']
                if label == 'Red':
                    file['XMP:Rating'] = 3
                elif label == 'Yellow':
                    file['XMP:Rating'] = 2                
                elif label == 'Green':
                    file['XMP:Rating'] = 0
            #    
            filetype = file['WindowsExtension']
            if filetype in ['xmp']:
                if file['XMP:Rating'] == "":
                    computation = 5
                else:
                    computation = 15 * (1 + int(file['XMP:Rating']))
            elif filetype in ['dop', 'dng']:
                DxOUsed = True
            elif filetype in ['jpg', 'jpeg']:
                if len(upn['Files']) != 1:
                    if file['XMP:Rating'] == "":
                        computation = 5
                    else:
                        computation = 15 * (1 + int(file['XMP:Rating']))
                elif file['XMP:Rating'] in ['0', ''] and file['XMP:Label'] == "":
                        computation = 3                    

        # save new Rating for Picture
        bestresult = 0
        for filename in upn['Files']:
            rating = self.files_dict[filename]['XMP:Rating']
            if rating != "":
                bestresult = max(bestresult, int(rating))
        upn['Rating'] = bestresult

        # Penalties for very small pics
        saved_multiplier = 0.1
        multiplier = 1
        for filename in upn['Files']:
            file = self.files_dict[filename]
            if file['WindowsExtension'] in ['jpg', 'jpeg'] and file['ImageSize'] != "":
                width_jpg, heigth_jpg = map(int, re.split('[x ]', file['ImageSize']))
                multiplier = float(min(1500, max(width_jpg, heigth_jpg))) / 1500.0
                if upn['ImageSize'] != "":  # should be true
                    width_camera, heigth_camera = map(int, re.split('[x ]',upn['ImageSize']))
                    if ((width_camera >= (1.5 * width_jpg)) or (heigth_camera >= (1.5 * heigth_jpg))):				# most likely better crop
                        multiplier = multiplier * 1.5

            saved_multiplier = max(saved_multiplier, multiplier)

        computation = int(float(computation)*saved_multiplier)             
        if DxOUsed:
            computation += 8
        if upn['Description'] != "":
            computation += 15        
        
        return int(computation)


    # logic from C++ version
    def computeRatingFile(self, file):
        computation = 0
        DxOUsed = False
        # copy Label to Rating
        if file['XMP:Rating'] in ['0', ''] and file['XMP:Label'] != '':
            label = file['XMP:Label']
            if label == 'Red':
                file['XMP:Rating'] = 3
            elif label == 'Yellow':
                file['XMP:Rating'] = 2                
            elif label == 'Green':
                file['XMP:Rating'] = 0
        #    
        filetype = file['WindowsExtension']
        if filetype in ['xmp','jpg', 'jpeg']:
            if file['XMP:Rating'] == "":
                computation = 5
            else:
                computation = 15 * (1 + int(file['XMP:Rating']))

        # Penalties for very small pics
        saved_multiplier = 0.1
        multiplier = 1
        if file['WindowsExtension'] in ['jpg', 'jpeg'] and file['ImageSize'] != "":
            width_jpg, heigth_jpg = map(int, re.split('[x ]', file['ImageSize']))
            multiplier = float(min(1500, max(width_jpg, heigth_jpg))) / 1500.0
        
        saved_multiplier = max(saved_multiplier, multiplier)

        computation = int(float(computation)*saved_multiplier)             
        if "dxo" in file['File:FileName'].lower():
            computation += 8
        
        return int(computation)
    
