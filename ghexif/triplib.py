import os
import io
from pathlib import Path
import csv
from datetime import datetime, date
import ghexif.projectlib
import logging
import configparser

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
            self.tripname = Path(self.mp.getBiotaWebRoot()).name
        except:
            pass
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
            # create a separete config instance
            tripconfig.read(tripinifile)
            tripconfig['trip_config']['created'] = datetime.today().strftime('%Y-%m-%d %H:%M')
        else:
            # Ensure the sections exist before setting values
            if not tripconfig.has_section('trip_config'):
                tripconfig.add_section('trip_config')
            if not tripconfig.has_section('trip_text'):
                tripconfig.add_section('trip_text')        
            # set defaults
            tripconfig['trip_config']['master_repository_web'] = "/safari/"
            tripconfig['trip_config']['master_repository_disk'] = "c:/laptop/Zuid Afrika"            
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

        # now write updated / created version back
        with open(tripinifile, 'w') as tripfile:
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
        for tripitem in self.trip_dict:
            headers = self.trip_dict[tripitem].keys()
            break

        with open(self.getTripAdminFile(), "w", newline="", encoding='utf-8') as csvfile:
            # needed to get column in order          
            writer = csv.DictWriter(csvfile, fieldnames=headers, delimiter=';')
            writer.writeheader()

            for tripitem in self.trip_dict:
                trip_object = self.trip_dict[tripitem]
                row = {}
                row['trip_name'] = trip_object['trip_name'] 
                row['title_nl'] = trip_object['title_nl'] 
                row['title_en'] = trip_object['title_en']
                row['message_nl'] = trip_object['message_nl']
                row['message_en'] = trip_object['message_en']
                #row['Project'] = self.mp.ProjectName()
                row['created'] = datetime.today().strftime('%Y-%m-%d %H:%M')
                row['master_repository_disk'] = trip_object['master_repository_disk'] 
                row['master_repository_web'] = trip_object['master_repository_web']                 
                row['trip_banner'] = trip_object['trip_banner']
                row['trip_picture'] = trip_object['trip_picture']
                # soon to be obsolete
                #row['repositorybanner'] = trip_object['repositorybanner']
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

