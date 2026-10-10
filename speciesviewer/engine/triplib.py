import os
import io
import re
from pathlib import Path
import csv
from datetime import datetime, date
import engine.projectlib
import logging
import configparser
import requests

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

_INI_SECTION_RE = re.compile(r'^\s*\[.+\]\s*$')
_INI_KEYVAL_RE  = re.compile(r'^[^\s\[;#][^=:]*[=:]')


def _coerce_orphan_continuations(text: str) -> str:
    """Indent any unindented continuation line inside a multi-line value
    so configparser can re-read trip.ini files that contain hand-added
    paragraph breaks. Mirrors the helper in qt/utils/trip_config.py."""
    out = []
    in_value = False
    for line in text.splitlines():
        if _INI_SECTION_RE.match(line):
            in_value = False
            out.append(line)
        elif _INI_KEYVAL_RE.match(line):
            in_value = True
            out.append(line)
        elif in_value and line.strip() and not line.startswith((' ', '\t')):
            out.append('    ' + line)
        else:
            out.append(line)
    return '\n'.join(out) + '\n'


class Trip:
    tripday = ""
    tripname = ""
    trip_csv_headers = []
    mp = None
    files = 0
    pictures = 0
    master_repository_disk = ""
    master_repository_web = ""    
    trip_picture = ""
    trip_banner = ""
    trip_row = {}
    trip_dict = {}

    def __init__(self, MyProject, config):
        MyProject.registerTrip(self)
        self.mp = MyProject
        try:
            self.master_repository_disk = config.get("master","MasterBiotaDatabase")
            self.mp.setMasterRepository(self.master_repository_disk)
            web_root = self.mp.getBiotaWebRoot()
            self.tripname = Path(web_root).name
            # Derive the URL prefix (/trips/ or /virttrips/) from the web root so
            # breadcrumbs are correct for both regular and virtual trips.
            parts = Path(web_root.lstrip("/")).parts
            self.web_prefix = "/" + parts[0] + "/" if len(parts) >= 1 else "/trips/"
        except:
            self.web_prefix = "/trips/"
        return

    def setTripLanguageSwitch(self, name):
        self.tripname = name
    def getTripLanguageSwitch(self):
        return self.tripname    
    def existTrip(self, newname):
        if newname in self.trip_dict: return True
        return False
    def getTripName(self):
        return self.tripname
    def getMasterRepository(self):
        return self.mp.getMasterRepository()
    def getMasterBiotaWebRoot(self):
        return self.master_repository_web
    def getTripAdminFile(self):                 # the location when the trips.csv file is stored
        return os.path.join(self.mp.getMasterRepository(), 'admin', 'trip_admin.csv')
    def getTripAdminWeb(self):
        return self.getMasterBiotaWebRoot() + 'includes web/trip_admin.csv'
    def getTripBanner(self):          # the web address where the file can be found
        return os.path.join(self.mp.getBiotaWebRoot() , self.trip_dict[self.tripname]['trip_banner'])
    # def getTripPicture(self, tripname):         # the web address where the picture can be found
    #     return os.path.join(self.mp.getBiotaWebRoot() , self.trip_dict[tripname]['trip_name'], self.trip_dict[tripname]['trip_picture'])
    # def getTripName(self, tripname):
        return self.trip_dict[tripname]['trip_name']
    
    def setTripDict(self, dict):
        self.trip_dict = dict
    def getTripDictSortedKeys(self):
        return self.trip_dict.keys()
        # return reversed(sorted(self.trip_dict.keys()))
    def getTrip(self, tripname):
        return self.trip_dict[tripname]
    def addTrip(self, row):
        self.trip_dict[row['trip_name']] = row
        return

    def ensureTripRegistered(self, fallback_row):
        """Seed this trip's registry entry from its own config (e.g. virttrip.ini)
        when it is not already present in trip_dict — i.e. a retroactive virtual
        trip that has not yet been added to the master trip_admin.csv. Without
        this, getTripBanner()/getTripPicture() throw and the page falls back to
        the keerkringvogel default. An existing (authoritative master-registry)
        row is never overwritten. Keyed on self.tripname so the banner lookup,
        which uses trip_dict[self.tripname], is guaranteed to resolve."""
        if not fallback_row or not self.tripname:
            return
        if self.tripname not in self.trip_dict:
            row = dict(fallback_row)
            row['trip_name'] = self.tripname
            self.trip_dict[self.tripname] = row
    def removeTrip(self, trip_name):
        """Drop one trip from the in-memory admin dict (by trip_name). Returns
        True when a row was actually removed. Used by Manage Trips to un-advertise
        a trip — the trip's own web pages are left untouched, only its row in
        trip_admin.csv (and thus the tripindex) goes away."""
        if trip_name in self.trip_dict:
            del self.trip_dict[trip_name]
            return True
        return False
    def setTripHeader(self, headers):
        self.trip_csv_headers = self.new_trip_row.keys()
        return
    def getTripHeader(self):
        return self.trip_csv_headers 

    # def getTripTitel(self):
    #     return self.TitleNl
    # def getTripTitle(self):
    #     return self.TitleEn
    # def getTripBericht(self):
    #     return self.MessageNl
    # def getTripMessage(self):     
    #     return self.MessageEn
    def setColumnWidth(self, cw):
        self.column_width = cw
        return
    def getColumnWidth(self):
        return self.column_width
    
    # update by scanning the trip directory
    def getCollectTrips(self, directory):
        self.trip_dict = {}
        for root, dirs, files in os.walk(directory):
            for file in files:
                if file.endswith('trip.ini'):
                    file_path = os.path.join(root, file)        # fully qualified name
                    parseddir = root.split('\\')
                    name = parseddir[-1]
                    if name == "": name = parseddir[0]
                    triprow = self.readTrip(file_path)
                    logger.info(f"Read trip information from {file_path}.")
                    self.trip_dict[name] = triprow
  
    # 
    def readTrip(self, tripinifile):
        tripconfig = configparser.ConfigParser()

        if os.path.exists(tripinifile):
            # create a separete config instance.
            # Read tolerantly: the file is UTF-8 today, but the write-back below
            # used to omit the encoding (→ cp1252 on Windows), so files touched by
            # older builds hold cp1252 accents (é → 0xe9). Fall back to cp1252 so
            # those legacy files load; the UTF-8 write-back below then heals them.
            with open(tripinifile, 'rb') as _tf:
                _raw = _tf.read()
            try:
                _text = _raw.decode('utf-8')
            except UnicodeDecodeError:
                _text = _raw.decode('cp1252')
            tripconfig.read_string(_coerce_orphan_continuations(_text))
            # 'created' records when the trip's web link was first set up — set it
            # only once. (Previously this clobbered it on every read, so the date
            # never reflected the original setup.)
            if not tripconfig.has_option('trip_config', 'created') or \
                    not tripconfig['trip_config']['created'].strip():
                tripconfig['trip_config']['created'] = datetime.today().strftime('%Y-%m-%d %H:%M')
        else:
            # Ensure the sections exist before setting values
            if not tripconfig.has_section('trip_config'):
                tripconfig.add_section('trip_config')
            if not tripconfig.has_section('trip_text'):
                tripconfig.add_section('trip_text')        
            # set defaults
            tripconfig['trip_config']['master_repository_web'] = "/safari/"
            tripconfig['trip_config']['master_repository_disk'] = ""
            #tripconfig['trip_config']['repositorybanner'] = self.mp.getRepositoryBanner()
            tripconfig['trip_config']['trip_banner'] = "keerkringvogel.jpg"
            tripconfig['trip_config']['trip_picture'] = ""
            tripname = Path(self.mp.getBiotaWebRoot()).name
            tripconfig['trip_config']['trip_name'] = tripname
            tripconfig['trip_config']['created'] = datetime.today().strftime('%Y-%m-%d %H:%M')

            tripconfig['trip_text']['title_nl'] = tripname
            tripconfig['trip_text']['message_nl'] = "Een omschrijving van de trip " + tripname
            tripconfig['trip_text']['title_en'] = tripname
            tripconfig['trip_text']['message_en'] = "A description of the trip " + tripname

        # now write updated / created version back — always UTF-8, matching the
        # read above. Omitting the encoding wrote cp1252 on Windows, which then
        # broke the next UTF-8 read; keeping one encoding closes that loop.
        with open(tripinifile, 'w', encoding='utf-8') as tripfile:
            tripconfig.write(tripfile)

        # at least one must have master
        if self.master_repository_web == "" :  
            self.master_repository_web = tripconfig['trip_config']['master_repository_web']
        if self.master_repository_disk == "":
            self.master_repository_disk = tripconfig['trip_config']['master_repository_disk']

        # now create a dictionary
        trip_dict = {}
        for section in tripconfig.sections():
            #trip_dict[section] = {}
            for key, value in tripconfig.items(section):
                trip_dict[key] = value
        return trip_dict
    
    def read_TripAdmin_from_web(self, url):
        try:
            # Fetch the CSV content from the URL
            print(f"Reading from {url}")
            response = requests.get(url)
            response.raise_for_status()  # Raise an HTTPError for bad responses (4xx and 5xx)
            # The server doesn't set a charset for .csv, so requests falls
            # back to ISO-8859-1 and turns UTF-8 bytes (e.g. ë → 0xC3 0xAB)
            # into mojibake. Force UTF-8 to match how the file was written.
            response.encoding = 'utf-8'

            # Read the content using csv.DictReader
            csvfile = io.StringIO(response.text)  # Convert response text to a file-like object
            reader = csv.DictReader(csvfile, delimiter=';')

            for row in reader:
                if row['trip_name'] != "":
                    self.addTrip(row)
        except Exception as e:
            print(f"An error occurred: {e}")  # Handle errors gracefully
            pass    # No trips yet, that's fine
        return

    
    # get info all/existing trips
    def read_TripAdmin(self):
        try:
            with io.open(self.getTripAdminFile(), mode="r", newline="", encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile, delimiter=';')
                for row in reader:
                    if row['trip_name'] != "":
                        self.addTrip(row)
        except:
            pass    # no trips yet, that's fine
        return
    
    # write back 
    def write_TripAdmin(self):
        # Write Picture CSV file
        logger.info(f'Write all trip data in CSV file "{self.getTripAdminFile()}".')
        # Canonical column order — also the fallback when no trips remain, so an
        # emptied admin still writes a valid header-only CSV instead of crashing.
        headers = ['master_repository_web', 'master_repository_disk', 'trip_banner',
                   'trip_picture', 'created', 'trip_name', 'fromdate', 'todate',
                   'oneblog', 'title_nl', 'message_nl', 'title_en', 'message_en']
        for tripitem in self.trip_dict:
            headers = self.trip_dict[tripitem].keys()
            break

        with open(self.getTripAdminFile(), "w", newline="", encoding='utf-8') as csvfile:
            # needed to get column in order          
            writer = csv.DictWriter(csvfile, fieldnames=headers, delimiter=';')
            writer.writeheader()

            for tripitem in self.trip_dict:
                trip_object = self.trip_dict[tripitem]
                row = {
                    'trip_name': trip_object.get('trip_name', ''), 
                    'title_nl': trip_object.get('title_nl', ''), 
                    'title_en': trip_object.get('title_en', ''),
                    'message_nl': trip_object.get('message_nl', ''),
                    'message_en': trip_object.get('message_en', ''),
                    # Preserve the original setup date; only stamp today for a
                    # trip that has none yet (a freshly created one).
                    'created': trip_object.get('created', '').strip()
                               or datetime.today().strftime('%Y-%m-%d %H:%M'),
                    'master_repository_disk': trip_object.get('master_repository_disk', ''), 
                    'master_repository_web': trip_object.get('master_repository_web', ''),                 
                    'trip_banner': trip_object.get('trip_banner', ''),
                    'trip_picture': trip_object.get('trip_picture', ''),
                    'fromdate': trip_object.get('fromdate', ''),
                    'todate': trip_object.get('todate', ''),
                    'oneblog': trip_object.get('oneblog', '')
                }                                             
                writer.writerow(row)
            
        return

    def readAndWriteTripAdmin(self):
        self.read_TripAdmin()                       # read existing trip admin
        tripinifile = os.path.join(self.mp.getRepositoryPath(), "trip.ini")
        trip_row = self.readTrip(tripinifile)       # read this new trip.ini file
        if trip_row != None and trip_row != {}:
            self.addTrip(trip_row)                  # add (or update) newly read trip.ini
            self.write_TripAdmin()                  # write updated trip dictionary


    # create updated index page
    def updateTripIndexPage(self, language):
        MyProject = self.mp
        MyProject.breadcrumb.clearBreadCrumb()

        # read all lines from the HTML template
        if language.lower() == 'nl': 
            MyProject.breadcrumb.addBCsimple("/nature/intro-nl.html", "Natuur")
            MyProject.breadcrumb.addBCsimple("/nature/tripindex-nl.html", "Trips")
        else: 
            MyProject.breadcrumb.addBCsimple("/nature/intro-en.html", "Nature")
            MyProject.breadcrumb.addBCsimple("/nature/tripindex-en.html", "Trips")

        triptemplate = MyProject.getTemplateLines("tripindex-" + language + ".html")
        with open((os.path.join(self.getMasterRepository(), "tripindex-" + language + ".html")).lower(), "w", encoding='UTF-8') as fileTripIndex:
            for templateline in triptemplate:
                line = getTemplateOutputString(MyProject, None, '', '', templateline.rstrip('\n'))
                print(line, file=fileTripIndex)
            fileTripIndex.close()

        logger.info(f'Trip index written to {os.path.join(self.getMasterRepository(), "tripindex-" + language + ".html")}.')
        return

    def openSFTP(self, pwd):
        MyProject = self.mp
        if MyProject.online:        
            sftp = engine.sftplib.Sftp(password=pwd)
            # Connect to SFTP
            sftp.connect()
        else:
            sftp= None
        return sftp
    
    def uploadTrip(self, sftp, sftpPictures = False):
        # # trip post and pictures
        MyProject = self.mp     
        # biotatrip = MyProject.getBiotaTripDir()
        fromfile = os.path.join(self.getMasterRepository(), "tripindex-nl.html")
        toweb = sftp.remotePrefix() + self.getMasterBiotaWebRoot() + "tripindex-nl.html"
        sftp.upload(fromfile, toweb)
        sftp.upload(fromfile.replace('-nl','-en'), toweb.replace('-nl','-en'))
        print(f'Uploaded {fromfile} to {self.getMasterBiotaWebRoot() + "tripindex-nl.html"}')
        print(f'Uploaded {fromfile.replace("-nl","-en")} to {self.getMasterBiotaWebRoot() + "tripindex-en.html"}')        
        return

    def uploadTripAdmin(self, sftp, path):
        # # trip post and pictures
        MyProject = self.mp     
        # biotatrip = MyProject.getBiotaTripDir()
        fromfile = self.getTripAdminFile()
        toweb = sftp.remotePrefix() + path
        sftp.upload(fromfile, toweb)
        print(f'Uploaded {fromfile} to {path}')
        return
