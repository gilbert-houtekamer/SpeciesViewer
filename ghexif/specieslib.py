import os
import csv
import io
import time
from collections import OrderedDict

from ghexif import COLUMN_NAMES, SELECTED_FIELDS, SPECIES_FIELDS
from ghexif import files_dict, dutch_dict, english_dict, species_dict
import ghexif
import ghexif.projectlib
from ghexif.handling import read_file, read_pictures

from enum import Enum, auto

# Biota, Kingdom, Phylum, Class, Order, Family, Species
class Level(Enum):
    SPECIES = auto()
    FAMILY = auto()
    ORDER = auto()
    CLASS = auto()
    PHYLUM = auto()
    KINGDOM = auto()
    BIOTA = auto()

class Species:
    # specieslib
    species_dict = {}       # all species we have the names for, points to Dictonory
    # dutch_dict = {}         # all species we have the Dutch name for, points to scientific name
    # english_dict = {}       # all species we have the English name for, points to scientific name
    sc = None
    mp = None

    def __init__(self, MyProject):
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
    
    def getSpecies(self, name):
        name = name.lower()
        # does the directory have it?  
        # If so, return scientific name .  If not, get it from GBIF
        # if all fails, return ""
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
            # Read the first line (header) to get column names
            header = csvfile.readline().strip().split(';')
            # columns that need special treatment
            numeric_columns = ['currentHighestRating', 'iSpeciesThisRunCount', 'iSpeciesCollectionCount',\
                                'iPictureThisRunCount', 'iPictureCollectionCount', 'iPicturesWithJPGsThisRun', 'iPicturesWithJPGsCollection']
            list_columns = ['Leaves', 'JPGmap']

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
                    # fix numeric, int() for applicable numbers
                    for col in numeric_columns:
                        try:
                            row_dict[col] = int(row_dict[col])  # Convert to integer
                        except ValueError:
                            pass  # Handle non-numeric values gracefully
                
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

                self.species_dict[row_dict['ScientificName']] = row_dict

        for speciesname in self.species_dict:
            species = self.species_dict[speciesname]
            ScientificName = species['ScientificName']
            UpName = species['Up']
            if UpName != "" and ScientificName not in self.species_dict[UpName]['Leaves']:
                self.species_dict[UpName]['Leaves'].append(ScientificName)
            
            # # add Nederlandse naam index
            # dutchname = species['DutchName']            
            # if dutchname != "":
            #     if dutchname not in dutch_dict:
            #         dutch_dict[dutchname] = ScientificName

            # # add Nederlandse naam index
            # englishname = species['EnglishName']            
            # if englishname != "":
            #     if englishname not in english_dict:
            #         english_dict[englishname] = ScientificName
        
        print(f'Read {len(self.species_dict)} predefined species from {speciesfilename}.')
        
        return

    def writeSpeciesInfo(self, speciesfilename, commit_counters):
        # Write Picture CSV file
        print(f'Write all used species data CSV file "{speciesfilename}".')
        with open(speciesfilename, "w", newline="", encoding='utf-8') as csvfile:
            # needed to get column in order
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
                row["JPGmap"] = spec_dict['JPGmap'] # {}
                row["currentHighestRating"] = spec_dict["currentHighestRating"]
                row["BestJPG"] = spec_dict['BestJPG']
                row["BestUPN"] = spec_dict['BestUPN']  
                row["BestJPGPathName"] = spec_dict['BestJPGPathName']
                row["TargetPath"] = spec_dict['TargetPath']
                
                if commit_counters:
                    # keep unchanged
                    row["iSpeciesCollectionCount"] = \
                        int(spec_dict["iSpeciesCollectionCount"]) + int(spec_dict['iSpeciesThisRunCount'])     
                    row["iPictureCollectionCount"] = \
                        int(spec_dict["iPictureCollectionCount"]) + int(spec_dict["iPictureThisRunCount"])
                    row["iPicturesWithJPGsCollection"] = \
                        int(spec_dict["iPicturesWithJPGsCollection"]) + int(spec_dict["iPicturesWithJPGsThisRun"])
                else:    
                    # keep unchanged
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
        global SpeciesDebug    

        # if soort == 'asphodelus fistulosus':
        #     x = 1

        species = self.species_dict[soort]
        # if SpeciesDebug > 4 and int(species['iPictureCollectionCount']) > 0:
        #     print(f"summarizeSpeciesCounters: {species['Level']}:  {species['iPictureCollectionCount']:6}  {species['iPicturesWithJPGsCollection']:6} \
        #         {species['ScientificName']:32} / {species['DutchName']} / {species['EnglishName']}", file=debugfile)
        
        # if species, return counters
        if (species['Level'] == 'Level.SPECIES') or (species['Level'] == Level.SPECIES):
            results = [int(species['iPictureCollectionCount']), int(species['iPictureThisRunCount']), \
                        int(species['iPicturesWithJPGsCollection']), int(species['iPicturesWithJPGsThisRun']), \
                        int(species['iSpeciesCollectionCount']), int(species['iSpeciesThisRunCount']), \
                        species['BestJPG'], species['BestJPGPathName'], species['BestUPN'], \
                        int(species['currentHighestRating'])]
        else:
            if species['TargetPath'] == "":
                species['TargetPath'] = self.getPathToSpecies(species['ScientificName'])
            # summarize the next level
            results = []
            for leave in species['Leaves']:
                if leave == "agaricomycetes":
                    x = 1
                tempresults = self.summarizeSpeciesCounters(leave, results, debugfile)
                i = 0
                for number in tempresults:
                    if len(results) > i:
                        # numeric to be added 
                        if i < 6:
                            results[i] += number
                        elif results[9] <= tempresults[9] and tempresults[6] != "":
                            for j in [6,7,8,9]:
                                results[j] =  tempresults[j]
                    else:           
                        results.append(number)
                    i += 1
            # save results for species summary level
            if results == []:
                print(f'hoe kan dit??')
            else:
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
        global SpeciesDebug
        species = self.species_dict[soort]
        if int(species['iPictureCollectionCount']) > 0:
            # if SpeciesDebug > 0:
            #     print(f"{species['Level']:16}: {species['iSpeciesCollectionCount']:6}  {species['iPictureCollectionCount']:6}  {species['iPicturesWithJPGsCollection']:6} \
            #         {species['ScientificName']:32} / {species['currentHighestRating']:3}: {species['BestJPG']:28} ({species['BestJPGPathName']})", file=debugfile)
            if species['BestJPG'] == "":
                print(f"""{species['Level']} {species['ScientificName']} ({species['EnglishName']}) has no identified JPGs to show from {species['iPicturesWithJPGsCollection']} JPGs.""")
            if species['currentHighestRating'] == '0':
                print(f"{species['Level']} {species['ScientificName']} ({species['EnglishName']}) has zero as text string.")
        for leave in species['Leaves']:
            self.printDataForSpecies(leave, debugfile)
        return
    
    def printSpeciesTree(self, scientificname, level, classification):
        species = self.species_dict[scientificname]
        if (int(classification) >= 4 and \
            ((species['Level'] != 'Level.SPECIES') or (species['Level'] != Level.SPECIES) \
            or (int(classification) >= 8))):
            print(f"{species['Level']:15}: {species['DutchName']:32}  {species['EnglishName']:32}  {species['ScientificName']}")
        for leaf in species['Leaves']:
            self.printSpeciesTree(leaf, level + 1, classification)
        return
    

# A FIRSTRUN will initially fill the biota data repository.  
# This will clear all counters, collect the data, and COMMIT this as new data
# to the repository.  The species topolopogy info is kept.
# the data is read from "species-initial.csv".
# "Collection" counters are set, the "ThisRun" counters are cleared.  
# The "species-frozen.csv" file is created.
#
# A REVIEW run will collect the data in a new directory, no commitment is done.
# The "species-frozen.csv" file is used as input.
# A "species-review.csv" is created, to make eg a website preview before data is 
# committed to the repository.  It is excepted that new runs will be needed, to
# make clean data and picture files.
#
# A COMMIT run will copy the files with known species information to the 
# repository, update the "Collection" counters with "ThisRun" counters and clean them.
# this assumes a "species-review.csv" file as input, and will create an
# "species-frozen.csv" file.
#
# copilot version
def readSpeciesInfo(config, speciesfilename, reset_all_counters):
    # Reads a CSV file line by line, splits by delimiter, and creates dictionaries.
    # Assumes the first row contains column headers.

    with io.open(speciesfilename, mode="r", newline="", encoding='utf-8') as csvfile:
        # Read the first line (header) to get column names
        header = csvfile.readline().strip().split(';')
        # columns that need special treatment
        numeric_columns = ['currentHighestRating', 'iSpeciesThisRunCount', 'iSpeciesCollectionCount',\
                            'iPictureThisRunCount', 'iPictureCollectionCount', 'iPicturesWithJPGsThisRun', 'iPicturesWithJPGsCollection']
        list_columns = ['Leaves', 'JPGmap']

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
                # fix numeric, int() for applicable numbers
                for col in numeric_columns:
                    try:
                        row_dict[col] = int(row_dict[col])  # Convert to integer
                    except ValueError:
                        pass  # Handle non-numeric values gracefully
            
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

            species_dict[row_dict['ScientificName']] = row_dict

    for speciesname in species_dict:
        species = species_dict[speciesname]
        ScientificName = species['ScientificName']
        UpName = species['Up']
        if UpName != "" and ScientificName not in species_dict[UpName]['Leaves']:
            species_dict[UpName]['Leaves'].append(ScientificName)
        
        # add Nederlandse naam index
        dutchname = species['DutchName']            
        if dutchname != "":
            if dutchname not in dutch_dict:
                dutch_dict[dutchname] = ScientificName

        # add Nederlandse naam index
        englishname = species['EnglishName']            
        if englishname != "":
            if englishname not in english_dict:
                english_dict[englishname] = ScientificName
    
    print(f'Read {len(species_dict)} predefined species from {speciesfilename}.')
    
    return

def writeSpeciesInfo(config, speciesfilename, commit_counters):
    # Write Picture CSV file
    print(f'Write all species data CSV file "{speciesfilename}".')
    with open(speciesfilename, "w", newline="", encoding='utf-8') as csvfile:
        # needed to get column in order
        speciesheaders = OrderedDict((field, None) for field in SPECIES_FIELDS)                 
        writer = csv.DictWriter(csvfile, fieldnames=speciesheaders, quoting=csv.QUOTE_MINIMAL, delimiter=';')
        writer.writeheader()
        for species in species_dict:
            spec_dict = species_dict[species]
            row = {}
            row["Level"] = spec_dict['Level'] 
            row["ScientificName"] = spec_dict['ScientificName']
            row["DutchName"] = spec_dict['DutchName']
            row["EnglishName"] = spec_dict['EnglishName']
            row["Up"] = spec_dict['Up']
            row["Leaves"] = spec_dict['Leaves']
            row["JPGmap"] = spec_dict['JPGmap'] # {}
            row["currentHighestRating"] = spec_dict["currentHighestRating"]
            row["BestJPG"] = spec_dict['BestJPG']
            row["BestUPN"] = spec_dict['BestUPN']  
            row["BestJPGPathName"] = spec_dict['BestJPGPathName']
            row["TargetPath"] = spec_dict['TargetPath']
            
            if commit_counters:
                # keep unchanged
                row["iSpeciesCollectionCount"] = \
                    int(spec_dict["iSpeciesCollectionCount"]) + int(spec_dict['iSpeciesThisRunCount'])     
                row["iPictureCollectionCount"] = \
                    int(spec_dict["iPictureCollectionCount"]) + int(spec_dict["iPictureThisRunCount"])
                row["iPicturesWithJPGsCollection"] = \
                    int(spec_dict["iPicturesWithJPGsCollection"]) + int(spec_dict["iPicturesWithJPGsThisRun"])
            else:    
                # keep unchanged
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


# logic from C++ version
def computeRating(upn):
    computation = 0
    DxOUsed = False
    # file type logic
    for filename in upn['Files']:
        file = files_dict[filename]
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

    # save new Rating for Picture
    bestresult = 0
    for filename in upn['Files']:
        rating = files_dict[filename]['XMP:Rating']
        if rating != "":
            bestresult = max(bestresult, int(rating))
    upn['Rating'] = bestresult

    # Penalties for very small pics
    saved_multiplier = 0.1
    multiplier = 1
    for filename in upn['Files']:
        file = files_dict[filename]
        if file['WindowsExtension'] in ['jpg', 'jpeg'] and file['ImageSize'] != "":
            width_jpg, heigth_jpg = map(int, file['ImageSize'].split("x"))
            multiplier = float(min(1500, max(width_jpg, heigth_jpg))) / 1500.0
            if upn['ImageSize'] != "":  # should be true
                width_camera, heigth_camera = map(int, upn['ImageSize'].split("x"))
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
def computeRatingFile(file):
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
        width_jpg, heigth_jpg = map(int, file['ImageSize'].split("x"))
        multiplier = float(min(1500, max(width_jpg, heigth_jpg))) / 1500.0
    
    saved_multiplier = max(saved_multiplier, multiplier)

    computation = int(float(computation)*saved_multiplier)             
    if "dxo" in file['File:FileName'].lower():
        computation += 8
    
    return int(computation)
