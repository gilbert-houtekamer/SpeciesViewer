import os
import re
import csv
import io
import logging
from collections import OrderedDict

from engine import SPECIES_FIELDS
from engine.subspecies import fold_species_flags

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
    # Non-taxonomic "topics" branch (topics/<topic>/<leaf>), appended AFTER the
    # taxonomic levels so their .value never renumbers the existing members. See
    # engine/topics.py. TOPICS mirrors BIOTA (root), TOPIC the middle node,
    # TOPICLEAF the countable leaf.
    TOPICS = auto()
    TOPIC = auto()
    TOPICLEAF = auto()

class Species:
    # specieslib
    species_dict = {}       # all species we have the names for, points to Dictonory
    sc = None
    mp = None

    def __init__(self, MyProject):
        # Per-instance state. species_dict is declared at class level above, which
        # (pre-GUI, one process per batch job) was harmless — but in the long-lived
        # GUI process that shared dict leaked counters across runs (a newly added
        # species kept accumulating iPictureThisRunCount). Reset it per instance.
        self.species_dict = {}
        self.sc = None
        self.mp = None
        if MyProject != None:
            MyProject.registerSpecies(self)
            self.mp = MyProject
        return

    def register_sc_with_sp(self, sc):
        self.sc = sc

    def getMP(self):
        return self.mp

    def createSpeciesRecord(self,level, scientificname, dutchname, englishname, upname):
        classification_record = {
            "Level": level,
            "ScientificName": scientificname,
            "DutchName": dutchname,
            "EnglishName": englishname,
            "Up": upname,
            "Leaves": [],
            "JPGmap": [],
            "currentHighestRating": int(0),
            "BestJPG": "",
            "BestUPN": "",
            "BestJPGPathName": "",
            "TargetPath":"",
            "iSpeciesThisRunCount": int(0),
            "iSpeciesCollectionCount": int(0),
            "iPictureThisRunCount": int(0),
            "iPictureCollectionCount": int(0),
            "iPicturesWithJPGsThisRun": int(0),
            "iPicturesWithJPGsCollection": int(0)
        }
        return classification_record

    # we use the species metadata from Lightroom or DxO get properlies like male/female, summer/winter, etc
    def getBirdInfo(self, speciesname):
        malefemale = ""
        ageintext = ""
        summerwinter = ""
        splitted = re.split(r"[ \[\],;90]", speciesname.replace("'",""))
        for token in splitted:
            if token.lower() in ["male", "man"]:
                malefemale = "male"
            elif token.lower() in ["female", "vrouw"]:
                malefemale = "female"
            elif token.lower() in ["couple", "paar", "paartje"]:
                malefemale = "couple"
            elif token.lower() in ["breeding", "summer", "zomer"]:
                summerwinter = "br."
            elif token.lower() in ["non-breeding", "winter"]:
                summerwinter = "non-br."
            elif token.lower() in ["juvenile", "jong", "juv.", "juv"]:
                ageintext = "juv."
            elif token.lower() in ["immature", "onvolwassen", "imm.", "imm"]:
                ageintext = "imm."
            elif token.lower() in ["adult", "volwassen", "ad.", "ad"]:
                ageintext = "ad."
        return malefemale, ageintext, summerwinter

    def getSpecies(self, name):
        name = name.lower()
        scientificname = self.sc.getSpecies(name)
        if scientificname != "":
            try:
                spobject = self.species_dict[scientificname]
            except:                       # not found in species (sp) database
                # make new species
                scinstance = self.sc.sc_dict[scientificname]
                spobject = self.createSpeciesRecord(
                    Level.SPECIES,
                    scientificname,
                    scinstance['DutchName'], scinstance['EnglishName'],
                    scinstance['Up'])
                self.species_dict[scientificname] = spobject
                # make sure all up's also exist
                self.addParents(scinstance)
            return scientificname
        return None   # unresolved: not in any table and GBIF gave no match

    def addParents(self, scinstance):
        next = self.sc.sc_dict[scinstance['Up']]['ScientificName']
        while next != "":       # may need to skip levels
            sc_level_instance = self.sc.sc_dict[next]
            try:
                spname = self.species_dict[next]
            except:
                self.species_dict[sc_level_instance['ScientificName']] = self.createSpeciesRecord(
                    sc_level_instance['Level'],
                    sc_level_instance['ScientificName'],
                    sc_level_instance['DutchName'], sc_level_instance['EnglishName'],
                    sc_level_instance['Up'])

            next = sc_level_instance['Up']
        # now that all levels exist, we can add Leave link
        next = self.sc.sc_dict[scinstance['Up']]['ScientificName']
        sc_sciencename = scinstance['ScientificName']
        while next != "":       # may need to skip levels
            sc_level_instance = self.sc.sc_dict[next]
            # save as a child if up is defined
            if sc_sciencename not in self.species_dict[next]['Leaves']:
                self.species_dict[next]['Leaves'].append(sc_sciencename)
            sc_sciencename = next
            next = self.sc.sc_dict[next]['Up']

    # the path is defined by the classification of the species, from "biota" (life)
    def getPathToSpecies(self, species):
        thislevel = self.species_dict[species]
        path = thislevel['ScientificName']
        while thislevel['Up'] != "":
            thislevel = self.species_dict[thislevel['Up']]
            path = thislevel['ScientificName'] + '/' + path

        path = path + '/'
        return path

    def readSpeciesInfo(self, speciesfilename, reset_all_counters):
    # Reads a CSV file line by line, splits by delimiter, and creates dictionaries.
    # Assumes the first row contains column headers.
        with io.open(speciesfilename, mode="r", newline="", encoding='utf-8') as csvfile:
            header = csvfile.readline().strip().split(';')
            numeric_columns = ['currentHighestRating', 'iSpeciesThisRunCount', 'iSpeciesCollectionCount',\
                                'iPictureThisRunCount', 'iPictureCollectionCount', 'iPicturesWithJPGsThisRun', 'iPicturesWithJPGsCollection']
            list_columns = ['Leaves', 'JPGmap']

            for line in csvfile:
                values = line.strip().split(';')
                row_dict = {col: value for col, value in zip(header, values)}
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
                elif level == "Level.TOPICS":
                    row_dict['Level'] = Level.TOPICS
                elif level == "Level.TOPIC":
                    row_dict['Level'] = Level.TOPIC
                elif level == "Level.TOPICLEAF":
                    row_dict['Level'] = Level.TOPICLEAF

                if reset_all_counters:
                    for col in numeric_columns: row_dict[col] = 0
                    row_dict['BestJPG'] = ''
                    row_dict['BestUPN'] = ''
                    row_dict['BestJPGPathName'] = ''
                    row_dict['TargetPath'] = ''
                    row_dict['JPGmap'] = []
                    list_columns = ['Leaves']
                else:
                    list_columns = ['Leaves', 'JPGmap']
                    for col in numeric_columns:
                        try:
                            row_dict[col] = int(row_dict[col])
                        except ValueError:
                            pass

                for col in list_columns:
                    if row_dict[col] == '[]':
                        row_dict[col] = []
                    else:
                        try:
                            liststring = row_dict[col][1:-1].replace("'","")
                            listvalues = liststring.split(',')
                            row_dict[col] = []
                            for value in listvalues:
                                if value.lstrip() not in row_dict[col]:
                                    row_dict[col].append(value.lstrip())
                        except ValueError:
                            pass

                self.species_dict[row_dict['ScientificName']] = row_dict

        for speciesname in self.species_dict:
            species = self.species_dict[speciesname]
            ScientificName = species['ScientificName']
            UpName = species['Up']
            if UpName != "" and ScientificName not in self.species_dict[UpName]['Leaves']:
                self.species_dict[UpName]['Leaves'].append(ScientificName)
        logger.info(f'Read {len(self.species_dict)} predefined species from {speciesfilename}.')

        return

    def writeSpeciesInfo(self, speciesfilename, commit_counters):
        logger.info(f'Write all used species data CSV file "{speciesfilename}".')
        with open(speciesfilename, "w", newline="", encoding='utf-8') as csvfile:
            speciesheaders = OrderedDict((field, None) for field in SPECIES_FIELDS)
            writer = csv.DictWriter(csvfile, fieldnames=speciesheaders, quoting=csv.QUOTE_MINIMAL, delimiter=';')
            writer.writeheader()
            for species in self.species_dict:
                spec_dict = self.species_dict[species]
                row = {}
                row["Level"] = spec_dict['Level']
                row["ScientificName"] = spec_dict['ScientificName']
                row["DutchName"] = spec_dict['DutchName']
                row["EnglishName"] = spec_dict['EnglishName']
                row["Up"] = spec_dict['Up']
                row["Leaves"] = spec_dict['Leaves']
                row["JPGmap"] = spec_dict['JPGmap']
                row["currentHighestRating"] = spec_dict["currentHighestRating"]
                row["BestJPG"] = spec_dict['BestJPG']
                row["BestUPN"] = spec_dict['BestUPN']
                row["BestJPGPathName"] = spec_dict['BestJPGPathName']
                row["TargetPath"] = spec_dict['TargetPath']

                if commit_counters:
                    row["iSpeciesCollectionCount"] = \
                        int(spec_dict["iSpeciesCollectionCount"]) + int(spec_dict['iSpeciesThisRunCount'])
                    row["iPictureCollectionCount"] = \
                        int(spec_dict["iPictureCollectionCount"]) + int(spec_dict["iPictureThisRunCount"])
                    row["iPicturesWithJPGsCollection"] = \
                        int(spec_dict["iPicturesWithJPGsCollection"]) + int(spec_dict["iPicturesWithJPGsThisRun"])
                else:
                    row["iSpeciesCollectionCount"] = spec_dict["iSpeciesCollectionCount"]
                    row["iPictureCollectionCount"] = spec_dict["iPictureCollectionCount"]
                    row["iPicturesWithJPGsCollection"] = spec_dict["iPicturesWithJPGsCollection"]

                if commit_counters:
                    row["iSpeciesThisRunCount"] = 0
                    row["iPictureThisRunCount"] = 0
                    row["iPicturesWithJPGsThisRun"] = 0
                else:
                    row["iSpeciesThisRunCount"] = spec_dict['iSpeciesThisRunCount']
                    row["iPictureThisRunCount"] = spec_dict["iPictureThisRunCount"]
                    row["iPicturesWithJPGsThisRun"] = spec_dict["iPicturesWithJPGsThisRun"]

                writer.writerow(row)
        return

    def summarizeSpeciesCounters(self, soort, results, debugfile):
        species = self.species_dict[soort]

        if (species['Level'] == 'Level.SPECIES') or (species['Level'] == Level.SPECIES):
            results = [int(species['iPictureCollectionCount']), int(species['iPictureThisRunCount']), \
                        int(species['iPicturesWithJPGsCollection']), int(species['iPicturesWithJPGsThisRun']), \
                        int(species['iSpeciesCollectionCount']), int(species['iSpeciesThisRunCount']), \
                        species['BestJPG'], species['BestJPGPathName'], species['BestUPN'], \
                        int(species['currentHighestRating'])]
        else:
            if species['TargetPath'] == "":
                species['TargetPath'] = self.getPathToSpecies(species['ScientificName'])
            results = []
            species_flags = []      # (name, collection, new) per child, folded below
            for leave in species['Leaves']:
                if leave == "agaricomycetes":
                    x = 1
                tempresults = self.summarizeSpeciesCounters(leave, results, debugfile)
                species_flags.append((leave, tempresults[4], tempresults[5]))
                i = 0
                for number in tempresults:
                    if len(results) > i:
                        if i < 6:
                            results[i] += number
                        elif results[9] <= tempresults[9] and tempresults[6] != "":
                            for j in [6,7,8,9]:
                                results[j] =  tempresults[j]
                    else:
                        results.append(number)
                    i += 1
            if results == []:
                print(f'hoe kan dit??')
            else:
                # a species and its subspecies count once (engine/subspecies.py)
                results[4], results[5] = fold_species_flags(
                    self.sc.sc_dict if self.sc else {}, species_flags)
                species['iPictureCollectionCount'] = results[0]
                species['iPictureThisRunCount'] = results[1]
                species['iPicturesWithJPGsCollection'] = results[2]
                species['iPicturesWithJPGsThisRun'] = results[3]
                species['iSpeciesCollectionCount'] = results[4]
                species['iSpeciesThisRunCount'] = results[5]
                if (results[3]+results[2]>0):
                    if (species['BestJPG'] == "" and results[6] != "") or (int(results[9]) > species['currentHighestRating']):
                        species['BestJPG'] = results[6]
                        species['BestJPGPathName'] = results[7]
                        species['BestUPN'] = results[8]
                        species['currentHighestRating'] = int(results[9])

        return [results[0], results[1], results[2], results[3], results[4], results[5], results[6], results[7], results[8], results[9]]

    def printDataForSpecies(self, soort, debugfile):
        species = self.species_dict[soort]
        if int(species['iPictureCollectionCount']) > 0:
            if species['BestJPG'] == "":
                logger.info(f"""{species['Level']} {species['ScientificName']} ({species['EnglishName']}) has no identified JPGs to show from {species['iPicturesWithJPGsCollection']} JPGs.""")
            if species['currentHighestRating'] == '0':
                logger.info(f"{species['Level']} {species['ScientificName']} ({species['EnglishName']}) has zero as text string.")
        for leave in species['Leaves']:
            self.printDataForSpecies(leave, debugfile)
        return

    def printSpeciesTree(self, scientificname, level, classification):
        species = self.species_dict[scientificname]
        if (int(classification) >= 4 and \
            ((species['Level'] != 'Level.SPECIES') or (species['Level'] != Level.SPECIES) \
            or (int(classification) >= 8))):
            logger.info(f"{species['Level']:15}: {species['DutchName']:32}  {species['EnglishName']:32}  {species['ScientificName']}")
        for leaf in species['Leaves']:
            self.printSpeciesTree(leaf, level + 1, classification)
        return
