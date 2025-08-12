import os
import io
import csv
import time
import logging
from pygbif import species

from ghexif import COLUMN_NAMES, SELECTED_FIELDS, SPECIES_FIELDS
import ghexif
import ghexif.projectlib

from enum import Enum, auto

logger = logging.getLogger("speciesviewer")

# Biota, Kingdom, Phylum, Class, Order, Family, Species
class Level(Enum):
    SPECIES = auto()
    FAMILY = auto()
    ORDER = auto()
    CLASS = auto()
    PHYLUM = auto()
    KINGDOM = auto()
    BIOTA = auto()


class SpeciesClassification:
    sc_dict = {}
    dutch_dict = {}         # all species we have the Dutch name for, points to scientific name
    english_dict = {}       # all species we have the English name for, points to scientific name
    names_not_found_list = []

    DUMMYSPECIES = ['vogel', 'vogels', 'bird', 'libel', 'libellen', 'onbekend', 
                    'rups', 'panama', 'californie', 'onbekend', 'sprinkhaan', 'turkey',
                    'blog', 'goodidpicture',
                    'juvenile', 'juv.', 'ímmature', 'imm.', 'onvolwassen',  'adult', 'volwassen', 
                    'male', 'man', 'female', 'vrouw', 'couple', 'paar', 'paartje', 
                    'summer', 'zomer', 'winter', 'breeding', 'non-breeding']


    def __init__(self, MyProject):
        if MyProject != None:
            MyProject.registerSpeciesClassification(self)
        return 
    
    def clearDict(self):
        self.sc_dict = {}

    def scCopy(self):
        return self.sc_dict.copy()

    def createSpeciesRecord(self, level, scientificname, dutchname, englishname, upname):
        sc_record = {
            "Level": level, 
            "ScientificName": scientificname,
            "DutchName": dutchname,
            "EnglishName": englishname,
            "Up": upname,
            "Leaves": []
        }
        return sc_record    
 
    def readSpeciesCollection(self, speciesfilename, donotaddLanguageIndices=False):
        # Reads a CSV file line by line, splits by delimiter, and creates dictionaries.
        # Assumes the first row contains column headers.

        with io.open(speciesfilename, mode="r", newline="", encoding='utf-8') as csvfile:
            # Read the first line (header) to get column names
            header = csvfile.readline().strip().split(';')
            # column that needs special treatment
            list_columns = ['Leaves']

            # read line by line
            for line in csvfile:
                # Split the line by semicolon
                values = line.strip().split(';')
                # Create a dictionary for this row
                row_dict = {col: value for col, value in zip(header, values)}
                # make enums again coded
                level = row_dict['Level']
                if level == "Level.BIOTA":
                    row_dict['Level'] = Level.BIOTA
                elif level == "Level.KINGDOM":
                    row_dict['Level'] = Level.KINGDOM
                elif level == "Level.PHYLUM":
                    row_dict['Level'] = Level.PHYLUM  
                elif level == "Level.CLASS":
                    row_dict['Level'] = Level.CLASS  
                elif level == "Level.ORDER":
                    row_dict['Level'] = Level.ORDER  
                elif level == "Level.FAMILY":
                    row_dict['Level'] = Level.FAMILY                                 
                elif level == "Level.SPECIES":
                    row_dict['Level'] = Level.SPECIES

            
                # fix lists to native [Python list] conventions                
                for col in list_columns:
                    if row_dict[col] == '[]':           # update in place
                        row_dict[col] = []
                    else:
                        try:
                            # remove additional quoting
                            liststring = row_dict[col][1:-1].replace("'","")
                            listvalues = liststring.split(',')
                            # remove possible leading spaces
                            row_dict[col] = []                    
                            for value in listvalues:
                                if value.lstrip() not in row_dict[col]:
                                    row_dict[col].append(value.lstrip())
                        except ValueError:
                            pass  # Handle non-numeric values gracefully

                self.sc_dict[row_dict['ScientificName']] = row_dict

        logger.info(f"Read specification file, now create links.")
        try:
            for speciesname in self.sc_dict:
                # if speciesname == 'dasyprocta punctata': 
                #     singlestep = 1                
                species = self.sc_dict[speciesname]
                ScientificName = species['ScientificName']
                UpName = species['Up']
                if UpName != "" and ScientificName not in self.sc_dict[UpName]['Leaves']:
                    self.sc_dict[UpName]['Leaves'].append(ScientificName)
                
                if not donotaddLanguageIndices:
                    # add Nederlandse naam index
                    dutchname = species['DutchName']            
                    if dutchname != "":
                        if dutchname not in self.dutch_dict:
                            self.dutch_dict[dutchname] = ScientificName

                    # add Nederlandse naam index
                    englishname = species['EnglishName']            
                    if englishname != "":
                        if englishname not in self.english_dict:
                            self.english_dict[englishname] = ScientificName
            
            logger.info(f'Read {len(self.sc_dict)} predefined species from {speciesfilename}.')
        except Exception as e:
            logger.error(f'Reading {speciesname} from {speciesfilename} failed, inconsistent reference in file: {e}')
        return

    # save last version, always save last version under .csv_old  
    def writeSpeciesCollection(self, speciesfilename):
        # Write Picture CSV file
        logger.info(f'Write {len(self.sc_dict)} updated species data CSV file "{speciesfilename}".')
        with open(speciesfilename, "w", newline="", encoding='utf-8') as csvfile:
            # needed to get column in order
            sc_headers = ['Level', 'ScientificName', 'DutchName', 'EnglishName', 'Up', 'Leaves']                 
            writer = csv.DictWriter(csvfile, fieldnames=sc_headers, quoting=csv.QUOTE_MINIMAL, delimiter=';')
            writer.writeheader()
            for species in self.sc_dict:
                spec_dict = self.sc_dict[species]
                row = {}
                row["Level"] = spec_dict['Level'] 
                row["ScientificName"] = spec_dict['ScientificName']
                row["DutchName"] = spec_dict['DutchName']
                row["EnglishName"] = spec_dict['EnglishName']
                row["Up"] = spec_dict['Up']
                row["Leaves"] = spec_dict['Leaves']

                writer.writerow(row)                    
        return   

    # merge both SpeciesCollections
    def mergeSpeciesCollections(self, newSC):
        for species in newSC:
            if species in self.sc_dict: continue
            speciesobject = newSC[species] 
            self.sc_dict[species] = speciesobject
    
        return


    # keep previous version and latest
    def updateSpeciesCollection(self, speciesfilename):
        if os.path.exists(speciesfilename + 'old'):  
            os.remove(speciesfilename + 'old')
        if os.path.exists(speciesfilename):  
            os.rename(speciesfilename, speciesfilename + 'old')
        # make new file with latest additions
        self.writeSpeciesCollection(speciesfilename)

    # getCommonVernacularNames: Process all languages, and sort the names based on frequency.
    # Some species have many names, like 
    # Engels: common stork's-bill 
    #           ["common stork's-bill", 'alfilaria', 'common crowfoot', 'common erodium', 
    #            "common heron's-bill", "heron's-bill", 'red-stem filaree', "stork's-bill"]
    # NL:     duinreigersbek ['duinreigersbek', 'gewone reigersbek', 'reigersbek']
    #
    # Parameters:
    #   soort:    a dictionary describing the species from the GBIF (backbone) data
    #   rankname: the rank we are looking, eg kingdom, class or species
    #             This specifie where to look
    # returns:
    #   The getCommonVernacularNames function returns the complete structure,
    #   the names by language in descending frequency
    #
    def getCommonVernacularNames(self, soort, rankname):
        language_counts  = {}
        key = rankname + 'Key'
        vernacularNames = species.name_usage(key=soort[key], data='vernacularNames', rank=rankname)

        # proposed from CoPilot ok
        for v in vernacularNames['results']:
            # Update the dictionary with the species name and language
            language_name = v['language']
            if language_name not in language_counts:
                language_counts[language_name] = []
            language_counts[language_name].append(v["vernacularName"].lower())

        # Sort species lists by occurrence count (descending)
        # it gets tricky for CoPilot :-).  But together we make this 
        language_totals  = {}
        for l in language_counts:
            language_totals[l] = []
            #print(f"for {l} we have")
            name_counts = {}
            for name in language_counts[l]:
                name_counts[name] = name_counts.get(name, 0) + 1

            sorted_names = sorted(name_counts.items(), key=lambda x: (-x[1], x[0]))
            unique_names = [name for name, _ in sorted_names]
            for name in unique_names:
                language_totals[l].append(name)
            # print(f"sorted_names={sorted_names}\n unique_names={unique_names}")

        return language_totals

    # getNldEngVernacularNames: Extract the most often referenced English and Dutch 
    #                           names from getCommonVernacularNames
    # 
    # Engels: common stork's-bill 
    #           ["common stork's-bill", 'alfilaria', 'common crowfoot', 'common erodium', 
    #            "common heron's-bill", "heron's-bill", 'red-stem filaree', "stork's-bill"]
    # NL:     duinreigersbek
    #           ['duinreigersbek', 'gewone reigersbek', 'reigersbek']
    #
    # Parameters:
    #   soort:    a dictionary describing the species from the GBIF (backbone) data
    #   rankname: the rank we are looking, eg kingdom, class or species
    #             This specifie where to look
    # returns:
    #   The English and Dutch names
    #
    def getNldEngVernacularNames(self, soort, rankname):
        try:
            names = self.getCommonVernacularNames(soort, rankname)
            nederlands = names.get('nld', [''])[0]
            engels = names.get('eng', [''])[0]
        except:
            nederlands = ""
            engels = ""
        return nederlands, engels

    # Call createSpeciesRecord to add entry, and add uplink and child in up
    def addSpeciesLevel(self, level, scientificname, dutchname, englishname, upname):
        if scientificname not in self.sc_dict:
            # create it
            self.sc_dict[scientificname] = self.createSpeciesRecord(level, scientificname, dutchname, englishname, upname)
        else:
            # maybe we get more info
            if dutchname != "" and self.sc_dict[scientificname]['DutchName'] == "":
                self.sc_dict[scientificname]['DutchName'] = dutchname
            if englishname != "" and self.sc_dict[scientificname]['EnglishName'] == "":            
                self.sc_dict[scientificname]['EnglishName'] = englishname

        # save as a child if up is defined
        if upname != "" and scientificname not in self.sc_dict[upname]['Leaves']:            
            self.sc_dict[upname]['Leaves'].append(scientificname)        
        return

    def getVernacularNames(self, soort, key):
        engels = "" 
        nederlands = ""
        vernacularNames = species.name_usage(key=soort[key], data='vernacularNames')
        for v in vernacularNames['results']:
    #       print(f'{v["vernacularName"]} in taal {v["language"]}')
            if v["language"] == "eng" and engels == "":
                engels = v["vernacularName"]
            if v["language"] == "nld" and nederlands == "":
                nederlands = v["vernacularName"]
            if engels != "" and nederlands != "":
                break
        return engels.lower(), nederlands.lower()

    # add species based on metadata from GBIF
    def addSpecies(self, gbif):
        namen = self.getNldEngVernacularNames(gbif, 'kingdom') if 'kingdomKey' in gbif else ("", "")
        self.addSpeciesLevel(Level.KINGDOM, gbif['kingdom'].lower(), namen[0], namen[1], 'biota')
        namen = self.getNldEngVernacularNames(gbif, 'phylum') if 'phylumKey' in gbif else ("", "")
        self.addSpeciesLevel(Level.PHYLUM, gbif['phylum'].lower(), namen[0], namen[1], gbif['kingdom'].lower())
        namen = self.getNldEngVernacularNames(gbif, 'class') if 'classKey' in gbif else ("", "")    
        self.addSpeciesLevel(Level.CLASS, gbif['class'].lower(), namen[0], namen[1], gbif['phylum'].lower())
        ordernames = self.getNldEngVernacularNames(gbif, 'order') if 'orderKey' in gbif else ("", "")      
        namen = self.getNldEngVernacularNames(gbif, 'family') if 'familyKey' in gbif else ("", "")
        try:
            self.addSpeciesLevel(Level.ORDER, gbif['order'].lower(), ordernames[0], ordernames[1], gbif['class'].lower())
            self.addSpeciesLevel(Level.FAMILY, gbif['family'].lower(), namen[0], namen[1], gbif['order'].lower())
        except:
            try: 
                print(f"Will link Family to Class because there is no ORDER known for {gbif['canonicalName']}.")       
                self.addSpeciesLevel(Level.FAMILY, gbif['family'].lower(), namen[0], namen[1],  gbif['class'].lower())
            except:
                print(f"No ORDER or FAMILY for {gbif['canonicalName']}, that is weird")
        namen = self.getNldEngVernacularNames(gbif, 'species') if 'speciesKey' in gbif else ("", "")            
        self.addSpeciesLevel(Level.SPECIES, gbif['canonicalName'].lower(), namen[0], namen[1],  gbif['family'].lower())

        return gbif['canonicalName'].lower()

    # look for names in all three tables
    # add some logic to massage species
    def getKnownSpecies(self, subject, nested, LookUpInGBIF=False):
        scientificname = ''
        if LookUpInGBIF: 
            timesoort = time.time()
        if subject == "" or subject in self.DUMMYSPECIES:
            return ""
        elif subject in self.sc_dict:
            return subject        
        elif subject in self.dutch_dict:
            return self.dutch_dict[subject]
        elif subject in self.english_dict:
            return self.english_dict[subject]
        elif LookUpInGBIF and (',' not in subject):
            try: 
                # check if we already searched for this species
                if subject not in self.names_not_found_list:
                    gbif = species.name_backbone(name=subject)
                    if gbif['matchType'] == 'EXACT' and gbif.get('species'):
                        scientificname = self.addSpecies(gbif).lower()
                        timesoort = time.time() - timesoort
                        logger.info(f'The species "{gbif["canonicalName"].lower()}" is found in the GBIF database in {timesoort:.3f} seconds, class {gbif["class"]} in {gbif["kingdom"]}.')
                    else:
                        timesoort = time.time() - timesoort
                        logger.info(f"""The species "{subject}" is not found in the GBIF database as a scientific name, took {timesoort:.3f} seconds.""")
                        self.names_not_found_list.append(subject)
            except:
                logger.warning(f'Timeout on internet connection')
        elif not nested:
            # likely a list of variables, decode it
            if len(subject.split(','))>1:
                subject = subject.strip('[]')
                #print(f"{subject.split(',')}")
                for subjects in subject.split(','):
                    if subjects[0] == '"':       # possibly quoted that we do not want
                        subjects = subjects.strip('"')
                    subjects = subjects.lstrip().rstrip()
                    if subjects[0] == "'":       # possibly quoted that we do not want
                        subjects = subjects.strip("'")
                    if subjects[0] == '"':       # remove " in eg "'\'female\', "passerini\'s tanager"'"
                        subjects = subjects.strip('"')
                    # categories are interpreted as Aves etc, avoid this
                    if subjects.lower() not in self.DUMMYSPECIES:
                        scientificname = self.getKnownSpecies(subjects, True, LookUpInGBIF)
                        if scientificname != "":
                            # if self.SpeciesDebug > 8:
                            #     print(f"In {subject.split(',')} - Found {subjects} -> {scientificname}")
                            return scientificname
            
        return scientificname

    # See if we recognize the species from either languages, if not call GBIF
    def getSpecies(self, rawsubject):       
        # lower case
        subject = rawsubject.lower().lstrip().rstrip()
        nested = False

        scientificname = self.getKnownSpecies(subject, nested, LookUpInGBIF=False)

        # not found, see if it is a new species
        if (scientificname == ""):
            scientificname = self.getKnownSpecies(subject, nested,  LookUpInGBIF=True)   
    
        return scientificname

