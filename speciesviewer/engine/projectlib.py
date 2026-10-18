

import os
import re
from pathlib import Path
from urllib.parse import urljoin
import time
import shutil
from engine.exceptions import SVException
from engine.handling import read_file, read_pictures
from engine.websupport import getTemplateOutputString
from engine.specieslib import Level
from engine.statemachine import getAllProjectStatus
import engine.breadcrumb
import engine.translatelib
import requests
import logging

logger = logging.getLogger("speciesviewer")

# The intro template's tiles: '…/insecta/003 odonata/003 odonata' (ch.6 — the
# number goes when a species list is converted).
_INTRO_TILE = re.compile(r"biota/animalia/arthropoda/insecta/\d+ (odonata|lepidoptera)/\d+ \1")


def intro_taxon_name(names, bare):
    """'003 odonata' when the list still numbers it, else 'odonata'."""
    numbered = sorted(n for n in names if n != bare and n.lstrip("0123456789 ") == bare)
    return numbered[0] if numbered else bare


# The picture columns for the groups above a species, by Level NAME: the
# tree's nodes carry speciesclassification.Level or specieslib.Level — two
# separate enums, never equal to each other. A group the tree skips stays empty.
_TAXON_COLUMN = {"FAMILY": "Family", "ORDER": "Order", "CLASS": "Class",
                 "PHYLUM": "Phylum", "KINGDOM": "Kingdom"}


def taxon_columns(species_dict, species):
    """{'Kingdom': …, 'Family': …} for a species: each group's OWN name in its
    own level's column. (Until 2026-10-18 each column got the group's parent —
    Kingdom 'biota', Family the order — on every picture; only the trip
    registry report read them.) Raises KeyError for a parent missing from the
    tree, as the inline walk did."""
    columns = dict.fromkeys(_TAXON_COLUMN.values(), "")
    node = species_dict[species['Up']]
    while node['Up']:
        column = _TAXON_COLUMN.get(getattr(node['Level'], 'name', ''))
        if column:
            columns[column] = node['ScientificName']
        node = species_dict[node['Up']]
    return columns


def _same_file_on_disk(src, dst):
    """True when src and dst refer to the same on-disk file even if
    the path strings differ (Windows is case-insensitive, mixes
    forward and back slashes, etc.). Falls back to a normalised
    string compare when one path doesn't yet exist — so the
    skip-guard still fires before the copy on a clean target."""
    try:
        if os.path.exists(src) and os.path.exists(dst):
            return os.path.samefile(src, dst)
    except OSError:
        pass
    return (os.path.normcase(os.path.normpath(src))
            == os.path.normcase(os.path.normpath(dst)))


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
        self.BiotaWebRoot = biotawebroot.replace('"', "").replace("'", "").rstrip('/') + '/'
        self.projectName = Path(self.NewPictDir.replace('*','')).stem
        # Per instance, not the class-level {}: addAllSpeciesFiles mutates it in
        # place, so the class dict was shared by every Project the GUI made in
        # one session. Publishing day after day, each day's allspecies.csv then
        # carried every earlier day's entries too, some pointing at frozen JPGs
        # since rebuilt away — a burst of "source missing" on the last day.
        self.AllSpeciesFiles = {}
        # global files
        self.files_dict = {}         # all files
        self.UniquePictureName_dict = {}      
        # all pictures, with references to files_dict and species_dict
    	# global flag
        self.online = self.internet_connection()
        if self.online:
            self.ct = engine.translatelib.CachedTranslate(self.Repository)
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
        self.breadcrumb = engine.breadcrumb.Breadcrumb(self)

    def internet_connection(self):
        """General 'is the network up at all' flag (translation, etc.). Publish
        operations gate on the SFTP server specifically — see engine.connectivity
        (publish_reachable) — not this. Fast TCP connects, so a sluggish web
        server no longer reads as offline."""
        import engine.connectivity as connectivity
        return connectivity.any_reachable()
        
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
        return self.BiotaWebRoot + 'biotablog/'
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
    def hasWebPage(self, species):
        """True when this taxon/species node has (or will have) a page in the
        CURRENT web build tree. Mirrors the render gate in makeWebDirectories,
        so sidebar links (sibling-species / orders-with-photos) never point at
        a page that was never generated. On a trip/album build the web tree
        holds only a subset of the master collection, so guarding on the master
        collection count (getSpeciesCount) emits dead links; this guards on the
        same condition that decides whether the page is actually written."""
        try:
            this_run   = int(species['iPicturesWithJPGsThisRun'])
            collection = int(species['iPicturesWithJPGsCollection'])
        except (KeyError, ValueError, TypeError):
            return False
        if (this_run + collection) <= 0:
            return False
        # 'All' and force_all builds (incl. the master rebuild, which runs as
        # 'New'+force_all) render every node that has photos; a plain 'New'
        # trip/album build renders only nodes touched this run.
        return self.all_or_new == 'All' or getattr(self, 'force_all', False) or this_run > 0
    
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

    def orderedLeaves(self, leaves):
        """Sibling taxa in display order: the species list's Sequence when it has
        one (ch.6), otherwise as before — sorted by name, which on an unconverted
        list follows its '040 passeriformes' prefixes."""
        from engine.taxon_order import ordered
        rows = getattr(getattr(self, "sc", None), "sc_dict", None) or {}
        return ordered(list(leaves), rows)
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
            # Imported HERE, not at module level: engine.triplib imports
            # engine.projectlib, so a top-level import would be circular. Until
            # now this line relied on the CALLER having imported engine.triplib
            # first — every batch script that needs a Trip does so, except
            # svv4BuildWebsite, which therefore only worked when something else
            # (the GUI) had already pulled triplib in. Owning the import here
            # fixes it for every caller instead of one.
            import engine.triplib
            self.Trip = engine.triplib.Trip(self, config)
            if inittrip:
                self.Trip.readAndWriteTripAdmin()                   # not sure we want this for every step?  maybe only 4 and 9?
        return
    
    def readProject(self, getEarlierDays = False, silent=False):
        starttime = time.time()
        metadatadir = self.getMetaDir()
        metadatadirreview = self.getMetaDirReview()

        # get species an existing committed files and picture
        self.readSpeciesInfo(os.path.join(metadatadirreview, "species.csv"), False)
        self.sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)
        read_file(self, os.path.join(metadatadir, "active", "data_files.csv"))
        read_pictures(self, os.path.join(metadatadir, "active", "data_pictures.csv"))

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
    
    def readMasterCommitted(self, silent=False):
        """Load the master's fully committed collection from metadata/active/
        for rebuilding the whole master website independent of any trip.

        readProject() reads the species list from review/<projectName>/species.csv
        — the subset for the trip currently being ingested. This instead reads the
        FULL master species tree from active/species.csv (already carrying computed
        Leaves / TargetPath / BestJPG / counters), plus the committed files and
        pictures from active/. No review/day merging — there is no trip context."""
        starttime = time.time()
        active = os.path.join(self.getMetaDir(), "active")
        self.readSpeciesInfo(os.path.join(active, "species.csv"), False)
        self.sc.readSpeciesCollection(os.path.join(active, "speciescollection.csv"), False)
        read_file(self, os.path.join(active, "data_files.csv"), silent=silent)
        read_pictures(self, os.path.join(active, "data_pictures.csv"))
        logger.info(f"Read committed master collection (active/) in {time.time()-starttime:.1f}s.")
        return

    def makeSpeciesDirectory(self, leaf):
        global debugfile, count
        species = self.getSpecies(leaf)
        biotadatabase = self.getRepositoryPath()
        # os.path.join handles a missing trailing separator on the
        # repository path; the previous '+' concat produced
        # 'naturedbbiota/...' when biotadatabase had no trailing slash,
        # which created a bogus directory tree alongside master.
        directoryname = os.path.join(biotadatabase,
                                      species['TargetPath']).lower()
        os.makedirs(directoryname, exist_ok=True)

        # find all files in this directory, this is an expensive scan, let's find out how slow it is
        for picture in self.UniquePictureName_dict:
            if self.UniquePictureName_dict[picture]['WetSoortNaam'] == leaf:        # we want this
                for filename in self.UniquePictureName_dict[picture]['Files']:
                    sourcefile = ""
                    targetfile = ""
                    try:
                        sourcefile = self.files_dict[filename]['WindowsFileName']
                        targetfile = os.path.join(
                            biotadatabase,
                            self.UniquePictureName_dict[picture]['TargetPath'],
                            self.files_dict[filename]['File:FileName'])
                        print(f"File {filename:40}: {sourcefile:64} to {targetfile}",
                            file=debugfile)
                        # Use samefile() rather than string == — the
                        # source path may use forward slashes / a
                        # different case from os.path.join's output
                        # while still being the same file on Windows.
                        if not _same_file_on_disk(sourcefile, targetfile):
                            shutil.copy(sourcefile, targetfile)
                        #also delete
                        # os.remove(sourcefile)
                        count += 1
                    except Exception as exc:
                        logger.info(
                            f"Species copy failed {sourcefile} -> {targetfile}: "
                            f"{type(exc).__name__}: {exc}"
                        )
            #print(f"Species: Directory {directoryname} created for {species['ScientificName'] + '.html'}.")
        return

    # create website based on current count setting, now to test iSpeciesThisRunCount
    def makeDirectories(self, speciesname):
        species = self.getSpecies(speciesname)
        # create database to store pictures as needed
        biotadatabase = self.getRepositoryPath()
        directoryname = os.path.join(biotadatabase, species['TargetPath']).lower()
        os.makedirs(directoryname, exist_ok=True)
        for leaf in self.orderedLeaves(species['Leaves']):
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
                    self.breadcrumb.addBCsimple(self.Trip.web_prefix + self.Trip.tripname + "/summaries/all biota species-nl.html", "Trip soorten")
                else:
                    self.breadcrumb.addBCsimple("/nature/summaries/all biota species-nl.html", "Alle soorten")
            else:
                self.breadcrumb.addBCsimple("/nature/intro-en.html", "Nature")
                if self.Trip:
                    self.breadcrumb.addBCsimple("/nature/tripindex-en.html", "Trips")
                    self.breadcrumb.addBCsimple(self.Trip.web_prefix + self.Trip.tripname + "/summaries/all biota species-en.html", "Trip species")
                else:
                    self.breadcrumb.addBCsimple("/nature/summaries/all biota species-en.html", "All species")
            if self.Trip:
                self.breadcrumb.appendLevelBC(species, self.Trip.web_prefix + self.Trip.tripname + "/")
            else:
                self.breadcrumb.appendLevelBC(species,"/nature/")

            templatefile = self.getTemplateLines("vogelordepage-" + language + ".html")
            with open((directoryname + species['ScientificName'] + '-' + language + '.html').lower(), "w", encoding='UTF-8') as SpeciesHTMLfile:
                for rawline in templatefile:
                    line = getTemplateOutputString(self, species, '', '', rawline.rstrip('\n'))
                    print(line, file=SpeciesHTMLfile)
            SpeciesHTMLfile.close()
        except Exception as e:
            logger.warning(f"makeWebLevel '{species.get('ScientificName', '?')}' failed: {e}")
        return

    # create website based on current count setting, now to test iSpeciesThisRunCount
    #
    # force_all decouples SELECTION from OUTPUT ROUTING. Normally all_or_new does both:
    # it decides which pages to render (New = only taxa/species with new pics this run)
    # AND where they land (getBiotaAllorNewDir: 'All' -> web/, 'New' -> frozen/). The
    # uploaders (sv7, svv4) only ship frozen/biota, so we must keep all_or_new='New' to
    # land in frozen. force_all=True then re-renders EVERY published taxon/species anyway
    # — a full re-render (e.g. after a shared-template change) that still uploads normally.
    # progress_cb, when given, is called once per species page rendered — a heartbeat
    # for long full re-renders (the master has thousands of species). Optional, so
    # normal trip/album builds are unaffected.
    def makeWebDirectories(self, speciesname, all_or_new, force_all=False, progress_cb=None):
        if speciesname not in self.sp.species_dict:
            logger.warning(f"makeWebDirectories: '{speciesname}' not found in species data, skipping.")
            return
        species = self.sp.species_dict[speciesname]
        self.setAllOrNew(all_or_new)
        # Remember whether this build forces a full render (master rebuild runs
        # as 'New'+force_all). hasWebPage() reads it so sidebar links match the
        # set of pages actually written this build.
        self.force_all = force_all
        self.makeWebLevel(species, all_or_new)
        for leaf in self.orderedLeaves(species['Leaves']):
            species = self.sp.species_dict[leaf]
            if (int(species['iPicturesWithJPGsThisRun']) + int(species['iPicturesWithJPGsCollection'])) > 0:
                render_it = all_or_new == 'All' or force_all or (int(species['iPicturesWithJPGsThisRun']) > 0)
                if species['Level'] != Level.SPECIES:
                    # only create directory if is new, all, or a forced full re-render
                    if render_it:
                        self.makeWebDirectories(leaf, all_or_new, force_all=force_all, progress_cb=progress_cb)
                # species page updated just in case it is needed
                elif render_it:
                    # Skip species whose JPGmap has no JPG currently in files_dict;
                    # these are forward references from stale state and would render empty pages.
                    if any(os.path.basename(jpg) in self.files_dict for jpg in species['JPGmap']):
                        self.makeSpeciesWebDirectory(leaf, all_or_new)
                        if progress_cb is not None:
                            progress_cb()
        return

    def makeSpeciesWebDirectory(self, leaf, all_or_new):
        try:
            species = self.sp.species_dict[leaf]
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
                    self.breadcrumb.addBCsimple(self.Trip.web_prefix + self.Trip.tripname + "/summaries/all biota species-nl.html", "Trip soorten")
                else:
                    self.breadcrumb.addBCsimple("/nature/summaries/all biota species-nl.html", "Alle soorten")
            else:
                self.breadcrumb.addBCsimple("/nature/intro-en.html", "Nature")
                if self.Trip:
                    self.breadcrumb.addBCsimple("/nature/tripindex-en.html", "Trips")
                    self.breadcrumb.addBCsimple(self.Trip.web_prefix + self.Trip.tripname + "/summaries/all biota species-en.html", "Trip species")
                else:
                    self.breadcrumb.addBCsimple("/nature/summaries/all biota species-en.html", "All species")
            if self.Trip:
                self.breadcrumb.appendLevelBC(species, self.Trip.web_prefix + self.Trip.tripname + "/")
            else:
                self.breadcrumb.appendLevelBC(species,"/nature/")

            vogelsoortpage_lines = self.getTemplateLines("vogelsoortpage-" + language + ".html")
            with open((directoryname + species['ScientificName'] +'-' + language + '.html').lower(), "w", encoding='UTF-8') as SpeciesHTMLfile:
                for rawline in vogelsoortpage_lines:
                    line = getTemplateOutputString(self, species, '', '', rawline.rstrip('\n'))
                    print(line, file=SpeciesHTMLfile)
            SpeciesHTMLfile.close()

            #print(f"Species: Directory {directoryname} created for {species['ScientificName'] + '.html'}.")
        except Exception as e:
            logger.warning(f"makeSpeciesWebDirectory: '{leaf}' failed: {e}")
        return

    def makeAllSpeciesPage(self, speciesname, language):
        biota = self.getBiotaDir()
        self.setLanguage(language)
        directorytouse = os.path.join(biota, 'summaries')
        os.makedirs(directorytouse, exist_ok=True)

        # breadcrumb
        self.breadcrumb.clearBreadCrumb()
        if language == 'nl':
            self.breadcrumb.addBCsimple("/nature/intro-nl.html", "Natuur")
            if self.Trip:
                self.breadcrumb.addBCsimple("/nature/tripindex-nl.html", "Trips")
                self.breadcrumb.addBCsimple(self.Trip.web_prefix + self.Trip.tripname + "/summaries/all biota species-nl.html", "Trip soorten")
            else:
                self.breadcrumb.addBCsimple("/nature/summaries/all biota species-nl.html", "Alle soorten")
        else:
            self.breadcrumb.addBCsimple("/nature/intro-en.html", "Nature")
            if self.Trip:
                self.breadcrumb.addBCsimple("/nature/tripindex-en.html", "Trips")
                self.breadcrumb.addBCsimple(self.Trip.web_prefix + self.Trip.tripname + "/summaries/all biota species-en.html", "Trip species")
            else:
                self.breadcrumb.addBCsimple("/nature/summaries/all biota species-en.html", "All species")
        #biota is implied, do not show this
        #self.breadcrumb.appendLevelBC(self.sp.species_dict[speciesname], self.Trip.web_prefix + self.Trip.tripname)

        speciesOverview = self.getTemplateLines("allspecies-" + language + ".html")
        outpath = os.path.join(biota, 'summaries', f'all {speciesname} species-{language}.html').lower()
        with open(outpath, "w", encoding='UTF-8') as speciesOverviewFile:
            for templateline in speciesOverview:
                line = getTemplateOutputString(self, self.sp.species_dict[speciesname], '', '', templateline.rstrip('\n'))
                print(line, file=speciesOverviewFile)

            speciesOverviewFile.close()

        logger.info(f'Written all Species data to "{outpath}".')
        return
    
    # Intro pages, can be used generically
    # 
    def introTaxonName(self, bare):
        """The intro page's dragonfly and butterfly tiles link to an order whose
        folder is '003 odonata' on an unconverted species list and 'odonata' on a
        converted one (ch.6): the name as THIS list has it."""
        rows = getattr(getattr(self, "sc", None), "sc_dict", None)
        if rows:
            names = set(rows)
        else:
            from engine.speciesclassification import namesInSpeciesCollection
            names = namesInSpeciesCollection(os.path.join(
                self.getRepositoryPath(), "metadata", "active", "speciescollection.csv"))
        return intro_taxon_name(names, bare)

    def makeIntro(self, simplepage, language):
        # beadcrumb
        self.breadcrumb.clearBreadCrumb()        
        if language == 'nl': natuur = 'natuur' 
        else:  natuur = 'nature'
        if simplepage == "intro":
            self.breadcrumb.addBCsimple("/nature/" + simplepage + '-' + language + ".html", natuur)
            if self.Trip:
                self.breadcrumb.addBCsimple("/nature/tripindex-" + language + ".html", "Trips")
                self.breadcrumb.addBCsimple(self.Trip.web_prefix + self.Trip.tripname + "/" + simplepage + '-' + language + ".html", "Intro")

        simplepage += '-' + language
        speciesOverview = self.getTemplateLines(simplepage + ".html")
        with open((self.getBiotaDir() + simplepage + ".html").lower(), "w", encoding='UTF-8') as IntroFile:
            for templateline in speciesOverview:
                species = self.sp.species_dict["biota"] if self.sp != None else ""
                line = getTemplateOutputString(self, species, '', '', templateline.rstrip('\n'))
                line = _INTRO_TILE.sub(lambda m: "biota/animalia/arthropoda/insecta/{0}/{0}".format(
                    self.introTaxonName(m.group(1))), line)
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
            filedata = self.files_dict.get(file)
            if filedata is None:
                continue   # file absent from files_dict (e.g. XMP not extracted for virtual trip)
            ext = filedata['WindowsExtension']
            if ext.upper() in preferredtypes:
                bestresult = filedata.get(column, "")
                if bestresult != "":
                    return bestresult

        return ""

    # first look for a CameraRaw format ("ORF", "NEF", "NRW", "HEIC")
    def findAnySourceItem(self, filelist, column):
        # MOV/MP4 belong in "any source" and were missing: this feeds the UPN's
        # GPSPosition / GPSAltitude / GPSProcessingMethod, so a VIDEO-ONLY
        # picture ended up with an empty GPSPosition however good the fix in the
        # file was, and Count Species reported it as "no GPS". The large viewer
        # disagreed, because exif_from_rows falls back to the FILE row's
        # Composite:GPSPosition — which had the coordinate all along.
        # Appended LAST for the same reason findRawOrJPGItem does it (see below):
        # a mixed UPN still prefers the camera original, and an XMP still wins
        # for GPS because that is where a hand-placed fix is written. Adding to
        # the end of the list can only fill a value that was previously empty.
        return self.findBestItem(filelist, column,
                                 ("ORF", "NEF", "NRW", "HEIC", "JPG", "JPEG", "XMP",
                                  "MOV", "MP4"))
    # MOV/MP4 trail the stills on purpose: a mixed UPN (never happens today, but
    # nothing stops it) should still prefer the camera original. For a video-only
    # UPN every still type misses, so this is the only way it gets a
    # DateTimeOriginal/CameraModel/etc. at all — without it, a movie's UPN row
    # carries no date, which is invisible (no card badge) rather than obviously
    # wrong, so it went unnoticed until a real video UPN existed to show it.
    def findRawOrJPGItem(self, filelist, column):
        return self.findBestItem(filelist, column,
                                 ("ORF", "NEF", "NRW", "HEIC", "JPG", "JPEG", "MOV", "MP4"))
    def findCameraOrXMPItem(self, filelist, column):
        return self.findBestItem(filelist, column, ("ORF", "NEF", "NRW", "HEIC", "XMP"))
    def findCameraItem(self, filelist, column):
        return self.findBestItem(filelist, column, ("ORF", "NEF", "NRW", "HEIC"))
    # check for manual input by PhotoMechanic or Lightroom (mostly)
    def findXMPItem(self, filelist, column):
        """An annotation value for the picture: a sidecar/JPG value if there is
        one, else the HEIC's (or PNG's) own.

        A RAW keeps its rating, colour, species and caption in a sidecar, so
        XMP/JPG is the whole story for one. A HEIC does not: it carries them
        INSIDE the file, which is where SpeciesViewer writes them and where
        Lightroom and PhotoLab both read and write them (see the HEIC set in
        utils/raw_extensions.py). Looking only at XMP/JPG therefore threw away
        everything a HEIC-only picture knew about itself — measured on the master
        before this changed: of 392 HEIC-only pictures, 67 carried a species in
        the file, 10 a rating and 1 a colour, all invisible to the rollup even
        though sv2ReadDirExif had read them into data_files.csv correctly. Only
        Label had the fallback (as findColorLabelItem, folded in here) because
        May's HEIC work stopped there.

        The fallback can only ADD: findBestItem returns the first non-empty match,
        so a sidecar value always wins and no existing value can change. That is
        what makes this safe on the 11 pictures where a HEIC sits beside an
        xmp/jpg.
        """
        val = self.findBestItem(filelist, column, ("XMP", "JPG"))
        if val == "":
            val = self.findBestItem(filelist, column, ("HEIC", "PNG"))
        return val
    def findDNGItem(self, filelist, column):
        return self.findBestItem(filelist, column, ("DNG"))
    # should look at best JPG first!
    def findJPGItem(self, filelist, column):
        return self.findBestItem(filelist, column, ("JPG", "JPEG"))

    # ImageSize of the JPG with the MOST pixels (the camera original, not a
    # smaller DxO/Lightroom crop). Used as the picture's size when there is no
    # RAW/HEIC/XMP source (e.g. a phone JPG), so the "better crop" reward in
    # computeRating can still recognise a cropped render as a real crop.
    def findLargestJPGSize(self, filelist):
        best_size = ""
        best_area = -1
        for file in filelist:
            filedata = self.files_dict.get(file)
            if filedata is None:
                continue
            if filedata['WindowsExtension'] not in ('jpg', 'jpeg'):
                continue
            size = filedata.get('ImageSize', '')
            if not size:
                continue
            try:
                width, height = map(int, re.split('[x ]', size))
            except (ValueError, TypeError):
                continue
            area = width * height
            if area > best_area:
                best_area = area
                best_size = size
        return best_size


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
            "GPSProcessingMethod": "",
            "Location":         "",
            "City":             "",
            "Municipality":     "",
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
            "Headline":         "",
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
            # Note: a HEIC cannot hold IPTC at all (HEIF has no IPTC block —
            # the one field of thirteen that would not round-trip into one), so
            # for a HEIC-only picture this line can never supply anything. Its
            # species comes from XMP:Subject above, which is also the field
            # Lightroom and PhotoLab read and write.
            upn["subject"] =       self.findXMPItem(files, "IPTC:Keywords")
        upn["Rating"] =            self.findXMPItem(files, "XMP:Rating")
        upn["Label"] =             self.findXMPItem(files, "XMP:Label")
        # No colour stays NO colour. Until 2026-10-01 a picture with neither a
        # rating nor a colour was shown Green ("for waarneming.nl"). That made
        # un-culled pictures look like keepers: 1,380 never-culled frames of
        # 2026-09-04 read as Green, the `g` keepers filter and the day statistics
        # counted them, and the external-edit refresh had to special-case the fake
        # value. The colour bar's Grey swatch is the honest "not judged yet".
        # computeRating never used this value (it scores the file's own label).
        if upn["ImageSize"] == "": upn["ImageSize"] = self.findCameraOrXMPItem(files, "ImageSize")
        # Phone/compact JPGs have no RAW/HEIC/XMP source, so ImageSize stayed
        # empty and the "better crop" ×1.5 reward below could never fire. Fall
        # back to the LARGEST JPG (the original, not a smaller DxO crop) so a
        # cropped render still scores as a genuine crop.
        if upn["ImageSize"] == "": upn["ImageSize"] = self.findLargestJPGSize(files)
        # JPGSize = the display JPG's size. findJPGItem returned the FIRST jpg in
        # Files, which is often the derived '-small' web thumbnail (its 600x450
        # then showed as "from master (600x450)" and fed the rating's size term).
        # Take the LARGEST jpg instead — that's the real _DxO/Lightroom render,
        # never the thumbnail.
        upn["JPGSize"] =           self.findLargestJPGSize(files)
        if upn["GPSAltitude"] == "": upn["GPSAltitude"] =       self.findAnySourceItem(files, "EXIF:GPSAltitude")
        if upn["GPSPosition"] == "": upn["GPSPosition"] =       self.findAnySourceItem(files, "Composite:GPSPosition")
        # Travels with the coordinate: it is only meaningful about the fix above.
        if upn["GPSProcessingMethod"] == "":
            upn["GPSProcessingMethod"] = self.findAnySourceItem(files, "EXIF:GPSProcessingMethod")
        upn["Location"] =          self.findXMPItem(files, "XMP:Location")
        upn["City"] =              self.findXMPItem(files, "XMP:City")
        upn["Municipality"] =      self.findXMPItem(files, "XMP:Municipality")
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
        upn["Headline"] =          self.findXMPItem(files, "XMP:Headline")
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
        MyMagic = sp.getMP()
        # Add properties for picture
        if self.SpeciesDebug > 0:  
            filebug = open("addSpeciesInformation.txt", "w")
        recognized_species = 0
        for upn in list(self.UniquePictureName_dict.values())[firstUPNtoProcess:]:
            picture = self.UpdatePicture(upn)
            picture['Rate'] = self.computeRating(upn)
            scientificname = ""
            species = None  # reset; otherwise the second JPG loop can pour this UPN's data into the previous UPN's species record
            # Read species data and put in the UniquePictureName (upn)
            for filename in picture['Files']:
                try:
                    thisfile = self.files_dict[filename]
                except KeyError:
                    # File not in files_dict (e.g. XMP sidecar absent from a
                    # virtual-trip day extract) — skip and try the next file.
                    continue
                if thisfile['XMP:Subject'] != "":
                    subject_in_jpg = thisfile['XMP:Subject']
                else:
                    subject_in_jpg = thisfile['IPTC:Keywords']
                if subject_in_jpg != "":
                    scientificname = sp.getSpecies(subject_in_jpg)
                    known = picture.get('WetSoortNaam', '')
                    if (not scientificname and known in self.sc.sc_dict
                            and not self.sc.isBlogExtra(subject_in_jpg)):
                        # The keyword no longer resolves — an old name that a
                        # split gave to another species resolves to nothing on
                        # purpose (contested, 2026-10-17) — but this picture's
                        # record already knows its species (an album reads the
                        # master's). Keep it: Greece's 22 red-rumped swallows
                        # ("Roodstuitzwaluw") vanished from its blog (2026-10-18).
                        logger.info(f'"{subject_in_jpg}" does not name one species; '
                                    f'keeping {known} for {upn["UniquePictureName"]}.')
                        scientificname = known
                    if (scientificname == "" or scientificname is None) and self.sc.isBlogExtra(subject_in_jpg):
                        # Non-species blog extra (landscape/context photo). It
                        # carries no taxon, so it must not warn as an unknown
                        # species. Route it into the topics/ branch so it gets a
                        # home in the master (TargetPath, counters, best-JPG) via
                        # the SAME tree machinery as a species. It stays a blog
                        # extra for RENDERING (subject unchanged -> isBlogExtra
                        # still true; is_topic_node() keeps it off the species
                        # website/map). A leaf that can't be resolved (e.g. no
                        # capture date -> no year) leaves WetSoortNaam empty and
                        # is excluded at freeze, exactly as before.
                        from engine import topics
                        picture['errormessage'] = ""
                        leaf_key = topics.route(self.sc, sp, None, subject_in_jpg, picture)
                        if leaf_key:
                            species = sp.species_dict[leaf_key]
                            picture['WetSoortNaam'] = species['ScientificName']
                            picture['NLSoortNaam'] = species['DutchName']
                            picture['EngSoortNaam'] = species['EnglishName']
                            # truthy so the JPG loop below fills the leaf's
                            # BestJPG / JPGmap (topic pictures do NOT go through
                            # taxon_columns — their levels are not taxonomic).
                            scientificname = leaf_key
                            recognized_species += 1
                            break
                        continue
                    if scientificname == "":
                        logger.warning(f"""The species "{subject_in_jpg}" is not found in the GBIF database as a scientific name for "{upn['UniquePictureName']}".""")
                        picture['errormessage'] = f"""The species "{subject_in_jpg}" is not found in the GBIF database as a scientific name for "{upn['UniquePictureName']}"."""
                    elif scientificname == None:
                        # "not resolved" has two very different causes: a real
                        # typo GBIF rejected, or a GBIF lookup that never ran
                        # (API/library break, offline). Only accuse the user of
                        # a typo when the name was actually checked — otherwise
                        # one broken call turns every unknown name into a false
                        # "correct it".
                        gbif_broken = (getattr(getattr(sp, "sc", None), "_gbif_lookup_errors", 0) > 0
                                       or getattr(getattr(sp, "sc", None), "_gbif_offline", False))
                        if gbif_broken:
                            if not getattr(self, "_gbif_unverified_warned", False):
                                self._gbif_unverified_warned = True
                                logger.warning("GBIF species lookup is unavailable this run — "
                                               "unrecognised names are flagged as UNVERIFIED, not typos. "
                                               "Re-run once GBIF is reachable to validate them.")
                            msg = f"""Species "{subject_in_jpg}" could not be verified (GBIF lookup unavailable) in "{thisfile['WindowsFileName']}"."""
                        else:
                            msg = f"""Species "{subject_in_jpg}" may have a typo in "{thisfile['WindowsFileName']}", correct it."""
                        logger.warning(msg)
                        picture['errormessage'] = msg
                    else:
                        try:           
                            species = MyMagic.getSpecies(scientificname)
                            # these columns are in picture
                            picture['WetSoortNaam'] = species['ScientificName']
                            picture['NLSoortNaam'] = species['DutchName']
                            picture['EngSoortNaam'] = species['EnglishName']
                            picture.update(taxon_columns(sp.species_dict, species))
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
                # Ensure the species NODE also carries its path. The block below
                # that sets species['TargetPath'] runs ONLY for JPG files, so an
                # undeveloped species (no rendered JPG) would keep the empty
                # initial TargetPath — and sv6Freeze then builds the wrong master
                # directory and cannot copy the RAW/XMP (the ghost-row fix now
                # copies these). Look it up fresh; the loop-local `species` may be
                # stale from a prior UPN iteration.
                _spnode = sp.species_dict.get(picture['WetSoortNaam'])
                if _spnode is not None and not _spnode.get('TargetPath'):
                    _spnode['TargetPath'] = picture['TargetPath']
                

            # find JPG (if there is more than 1, find the best JPG)
            for filename in picture['Files']:
                try:
                    thisfile = self.files_dict[filename]
                except KeyError:
                    continue
                rating = 0
                if thisfile['WindowsExtension'] in ['jpg','jpeg']:
                    if filename.find('-small') != -1: continue                          # do not -small picture for BestJPG etc.
                    if (picture['BestJPG'] == "") or ('dxo' in thisfile['File:FileName'].lower()):
                        picture['BestJPGPathName'] = upn['TargetPath']
                        picture['BestJPG'] = thisfile['File:FileName']
                    # Only update the per-species record when this UPN is actually attributed
                    # to a species in this run. Bare `scientificname != ""` is not enough:
                    # the typo branch above sets it to None, and `species` may be stale from
                    # a prior UPN iteration. Require all three to agree.
                    if scientificname and species is not None and picture['WetSoortNaam']:
                        if (species['BestJPG'] == "") or \
                            (int(species['currentHighestRating']) < int(picture['Rate'])):
                            species['BestJPGPathName'] = upn['TargetPath']
                            species['BestJPG'] = thisfile['File:FileName']
                            species['BestUPN'] = upn['UniquePictureName']
                            species['TargetPath'] = upn['TargetPath']
                            species['currentHighestRating'] = int(picture['Rate'])      # so we know if there are better pictures
                            if self.SpeciesDebug > 0:
                                    print(f"{picture['WetSoortNaam']:28}: Added file {thisfile['WindowsFileName']} to best {species['BestJPGPathName']}/{species['BestJPG']} {species['JPGmap']}", file=filebug)
                        if thisfile['File:FileName'] not in species['JPGmap']:
                            species['JPGmap'].append(thisfile['File:FileName'])

        logger.info(f"Species information added for {recognized_species} pictures.")
        return 0

    # Collect the JPGs, the best file per picture.
    # Also update counters while we are at it, for all pictures, also the ones without JPGs 
    def addPicturesToSpecies(self, sp, firstUPNtoProcess, SpeciesDebug):
        if SpeciesDebug > 0:  
            from utils.logger_support import log_file
            debugfile = open(log_file("Species print.txt"), "w", encoding="utf-8")
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

        # at least biota (or the topics branch) must be present to run
        # summarization; skip gracefully when no keywords matched (culling-first
        # workflow: species may still be assigned in the viewer after Read &
        # Summarize). The two roots roll up independently — a landscape-only day
        # has a 'topics' root but no 'biota'.
        if 'biota' not in sp.species_dict and 'topics' not in sp.species_dict:
            logger.warning("No recognized species in this batch — data_pictures.csv written without species assignment.")
            return 0

        # Add counters from species to higher levels, tail-recursion works fine
        if 'biota' in sp.species_dict:
            counters = []
            counters = sp.summarizeSpeciesCounters('biota', counters, debugfile)
            sp.printDataForSpecies('biota', debugfile)

        # Same rollup for the non-taxonomic topics/ branch (see engine/topics.py)
        # so topic leaves carry counters + best-JPG up to topics/<topic>, which
        # the freeze file-copy and species.csv need.
        if 'topics' in sp.species_dict:
            sp.summarizeSpeciesCounters('topics', [], debugfile)
            sp.printDataForSpecies('topics', debugfile)

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
                        SpeciesJPGCount[upn['WetSoortNaam']] = [0,0,0,0,0]
                    pictures, jpgs, starred, yellow_or_red, keep = SpeciesJPGCount[upn['WetSoortNaam']]
                    if upn['Label'] in ["Red", "Yellow"]: yellow_or_red += 1
                    if upn['BestJPG'] != "": jpgs += 1
                    if upn['Rating'] > starred: starred = upn['Rating'] 
                    # #Keep = the pictures this day would survive culling:
                    # Good (Red/Yellow/Green) + Unrated. Exactly the complement
                    # of the day's "Duplicate or bad quality" (Blue/Purple)
                    # count, so the two always add up to #Raw.
                    if upn['Label'] not in ["Blue", "Purple"]: keep += 1
                    SpeciesJPGCount[upn['WetSoortNaam']] = [pictures+1, jpgs, starred, yellow_or_red, keep]
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
                    print(f" #Raw  #JPG  Best Color    old  #Keep Name                             (Engels/Nederlands)", file=sf)
                    for species in SpeciesJPGCount:
                        pictures, jpgcount, starred, yellow_or_red, keep = SpeciesJPGCount[species]
                        knownjpg = sp.species_dict[species]['iPicturesWithJPGsCollection']
                        print(f"{pictures:5} {jpgcount:5} {starred:5} {yellow_or_red:5} {knownjpg:6} {keep:6} {species:32} ({sp.species_dict[species]['EnglishName']} / {sp.species_dict[species]['DutchName']})", file=sf)

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
        # HEIC-only pictures carry their rating in the HEIC itself (SpeciesViewer
        # + LR/DxO write it there); score them like an XMP. Guarded so a HEIC
        # alongside a JPG/XMP doesn't override the sidecar that already scores.
        has_sidecar = any(
            self.files_dict[f]['WindowsExtension'] in ['xmp', 'jpg', 'jpeg']
            for f in upn['Files'] if f in self.files_dict)
        # file type logic
        for filename in upn['Files']:
            file = self.files_dict.get(filename)
            if file is None:
                continue
            # copy Label to Rating
            if file['XMP:Rating'] in ['0', ''] and file['XMP:Label'] != '':
                label = file['XMP:Label']
                if label == 'Red':
                    file['XMP:Rating'] = 4
                elif label == 'Yellow':
                    file['XMP:Rating'] = 2
                elif label == 'Green':
                    file['XMP:Rating'] = 1
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
            elif filetype in ['heic'] and not has_sidecar:
                if file['XMP:Rating'] == "":
                    computation = 5
                else:
                    computation = 15 * (1 + int(file['XMP:Rating']))

        # save new Rating for Picture
        bestresult = 0
        for filename in upn['Files']:
            filedata = self.files_dict.get(filename)
            if filedata is None:
                continue
            rating = filedata['XMP:Rating']
            if rating != "":
                bestresult = max(bestresult, int(rating))
        upn['Rating'] = bestresult

        # Penalties for very small pics
        saved_multiplier = 0.1
        multiplier = 1
        for filename in upn['Files']:
            file = self.files_dict.get(filename)
            if file is None:
                continue
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
                file['XMP:Rating'] = 4
            elif label == 'Yellow':
                file['XMP:Rating'] = 2
            elif label == 'Green':
                file['XMP:Rating'] = 1
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
    
