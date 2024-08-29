# no idea what I do :-(
import os
from pathlib import Path
import time
import shutil
from ghexif.handling import read_file, read_pictures
from ghexif import files_dict, UniquePictureName_dict
from ghexif.websupport import getTemplateOutputString
from ghexif.specieslib import Level
import ghexif.translatelib
import requests

# provide easy access to all the locations that are referenced without
# globals
class Project:
    # environment
    scriptpath = os.path.dirname(__file__).replace('"', "").replace("'", "")
    modpath = os.path.abspath(os.path.join(scriptpath, os.pardir)).replace('"', "").replace("'", "")
    online = False

    # blog admin
    Blog = None
    blogday = ""
    # web building
    templates_dict = {}
    all_or_new = "All"
    SkipJPGs = False
    # SpeciesClassification
    sc = None
    sp = None
    ct = None

    def __init__(self, repository, newpictdir, biotawebroot):
        self.Repository = repository.replace('"', "").replace("'", "")
        self.NewPictDir = newpictdir.replace('"', "").replace("'", "")
        self.BiotaWebRoot = biotawebroot.replace('"', "").replace("'", "")
        self.projectName = Path(self.NewPictDir.replace('*','')).stem
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

    def internet_connection(self):
        try:
            response = requests.get("https://houtekamer.com", timeout=75)
            return True
        except requests.ConnectionError:
            return False         

    def registerBlog(self, Blog):
        self.Blog = Blog
    def setBlogDay(self, today):
        self.blogday = today + '-'+ self.ProjectName()
    def getBlogDay(self):
        return self.blogday         

    def getRepositoryPath(self):
        return self.Repository    
    def getProjectPath(self):
        return self.NewPictDir
    def ProjectName(self): 
        return self.projectName
    # make a directory for pictures    
    def getMetaDir(self):
        return self.Repository + 'metadata/'     
    def getMetaDirReview(self):
        return self.Repository + 'metadata/review/' + self.projectName + "/" 
    # references to the Web
    def getBiotaDir(self):
        return self.Repository + 'web/'
    def getBiotaBlogDir(self):                          # points to project on disk
        return self.Repository + 'web/biotablog/'
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
        return self.BiotaWebRoot + 'biotablog/' 
    # reference to installed software
    def HTMLDirectory(self):
        return self.modpath + "/HTMLtemplates/"
    def ReviewFrozen(self):
        return self.Repository + 'metadata/review/' + self.projectName + "/frozen/"
    # Variables in passable way 
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
        templatefile = open(self.modpath + "/HTMLtemplates/" + template_file_name, "r")
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
    
    def readProject(self):
        starttime = time.time()
        metadatadir = self.getMetaDir()
        metadatadirreview = self.getMetaDirReview()
        self.readSpeciesInfo(metadatadirreview + "species.csv", False)
        self.sc.readSpeciesCollection(metadatadir + "active/speciescollection.csv", False)
        
        read_file(metadatadir+"active/data_files.csv")
        read_pictures(metadatadir+"active/data_pictures.csv")

        thissteptime = time.time()
        print(f"Read all committed data from the main repository in {thissteptime-starttime:.1f} seconds.")

        # get all data
        # RepositoryFileCount = len(files_dict)
        # RepositoryPictureCount = len(UniquePictureName_dict)
        # read other files and pictures
        RepositoryFileCount = len(files_dict)
        RepositoryPictureCount = len(UniquePictureName_dict)
        count, from_time, last_time = read_file(metadatadirreview+'data_files.csv')
        read_pictures(metadatadirreview+'data_pictures.csv')      

        print(f"Read all new data from project in {time.time()-thissteptime:.1f} seconds.")
        return [RepositoryFileCount, RepositoryPictureCount, from_time, last_time]
    
    def makeSpeciesDirectory(self, leaf):
        global debugfile, count
        species = self.getSpecies(leaf)
        biotadatabase = self.getRepositoryPath()
        directoryname = (biotadatabase + species['TargetPath']).lower()
        os.makedirs(directoryname, exist_ok=True)    

        # find all files in this directory, this is an expensive scan, let's find out how slow it is
        for picture in UniquePictureName_dict:
            if UniquePictureName_dict[picture]['WetSoortNaam'] == leaf:        # we want this
                for filename in UniquePictureName_dict[picture]['Files']:
                    try:
                        print(f"File {filename:40}: {files_dict[filename]['WindowsFileName']:64} to {biotadatabase+UniquePictureName_dict[picture]['TargetPath']+files_dict[filename]['File:FileName']}",
                            file=debugfile)
                        sourcefile = files_dict[filename]['WindowsFileName']
                        targetfile = biotadatabase+UniquePictureName_dict[picture]['TargetPath']+files_dict[filename]['File:FileName']                                      
                        shutil.copy(sourcefile, targetfile)
                        #also delete
                        # os.remove(sourcefile)
                        count += 1
                    except:
                        print(f"Species: Directory {(biotadatabase + species['TargetPath']).lower()} of file {species['BestJPG']} could not be copied.")                    
            #print(f"Species: Directory {directoryname} created for {species['ScientificName'] + '.html'}.")
        return

    # create website based on current count setting, now to test iSpeciesThisRunCount
    def makeDirectories(self, speciesname):
        species = self.getSpecies(speciesname)
        # create database to store pictures as needed
        biotadatabase = self.getRepositoryPath()
        directoryname = (biotadatabase + species['TargetPath']).lower()
        os.makedirs(directoryname, exist_ok=True)
        for leaf in species['Leaves']:
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
            directoryname = (self.getBiotaAllorNewDir() + species['TargetPath']).lower()
            os.makedirs(directoryname, exist_ok=True)
            # Dutch version
            language = self.getLanguage()
            templatefile = self.getTemplateLines("vogelordepage-" + language + ".html")
            with open((directoryname + species['ScientificName'] + '-' + language + '.html').lower(), "w", encoding='UTF-8') as SpeciesHTMLfile:
                for rawline in templatefile:
                    line = getTemplateOutputString(self, species, '', '', rawline.rstrip('\n'))
                    print(line, file=SpeciesHTMLfile)
            SpeciesHTMLfile.close()
        except:
            print(f"Level:   Directory {(biota + species['TargetPath']).lower()} of file {species['BestJPG']} could not be created.")
        return

    # create website based on current count setting, now to test iSpeciesThisRunCount
    def makeWebDirectories(self, speciesname, all_or_new):
        species = self.getSpecies(speciesname)
        self.setAllOrNew(all_or_new)
        self.makeWebLevel(species, all_or_new)
        for leaf in species['Leaves']:
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
            directoryname = (self.getBiotaAllorNewDir() + species['TargetPath']).lower()            
            os.makedirs(directoryname, exist_ok=True)
            language = self.getLanguage()
            vogelsoortpage_lines = self.getTemplateLines("vogelsoortpage-" + language + ".html")
            with open((directoryname + species['ScientificName'] +'-' + language + '.html').lower(), "w", encoding='UTF-8') as SpeciesHTMLfile:
                for rawline in vogelsoortpage_lines:
                    line = getTemplateOutputString(self, species, '', '', rawline.rstrip('\n'))
                    print(line, file=SpeciesHTMLfile)
            SpeciesHTMLfile.close()

            #print(f"Species: Directory {directoryname} created for {species['ScientificName'] + '.html'}.")
        except:
            print(f"Species: Directory {('e:/tempbiota/' + species['TargetPath']).lower()} of file {species['BestJPG']} could not be created.")
        return

    def makeAllSpeciesPage(self, speciesname, language):
        biota = self.getBiotaDir()
        self.setLanguage(language)
        directorytouse = biota + 'summaries/' 
        os.makedirs(directorytouse, exist_ok=True)

        speciesOverview = self.getTemplateLines("allspecies-" + language + ".html")
        with open((biota + 'summaries/all ' + speciesname + ' species-' + language + '.html').lower(), "w", encoding='UTF-8') as speciesOverviewFile:
            for templateline in speciesOverview:
                line = getTemplateOutputString(self, self.sp.species_dict[speciesname], '', '', templateline.rstrip('\n'))
                print(line, file=speciesOverviewFile)
            
            speciesOverviewFile.close()        

        print(f"""Written all Species data to "{(biota + 'summaries/all ' + speciesname + ' species-' + language + '.html').lower()}".""")
        return
    
    # Intro pages, can be used generically
    def makeIntro(self, simplepage):
        speciesOverview = self.getTemplateLines(simplepage + ".html")
        with open((self.getBiotaDir() + simplepage + ".html").lower(), "w", encoding='UTF-8') as IntroFile:
            for templateline in speciesOverview:
                line = getTemplateOutputString(self, self.sp.species_dict["biota"], '', '', templateline.rstrip('\n'))
                print(line, file=IntroFile)
        
            IntroFile.close()  
        print(f"""Written page webpage to "{(self.getBiotaDir() + simplepage + ".html").lower()}".""")
        return