import os
import shutil
import time
import tempfile
import math
from datetime import datetime
from operator import itemgetter
from collections import defaultdict
import logging
from pathlib import Path
from PIL import Image

global config

from ghexif import COLUMN_NAMES, SELECTED_FIELDS
from ghexif.specieslib import Level
import ghexif.projectlib
import ghexif.breadcrumb

logger = logging.getLogger(__name__)

ClassificationGroupName = ["Species","Family", "Order", "Class", "Phylum", "Kingdom", "Biota"]
ClassificationGroupNameNL = ["Soort", "Familie", "Orde", "Klasse","Stam", "Rijk", "Biota"]
ClassificationGroupLidwoord = ["de", "de", "de", "de","de", "het", "de"]
ClassificationGroupNameNLmeervoud = ["Soorten", "Families", "Orden", "Klassen","Stammen", "Rijken", "Biota"]
ClassificationGroupNameENmeervoud = ["Species","Families", "Orders", "Classes", "Phyla", "Kingdoms", "Biota"]

# web support 
def getTemplateOutputString(MyProject, species, file, picture, TemplateLine):
# 	Parse line in pieces between tokens (%%n%%) with n an integer that refers to a species-related blurp.
# 	The syntax implies that text and token %% seperators alternate, which we keep track off with TextOrToken 
# 	probably want nested call for subroutines, ie make this an output string.
# 	TemplateOutputString = ""
    try:
        outputLine = ""
        tokens = TemplateLine.split('%%')
        if len(tokens) == 1:
            # no tokens, return line as-is
            return TemplateLine
        else:
            # text on even positions (0, 2, 4, ..) tokens on odd position
            OddPosition = 0
            for token in tokens:
                if not OddPosition:         # not a token
                    outputLine += token
                else:                       # handle token
                    try:
                        outputLine += formatToken(int(token), MyProject, species, file, picture)       # '<token ' + token + '>'
                    except:
                        outputLine += '<Failed to handle token ' + token + '>'
                        if species == None:
                            print(f"Blog index:{'<Failed to handle token ' + token + '>'}")
                        else:
                            print(f"{species['ScientificName']}:{'<Failed to handle token ' + token + '>'}")
                OddPosition = 1 - OddPosition
            return outputLine
    except Exception as e:
        logger.warning(f"{TemplateLine} failed: {e}")
        outputLine = TemplateLine
    return tokens

def getFormattedCityStateCountry(picture):
	str = picture['City']
	if picture['City'] and picture['State'] == "" and picture['Country'] == "":
		return str
	if picture['State'] != "" or picture['Country'] != "":
		str = str + ", " + picture['State']
	if picture['State'] != "" and picture['Country'] != "":
		str = str + ", " + picture['Country']
	return str # "No GPS or location available"

def isUnnamedRoad(locationname):
    if locationname in ['Vía Sin Nombre', 'Route sans nom', 'Unnamed Road']:   # , 'Onbekende weg' ?
        return True
    return False

def getFormattedLocation(picture):
    if picture['Location'] == "" or isUnnamedRoad(picture['Location']):
        return getFormattedCityStateCountry(picture)
    else:
        if picture['Location'] != "" and len(picture['Location']) > 15:
            return picture['Location']
        if picture['Country'] == "": return picture['Location'];
        if picture['Location'] == picture['City'] or picture['Location'] == picture['Country'] or isUnnamedRoad(picture['Location']): 
            return getFormattedCityStateCountry(picture)
        if picture['City'] == '': 
            picture['City'] = picture['Location']
            return getFormattedCityStateCountry(picture)
        return picture['Location'] + " (" + getFormattedCityStateCountry(picture) + ")"

def makeLinkScienticficName(MyProject, BiotaWebRoot, classification, workstr):
    if classification['iSpeciesCollectionCount'] == 0:
        workstr +=  classification['ScientificName'].strip('0123456789 ').capitalize()
    else:
        workstr +=  '<a href="' + BiotaWebRoot + classification["TargetPath"] + classification["ScientificName"] + '-' + MyProject.getLanguage() +  '.html\"' + '>\n'
        workstr += '			         ' + classification['ScientificName'].strip('0123456789 ').capitalize() + '</a>'
    return workstr

# new csv files will have sexe
def formatSexe(upn):
    try:
        if upn['malefemale'] == 'male' or upn['malefemale'] == 'man':
            return ' ♂'
        elif upn['malefemale'] == 'female' or upn['malefemale'] == 'vrouw':
            return ' ♀'
        elif upn['malefemale'] == 'couple' or upn['malefemale'] == 'paar' or upn['malefemale'] == 'paartje':
            return ' ♂♀'
    except:
        pass
    return ''

def formatAgePlumage(upn):
    workstr = ''
    try:
        if upn['ageintext'] != '' or upn['summerwinter'] != '':
            workstr = ' ('
            if upn['ageintext'] != '': 
                workstr += upn['ageintext']
                if upn['summerwinter'] != '':   # unlikely combination
                    workstr += ', ' + upn['summerwinter']
            elif upn['summerwinter'] != '':
                workstr += upn['summerwinter']
            workstr += ')'
    except:
        pass 
    
    return workstr

# try different formats
def tryJPGtime(rawtime, MyProject):
    try:
        if rawtime[2:3] == ':':                                      # Likely 12:02:2016 15:00:00
            if len(rawtime)>12 and rawtime[13:14] == ":":            #        0123456789+123456789  
                JPGtime = datetime.strptime(rawtime, "%d:%m:%Y %H:%M:%S")
            else:
                JPGtime = datetime.strptime(rawtime.strip(), "%d:%m:%Y")
                JPGtime = JPGtime.date()
        elif rawtime[4:5] == ':':                                    # Likely 2024:12:25 15:00:00
            if len(rawtime)>12 and rawtime[13:14] == ":":            #        0123456789+123456789  
                JPGtime = datetime.strptime(rawtime, "%Y:%m:%d %H:%M:%S")
            else:
                JPGtime = datetime.strptime(rawtime.strip(), "%Y:%m:%d")
                JPGtime = JPGtime.date()                
    except Exception as e:
        logger.info(f"Conversion date {rawtime} failed, error: {e}")
        JPGtime = ''
        pass
    return JPGtime

# try different sources
def getJPGtime(JPGobject, MyProject):
    JPGtime = tryJPGtime(JPGobject['EXIF:DateTimeOriginal'], MyProject)
    if JPGtime == "":
        JPGtime = tryJPGtime(MyProject.UniquePictureName_dict[JPGobject['UniquePictureName']]['DateTimeOriginal'], MyProject)
    return JPGtime

# make sure we do not do small twice
def getSmallJPG(filename):
    if filename.lower().endswith('-small.jpg'):
        return filename
    else:
        return filename.replace('.jpg','-small.jpg')

def jpgResizeCopy(targetstr, horizontalsize = 600):
    # resize, avoid rounding errors that are case by 499 vs 500
    try:
        if targetstr.lower().endswith('-small.jpg'): return                 # only once needed
        image = Image.open(targetstr)
        if image.size[0] > image.size[1]:
            resized_image = image.resize((600, 450))
        elif image.size[0] == image.size[1]:
            resized_image = image.resize((600, 600))            
        else:
            resized_image = image.resize((450, 600))
        # wpercent = (horizontalsize/float(image.size[0]))
        # hsize = int((float(image.size[1])*float(wpercent)))
        # resized_image = image.resize((horizontalsize, hsize))
        resized_image.save(targetstr.replace('.jpg','-small.jpg'))
    except Exception as e:
        logger.info(f"Resizing {targetstr} failed, error: {e}")
    return

def getAltTextForJPG(MyProject, jpgname):
    try:
        jpgobj = MyProject.files_dict[jpgname]
        upn = MyProject.UniquePictureName_dict[jpgobj['UniquePictureName']]
        names = upn['WetSoortNaam'].replace(' ', '&nbsp;').replace('-', '&#8209;').capitalize() + ' / ' +  \
                upn['EngSoortNaam'].replace(' ', '&nbsp;').replace('-', '&#8209;').capitalize() + ' / ' + \
                upn['NLSoortNaam'].replace(' ', '&nbsp;').replace('-', '&#8209;').capitalize()
    except:
        names = ""
    return names

def getCameraSettings(MyProject, picture):
    if picture:
        language = MyProject.getLanguage()  
        if language == "en": metorwith = " with "
        else: metorwith = " met "
        workstr = ""
        if picture['CameraModel'] != "":
            workstr += picture['CameraModel']
            if picture['LensShortName'] != "":
                workstr += metorwith + picture['LensShortName']
            elif picture['LensLongName'] != "":
                workstr += metorwith + picture['LensLongName']
        elif picture['JPGSize'] in ['3024x4032','4032x3024']:
            workstr += 'Apple iPhone'        
        if picture['Settings'] != ' sec at F, ISO , distance ':
            if workstr != "": workstr += ", "
            if language == "en":
                workstr += picture['Settings']
            else:
                workstr += picture['Settings'].replace('at ',' bij ').replace('distance','afstand')
        return workstr
    else: 
        return ""  

def formatToken(tokenno, MyProject, species_param, file, picture):
    global config
    BiotaWebRoot = MyProject.getBiotaWebRoot()

    species_local = species_param      	# default species avoids crashes
    family_local = {}
    order_local = {}
    class_local = {}
    phylum_local = {}
    kingdom_local = {}
    # default output
    workstr = ""
    if species_param != None and species_param != '':
        if species_param['Level'] == Level.KINGDOM:
            kingdom_local = species_param
            species_local = ""
        elif species_param['Level'] == Level.PHYLUM:
            phylum_local = species_param
            kingdom_local = MyProject.getSpecies(phylum_local['Up']) 
        elif species_param['Level'] == Level.CLASS:
            class_local = species_param
            phylum_local = MyProject.getSpecies(class_local['Up'])          
            kingdom_local = MyProject.getSpecies(phylum_local['Up'])  
        elif species_param['Level'] == Level.ORDER:
            order_local = species_param
            class_local = MyProject.getSpecies(order_local['Up'])                  
            phylum_local = MyProject.getSpecies(class_local['Up'])          
            kingdom_local = MyProject.getSpecies(phylum_local['Up'])  
        elif species_param['Level'] == Level.FAMILY:
            family_local = species_param
            if MyProject.getSpecies(family_local['Up'])['Level'] == Level.CLASS:
                class_local = MyProject.getSpecies(family_local['Up'])
            else:
                order_local = MyProject.getSpecies(family_local['Up'])                          
                class_local = MyProject.getSpecies(order_local['Up'])    
            phylum_local = MyProject.getSpecies(class_local['Up'])          
            kingdom_local = MyProject.getSpecies(phylum_local['Up'])  
        elif species_param['Level'] == Level.SPECIES:
            # species_local = species_param                         # Already defaulted
            if species_local['Up'] != "":                           # Linked, assume all levels are ok
                family_local = MyProject.getSpecies(species_local['Up']) 
                if MyProject.getSpecies(family_local['Up'])['Level'] == Level.CLASS:
                    class_local = MyProject.getSpecies(family_local['Up'])
                else:
                    order_local = MyProject.getSpecies(family_local['Up'])                          
                    class_local = MyProject.getSpecies(order_local['Up'])                  
                phylum_local = MyProject.getSpecies(class_local['Up'])          
                kingdom_local = MyProject.getSpecies(phylum_local['Up'])  
    try:
        # orders
        if tokenno == 1:   
            return order_local['DutchName'].capitalize() if order_local else ""
        elif tokenno == 2: return order_local['ScientificName'].strip('0123456789 ').capitalize() if order_local else ""
        elif tokenno == 3: 
            return order_local['EnglishName'].capitalize() if order_local else ""
        elif tokenno == 4: return order_local['iPictureCollectionCount'] if order_local else ""
        elif tokenno == 7:     # lijst met ordes (Nederlands of Engels)
            language = MyProject.getLanguage()
            if language == 'nl': languageName = 'DutchName'
            else: languageName = 'EnglishName'                
            if species_param['Level'] == Level.SPECIES and len(class_local)>0:
                workstr = "<li class=""\"first""\">\n"
                for leaf in class_local['Leaves']:
                    if MyProject.getSpeciesCount(leaf):
                        workstr += '			<li><a href="' + BiotaWebRoot + \
                            MyProject.getSpecies(leaf)['TargetPath'] + \
                            MyProject.getSpecies(leaf)['ScientificName'] + '-' + language + '.html">' \
                            + MyProject.getSpecies(leaf)[languageName].capitalize() + "</a></li>\n"
                return workstr
            else:
                return ""
        # family
        elif tokenno == 11: return family_local['DutchName'].capitalize() if family_local else ""
        elif tokenno == 12: return family_local['ScientificName'].strip('0123456789 ').capitalize() if family_local else ""
        elif tokenno == 13: return family_local['EnglishName'].capitalize() if family_local else ""
        elif tokenno == 14: return family_local['iPictureCollectionCount'] if family_local else ""
        elif tokenno == 18:     # lijst met ordes (Nederlands)
            language = MyProject.getLanguage()
            if language == 'nl': languageName = 'DutchName'
            else: languageName = 'EnglishName'   
            if species_param['Level'] == Level.SPECIES and len(family_local)>0:
                workstr = "<li class=""\"first""\">\n"
                for leaf in family_local['Leaves']:
                    if MyProject.getSpeciesCount(leaf):
                        workstr += "			<li><a href=""\"" + BiotaWebRoot + \
                            MyProject.getSpecies(leaf)['TargetPath'] + \
                            MyProject.getSpecies(leaf)['ScientificName']  + '-' + language + '.html">' \
                            + MyProject.getSpecies(leaf)[languageName].capitalize() + "</a></li>\n"
                return workstr
            else:
                return ""
        # species
        elif tokenno == 21: return species_local['DutchName'].capitalize() if species_local else ""
        elif tokenno == 22: return species_local['ScientificName'].strip('0123456789 ').capitalize() if species_local else ""
        elif tokenno == 23: return species_local['EnglishName'].capitalize() if species_local else ""
        elif tokenno == 24: return species_local['iPictureCollectionCount'] if species_local else ""
        elif tokenno == 25: # Nederlands (Engels) of Nederlands of Engels  of wetenschappelijk
            language = MyProject.getLanguage()
            if species_local['DutchName'] != "" and language == 'nl':
                return species_local['DutchName'].capitalize() + " (" + species_local['ScientificName'].strip('0123456789 ').capitalize() + ")"
            elif species_local['EnglishName'] != "" and language == 'en':
                return species_local['EnglishName'].capitalize() + " (" + species_local['ScientificName'].strip('0123456789 ').capitalize() + ")" 
            else:
                return species_local['ScientificName'].strip('0123456789 ').capitalize()
        elif tokenno == 30:
            # Convert time.time() to timestamp to a datetime object
            date = datetime.fromtimestamp(time.time())
            # Format the datetime object as "Wed Jan 24, 2024"
            return date.strftime("a %b %d, %Y")   
        elif tokenno == 31:
            #return species_param['DutchName'].capitalize() + ' ' + species_param['ScientificName'].strip('0123456789 ').capitalize()
            return species_param['ScientificName'].strip('0123456789 ').capitalize()
        elif tokenno == 32:                                 # one level lower (enkelvoud)
            return ClassificationGroupName[species_param['Level'].value-2].capitalize()
        elif tokenno == 33:                                 # one level lower (enkelvoud)
            if MyProject.getLanguage() == "nl":
                languageName = 'DutchName'
                groupname = ClassificationGroupNameNL[species_param['Level'].value-1]
            else: 
                languageName = 'EnglishName'
                groupname = ClassificationGroupName[species_param['Level'].value-1]
            if species_param[languageName] != "":
                return groupname + ': ' + species_param[languageName].capitalize()
            else:
                return groupname + ': ' + species_param['ScientificName'].strip('0123456789 ').capitalize()
        elif tokenno == 34:                                 # meervoud voor de children, dus 1 extra er af daarom -2                   
            return ClassificationGroupNameNLmeervoud[species_param['Level'].value-2].lower()
        elif tokenno == 35:
            workstr =  ClassificationGroupNameNL[species_param['Level'].value-1] if MyProject.getLanguage() == "nl" else ClassificationGroupNameNL[species_param['Level'].value-1] 
            workstr +=  ' ' + species_param['ScientificName'].strip('0123456789 ').capitalize()
            languageName = 'DutchName' if MyProject.getLanguage() == "nl" else 'EnglishName'
            if species_param[languageName] != "":
                workstr += ' (' + species_param['DutchName'].capitalize() + ')'
            if workstr == 'Biota Biota (Biota)':
                language = MyProject.getLanguage()          # intended language
                if language == 'nl':
                    workstr = 'alle leven (biota)'
                else:
                    workstr = workstr = 'all life (biota)'
            return workstr            
        elif tokenno == 36:
            return species_param['DutchName'].capitalize()
        elif tokenno == 37:
            return species_param['EnglishName'].capitalize() 
        elif tokenno == 38:                                 # meervoud voor de children, dus 1 extra er af daarom -2                   
            return ClassificationGroupNameENmeervoud[species_param['Level'].value-2]   
        elif tokenno == 39:                                 # one level lower (enkelvoud)
            return ClassificationGroupNameNL[species_param['Level'].value-2].lower()
        elif tokenno == 40:
            return getFormattedLocation(picture) if picture else ""
        elif tokenno == 41:
            if picture['Description'] != "":
                language = MyProject.getLanguage()          # intended language
                if picture['Description'][0:1] == "#":
                    picttext = picture['Description'][1:]
                else:
                    picttext = picture['Description']
                try:
                    workstr = MyProject.ct.getOneLanguage(picttext, language)
                except:
                    workstr = picttext
            else:
                workstr =  ""          
            return workstr
        elif tokenno == 42:
            return getCameraSettings(MyProject, picture)
        elif tokenno == 43:# 	case 43: // was 60 = totaal aantal soorten
            return MyProject.getSpeciesCount('biota')
        elif tokenno == 44:
            return BiotaWebRoot + picture['TargetPath'] + picture['BestJPG']
        elif tokenno == 45:
            return MyProject.getBlogDateFromPictures() + '/' + file['File:FileName']    #BUGBUG
        elif tokenno == 46:
            try:
                return getJPGtime(file, MyProject).strftime('%Y:%m:%d %H:%M:%S')
            except:
                return ""
        elif tokenno == 47:
            return MyProject.getBlogDateFromPictures() + '/' + getSmallJPG(file['File:FileName'])   #BUGBUG        
        elif tokenno == 48:
            workstr = BiotaWebRoot + "includes web/keerkringvogel.jpg"
            return workstr      
        elif tokenno == 49: # banner, now by kingdom, but could be more specific
            if not MyProject.isMasterRepository():
                try:
                    workstr = MyProject.Trip.getTripBanner()
                except:
                    workstr = BiotaWebRoot + "includes web/keerkringvogel.jpg"
            elif kingdom_local == {}: 
                workstr = BiotaWebRoot + "includes web/keerkringvogel.jpg"
            else:
                if kingdom_local['ScientificName'].lower() == "animalia":
                    workstr = BiotaWebRoot + "includes web/keerkringvogel.jpg"
                elif kingdom_local['ScientificName'].lower() == "fungi":
                    workstr = BiotaWebRoot + "includes web/paddenstoelen.jpg"
                else:
                    workstr = BiotaWebRoot + "includes web/planten.jpg"
            return workstr
        elif tokenno == 50: # vogels
            return MyProject.getSpeciesCount('aves')
        elif tokenno == 51: # insecten
            return MyProject.getSpeciesCount('insecta')
        elif tokenno == 52: # planten
            return MyProject.getSpeciesCount('plantae')
        elif tokenno == 53: # fungi
            return MyProject.getSpeciesCount('fungi')     
        elif tokenno == 54: # fungi
            return MyProject.getSpeciesCount('mammalia')            
        elif tokenno == 60:                                 # one level lower (enkelvoud)
            return ClassificationGroupLidwoord[species_param['Level'].value-2].lower()
        elif tokenno == 71: return class_local['DutchName'].capitalize() 									 
        elif tokenno == 72: return class_local['ScientificName'].strip('0123456789 ').capitalize()						
        elif tokenno == 73: return class_local['EnglishName'].capitalize()
        elif tokenno == 74: return class_local['iPictureCollectionCount'] 		

        elif tokenno == 76: return phylum_local['DutchName'].capitalize()								
        elif tokenno == 77: return phylum_local['ScientificName'].strip('0123456789 ').capitalize()								
        elif tokenno == 78: return phylum_local['EnglishName'].capitalize()
        elif tokenno == 79: return phylum_local['iPictureCollectionCount']

        elif tokenno == 81: return kingdom_local['DutchName'].capitalize()						
        elif tokenno == 82: return kingdom_local['ScientificName'].strip('0123456789 ').capitalize()
        elif tokenno == 83: return kingdom_local['EnglishName'].capitalize()
        elif tokenno == 84: return kingdom_local['iPictureCollectionCount']
        elif tokenno == 89:
            if MyProject.Trip != None:
                return MyProject.Trip.getMasterBiotaWebRoot()     # path to current main repository, NOT to trip also for subdirectories
            else:
                return BiotaWebRoot
        elif tokenno == 90:
            return BiotaWebRoot                     # path to current main repository or to trip
        elif tokenno == 91:
            language = MyProject.getLanguage()
            if MyProject.Trip != None:
                directory = MyProject.Trip.getMasterBiotaWebRoot()     # path to current main repository, NOT to trip also for subdirectories
            workstr = "	<h4>Links</h4>\n	<div class=""\"content\""">\n		<ul class= ""\"w3-ul w3-hoverable\""">\n"
            workstr += '			<li class=""\"first\"""><a href="'+ BiotaWebRoot +'biotablog/blog index-' + language +'.html">Recent</a></li>\n'
            if MyProject.Trip != None:
                workstr += '            <li><a href="' + directory + 'tripindex-' + language + '.html">Trips</a></li>'
                if language == 'nl':
                    soorten = 'Trip soorten'
                    biota = 'Trip Biota'
                else:
                    soorten = 'Trip species'
                    biota = 'Trip Biota'
            else:
                if language == 'nl':
                    soorten = 'Alle soorten'
                    biota = 'Biota: Leven'
                else:
                    soorten = 'All species'
                    biota = 'Biota: Life'                    
            if language == 'nl':
                workstr += '			<li><a href= "'+ BiotaWebRoot +'summaries/all biota species-nl.html">' + soorten + '</a></li>\n'
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + "biota/biota-nl.html" + "\""">" + biota + "</a></li>\n"
                languageName = 'DutchName'
            else:
                workstr += '			<li><a href= "'+ BiotaWebRoot +'summaries/all biota species-en.html">' + soorten + '</a></li>\n'
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + "biota/biota-en.html" + "\""">" + biota + "</a></li>\n"
                languageName = 'EnglishName'                
            if len(kingdom_local)>0: 
                rijk = 'Rijk: ' if language == 'nl' else 'Kingdom: '
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + kingdom_local['TargetPath'] + kingdom_local['ScientificName'] + '-' + language + '.html">' + rijk + \
                    (kingdom_local[languageName].capitalize() if kingdom_local[languageName] != "" else  kingdom_local['ScientificName'].capitalize()) + "</a></li>\n"
            if len(phylum_local)>0:
                stam = 'Stam: ' if language == 'nl' else 'Phylum: '
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + phylum_local['TargetPath'] + phylum_local['ScientificName']  + '-' + language + '.html">' + stam + \
                    (phylum_local[languageName].capitalize() if phylum_local[languageName] != "" else phylum_local['ScientificName'].capitalize()) + "</a></li>\n"
            if len(class_local)>0:
                klasse = 'Klasse: ' if language == 'nl' else 'Class: '                
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + class_local['TargetPath'] + class_local['ScientificName'] + '-' + language + '.html">' + klasse + \
                    (class_local[languageName].capitalize() if class_local[languageName] != "" else class_local['ScientificName'].capitalize()) + "</a></li>\n"
            if len(order_local)>0:
                orde = 'Orde: ' if language == 'nl' else 'Order: '                       
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + order_local['TargetPath'] + order_local['ScientificName'] + '-' + language + '.html">' + orde + \
                    (order_local[languageName].capitalize() if order_local[languageName] != "" else order_local['ScientificName'].capitalize()) + "</a></li>\n"
            if len(family_local)>0:
                fam = 'Familie: ' if language == 'nl' else 'Family: '                
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + family_local['TargetPath'] + family_local['ScientificName'] + '-' + language + '.html">' + fam + \
                    (family_local[languageName].capitalize() if family_local[languageName] != "" else family_local['ScientificName'].strip('0123456789 ').capitalize()) + "</a></li>\n"
            workstr += "		</ul>\n	</div>\n"
            return workstr
        elif tokenno == 93:         # Plaatjes en namen, zelfde voor beide talen
            language = MyProject.getLanguage()
            workstr = "<div id=""\"grid\""">\n"
            for speciesname in sorted(species_param['Leaves']):
                species = MyProject.getSpecies(speciesname)
                if (int(species['iPicturesWithJPGsCollection']) + int(species['iPicturesWithJPGsThisRun'])) > 0:
                    workstr += "			  <div>\n"
                    workstr += '			     <a href="' + BiotaWebRoot + species["TargetPath"] + species["ScientificName"] + '-' + language + '.html\"' + '>\n'
                    if species['BestJPG'] != "" and species['BestJPGPathName'] != "":
                        workstr += '			         <img src="' + \
                            BiotaWebRoot + species['BestJPGPathName'].replace("E:/Dropbox/my birds","/biota/animalia/chordata/aves") + species['BestJPG'] + '"' + " style=""\"width:100%\""">\n"
                    workstr += "			     </a>" 
                    workstr += '<p>' + species['ScientificName'].strip('0123456789 ').capitalize() + '</p>'
                    workstr += '<p>' + species['EnglishName'].capitalize() + '</p>'
                    workstr += '<p>' + species['DutchName'].capitalize() + '</p>\n'                    
                    workstr += "\n"
                    workstr += "              </div>\n"
            workstr += "            </div>\n"
            return workstr
        elif tokenno == 94:          # page for order
            workstr = BiotaWebRoot + species_param["TargetPath"] + species_param["ScientificName"]
            return workstr
        elif tokenno == 95:          # page for species
            workstr = BiotaWebRoot + species_param["TargetPath"] + species_param["ScientificName"]
            return workstr
        elif tokenno == 100:         # Nederlandse of Engelse plaatjes en namen
            workstr = addOneSpeciesPage(MyProject, species_param)              
            return workstr
        elif tokenno == 101:         # Male/Female etc
            try:
                language = MyProject.getLanguage()
                if language == 'nl':
                    if picture['malefemale'] != '' or picture['ageintext']:                    
                        workstr = "De foto toont een "
                        if picture['malefemale'] == 'male':
                            workstr += "mannetje. "
                        elif picture['malefemale'] == 'female':
                            workstr += "vrouwtje. "
                        elif picture['malefemale'] == 'couple':
                            workstr += 'paartje. '
                        elif picture['ageintext'] == 'juv.':
                            workstr += 'jonge ' + picture['NLSoortNaam'].lower() + '.'
                        elif picture['ageintext'] == 'imm.':
                            workstr += 'onvolwassen ' +  + picture['NLSoortNaam'].lower() + '.'                     
                    if picture['summerwinter'] == 'br.' and 'aves' in picture['TargetPath']:
                        workstr += ' De vogel is in broedkleed (zomerkleed).'                        
                    if picture['summerwinter'] == 'non-br.' and 'aves' in picture['TargetPath']:
                        workstr += ' De vogel is in winterkleed.'   
                elif language == 'en':
                    if picture['malefemale'] != '' or picture['ageintext']:
                        workstr = "The photo shows a "
                        if picture['malefemale'] == 'male':
                            workstr += "male. "
                        elif picture['malefemale'] == 'female':
                            workstr += "female. "
                        elif picture['malefemale'] == 'couple':
                            workstr += "couple."
                        elif picture['ageintext'] == 'juv.':
                            workstr += 'young '  + picture['EngSoortNaam'].lower() + '.'
                        elif picture['ageintext'] == 'imm.':
                            workstr += 'immature ' + picture['EngSoortNaam'].lower() + '.'                 
                    if picture['summerwinter'] == 'br.' and 'aves' in picture['TargetPath']:
                        workstr += ' The bird is in breeding (summer) plumage.'                        
                    if picture['summerwinter'] == 'non-br.' and 'aves' in picture['TargetPath']:
                        workstr += ' The vogel is in winter plumage.'   
            except:
                workstr = ""                    
            return workstr
        elif tokenno == 110 or tokenno == 113:         # blog achtige samenvatting
            language = 'nl' if tokenno == 113 else 'en'
            BestJPGs = MyProject.Blog.BestJPGs
            if len(BestJPGs) == 0:
                # step 1: collect selected species
                selected_dict = {}
                addSpeciesToSummaryPost(MyProject, species_param['ScientificName'], selected_dict)
                # step 2: sort by time taken picture
                time_sorted_dict = dict(sorted(selected_dict.items(), key=itemgetter(1)))            
                # step 3: generate the webpage
                workstr = ""
                for file in time_sorted_dict:
                    workstr = addOneSpeciesJPG(MyProject, file, workstr, "oneblogphoto-" + language + ".html")
                    MyProject.Blog.CountJPG += 1
                # return HTML to caller to put in webpage
            else:
                for file in BestJPGs:
                    workstr = addOneSpeciesJPG(MyProject, file, workstr, "oneblogphoto-" + language + ".html")
                    MyProject.Blog.CountJPG += 1             
            return workstr
        elif tokenno == 111:         # Nederlandse tekst
            # mogelijke tekst
            suppressnames = False
            if picture['Description'] != "":
                if picture['Description'][0:1] == "#":
                    suppressnames = True
            workstr =  ""             
            if kingdom_local == {}:
                workstr += "De foto toont de omgeving." 
            else:
                # species and relationship
                if not (species_local['ScientificName'][0:5] == "homo " or suppressnames):                
                    workstr += "De foto toont een "
                    if species_local['DutchName'] != "":
                        workstr += species_local['DutchName'].capitalize() + " (<i>" + species_local['ScientificName'].strip('0123456789 ').capitalize() + "</i>). "
                    else:
                        workstr += "<i>" + species_local['ScientificName'].strip('0123456789 ').capitalize() + "</i>. "
                    workstr += "Het is een "
                    if kingdom_local['ScientificName'] == "animalia":
                        if class_local['ScientificName'] == "aves":
                            workstr += "vogel"
                        elif class_local['ScientificName'] == "insecta":
                            workstr += "insect" 
                        else:                
                            workstr += "dier" 
                    elif kingdom_local['ScientificName'] == "fungi":
                        workstr += "schimmel"
                    else:
                        workstr += "plant"
                    if order_local == {}:
                        workstr += " uit de klasse van de <i>"
                        workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, class_local, workstr)
                    else:
                        workstr += " uit de orde van de <i>"
                        workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, order_local, workstr)
                    workstr += "</i> en de familie <i>"
                    workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, family_local, workstr)
                    workstr +=  "</i>."
                # location                    
                try:
                    parsed_date = getJPGtime(file, MyProject)
                    workstr += " De foto is genomen"
                    if getFormattedLocation(picture) != "":
                        workstr += " bij " + getFormattedLocation(picture)
                    if parsed_date.strftime('%H:%M') not in ["","00:00"]:
                        workstr += " om " + parsed_date.strftime('%H:%M') 
                    if parsed_date.strftime('%d %b, %Y') != "":                        
                        workstr +=  " op " + parsed_date.strftime('%d %b, %Y')  + "."
                except:
                    logger.info(f'Location or date error for {file["File:FileName"]}: Location  {file["EXIF:DateTimeOriginal"]} and  {file["IPTC:Keywords"]}')
                    pass
            return workstr        
        elif tokenno == 112:         # Engelse tekst
            suppressnames = False
            if picture['Description'] != "":
                if picture['Description'][0:1] == "#":
                    suppressnames = True
            workstr =  ""   
            if kingdom_local == {}:
                workstr = "The photo shows the environment." 
            else:
                # species and relationship
                if not (species_local['ScientificName'][0:5] == "homo " or suppressnames):     
                    workstr = "The photo shows a "
                    if species_local['EnglishName'] != "":
                        workstr += species_local['EnglishName'].capitalize() + \
                            " (<i>" + species_local['ScientificName'].strip('0123456789 ').capitalize() + "</i>). "
                    else:
                        workstr += "<i>" + species_local['ScientificName'].strip('0123456789 ').capitalize() + "</i>. "
                    workstr += "It is a "
                    if kingdom_local['ScientificName'] == "animalia":
                        if class_local['ScientificName'] == "aves":
                            workstr += "bird"
                        elif class_local['ScientificName'] == "insecta":
                            workstr += "insect" 
                        else:                
                            workstr += "animal" 
                    elif kingdom_local['ScientificName'] == "fungi":
                        workstr += "fungus"
                    else:
                        workstr += "plant"
                    if order_local == {}:
                        workstr += " in the class <i>"
                        workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, class_local, workstr)
                    else:
                        workstr += " in the order <i>"
                        workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, order_local, workstr)
                    workstr += "</i> and the family <i>"
                    workstr = makeLinkScienticficName(MyProject, BiotaWebRoot, family_local, workstr)
                    workstr +=  "</i>."
                # location
                try:
                    parsed_date = getJPGtime(file, MyProject) 
                    workstr += " The photo was taken"
                    if getFormattedLocation(picture) != "":
                        workstr += " at " + getFormattedLocation(picture)
                    if parsed_date.strftime('%H:%M') not in ["","00:00"]:
                        workstr += " at " + parsed_date.strftime('%H:%M') 
                    if parsed_date.strftime('%A %B %d, %Y') != "":
                        workstr += " on " + parsed_date.strftime('%A %B %d, %Y')  + "."
                except:
                    pass
            return workstr    
        elif tokenno == 114:
            return MyProject.getBiotaBlogLanguageSwitch().lower()
        elif tokenno == 115:
            return MyProject.Blog.getBlogDay()
        elif tokenno == 116:
            return MyProject.Blog.getBlogTitel()
        elif tokenno == 117:
            return MyProject.Blog.getBlogTitle()
        elif tokenno == 118:
            return MyProject.Blog.getBlogBericht()
        elif tokenno == 119:
            return MyProject.Blog.getBlogMessage()                       
        elif tokenno == 120:         # all soorten
            workstr = ""
            workstr = addAllSpeciesWrapper(MyProject, species_param['ScientificName'], workstr)
            return workstr
        elif tokenno == 121 or tokenno == 122:
            language = 'nl' if tokenno == 121 else 'en'
            workstr = ''
            for logname in MyProject.Blog.getBlogDictSortedKeys():
                blog = MyProject.Blog.getBlog(logname)
                # print(f'Logname {logname} {blog["BestJPGs"][0]}')
                workstr += '   <div class="w3-row" style="margin: 15px">\n'
                workstr += '      <div class="w3-col w3-win8-olive" style="width:30%">\n'
                if blog["BestJPGs"] == []:
                    workstr += '          <p>(picture not found)</p>\n'
                elif blog["SelectedGroup"] == '':
                    # this is a reference to a blog index, not to a day/project
                    targetlink = os.path.join(Path(blog['BestJPGs']).parent,'biotablog','blog index-'+language+'.html')
                    workstr += f'          <a href="' + targetlink + '"><img\n'
                    workstr += '            src="' + blog['BestJPGs'] + '"\n'
                    workstr += '            class="w3-border" style="padding:8px;max-width:100%" title="Click to go to ' + blog["title_" + language] + '"></a>\n'                    
                else:
                    # pict the best picture from the current pictures in the blog
                    if blog['blogjpg'] == "":
                        bestjpg = str(MyProject.Blog.getBestJPGinList(logname))
                    else:  # override for preferred picture on index
                        bestjpg = blog['blogjpg']
                    altforjpg = getAltTextForJPG(MyProject, bestjpg)
                    workstr += f'          <a href="{logname}-slider-' + language + '.html"><img\n'
                    workstr += '            src="' + logname + '/' +  bestjpg + '" alt="' + altforjpg + '"\n'
                    workstr += '            class="w3-border" style="padding:8px;max-width:100%" title="Click to open ' + blog["title_" + language] + '"></a>\n'
                workstr += '      </div>\n'
                workstr += '      <div class="w3-col" style="width:70%">\n'
                workstr += '        <div class="content" style="margin-left:10px">\n'
                workstr += '          <table class="w3-table w3-hoverable">\n'
                workstr += '            <tr class="w3-win8-olive">\n'
                if blog["SelectedGroup"] == '':
                    workstr += f'              <th>Titel</th><td><a href="' + targetlink + '">' + blog["title_" + language] + '</td>\n'
                else:
                    workstr += f'              <th>Titel</th><td><a href="{logname}-'+language+'.html">' + blog["title_" + language] + '</td>\n'
                workstr += '            </tr>\n'
                workstr += '            <tr>\n'
                workstr += '              <th></th><td>' + blog["message_" + language] + '</td>\n'
                workstr += '            </tr>\n'
                if blog["SelectedGroup"] != 'biota' and blog["SelectedGroup"] != "":
                    workstr += '            <tr>\n'
                    workstr += '              <th>Soorten</th><td>' + blog["SelectedGroup"].capitalize() + '</td>\n'
                    workstr += '            </tr>\n'
                workstr += '            <tr>\n'
                if blog["SelectedGroup"] != "":
                    if language == 'nl':
                        workstr += '              <th>Datum:</th><td>' + blog["blog"][14:24] + '</td>\n'
                    else:
                        workstr += '              <th>Date:</th><td>' + blog["blog"][14:24] + '</td>\n'
                workstr += '            </tr>\n'                  
                workstr += '          </table>\n'
                workstr += '        </div>\n'
                workstr += '      </div>\n'
                workstr += '    </div>\n'
            return workstr
        elif tokenno == 125:
            #  languageName = 'DutchName'           else: languageName = 'EnglishName'   
            if MyProject.getLanguage() == 'nl' and species_local['DutchName'] != "" :
                workstr = species_local['DutchName'].capitalize() + formatSexe(picture) + formatAgePlumage(picture) + \
                    " <br><i><small>" + species_local['EnglishName'].capitalize() + "</small></i></br>" + \
                    "<i><small>" + species_local['ScientificName'].strip('0123456789 ').capitalize() + "</small></i></br>"
            else:
                if species_local['EnglishName'] != "":
                    workstr = species_local['EnglishName'].capitalize() + formatSexe(picture) + formatAgePlumage(picture) + \
                    " <br><i><small>" + species_local['ScientificName'].strip('0123456789 ').capitalize() + "</small></i></br>"
                else:
                    workstr = "<i>" + species_local['ScientificName'].capitalize() + "</i>"
            return workstr
        elif tokenno == 126:
            w =  MyProject.Blog.getColumnWidth()
            return str(w)
        # elif tokenno == 141:
        #     return MyProject.Blog.getBlogDay()
        elif tokenno == 142:
            return MyProject.Trip.getTripTitel()
        elif tokenno == 143:
            return MyProject.Trip.getTripTitle()
        elif tokenno == 144:
            return MyProject.Trip.getTripBericht()
        elif tokenno == 145:
            return MyProject.Trip.getTripMessage()  
        elif tokenno == 146 or tokenno == 147:
            if tokenno == 146:
                language = 'nl'
                soorten = 'Trip soorten'
                title = 'Titel'
            else:
                language = 'en'
                soorten = 'Trip species'
                title = 'Title'                
            workstr = ''
            # create copied versiuon
            sorted_items = sorted(MyProject.Trip.trip_dict.items(), key=lambda x: datetime.strptime(x[1]['todate'], '%Y-%m-%d'), reverse=True)

            for tripname in sorted_items:  # MyProject.Trip.getTripDictSortedKeys()
                trip = MyProject.Trip.getTrip(tripname[0])
                TripWebRoot = os.path.join(Path(BiotaWebRoot).parent, trip['trip_name'])
                # print(f'Logname {logname} {blog["BestJPGs"][0]}')
                workstr += '   <div class="w3-row" style="margin: 15px">\n'
                workstr += '      <div class="w3-col w3-win8-olive" style="width:25%">\n'
                if trip['trip_name'] == "":
                    workstr += '          <p>(picture not found)</p>\n'
                else:
                    workstr += f'          <a href="'  + TripWebRoot + '/biotablog/blog index-' + language + '.html"><img\n'
                    workstr += '            src="' + TripWebRoot + '/' + trip["trip_picture"]  + '"\n'  
                    workstr += '            class="w3-border" style="padding:8px;max-width:100%" title="Click to open "' + trip["title_" + language] + '""></a>\n'
                workstr += '      </div>\n'
                workstr += '      <div class="w3-col" style="width:75%">\n'
                workstr += '        <div class="content" style="margin-left:10px">\n'
                workstr += '          <table class="w3-table w3-hoverable">\n'
                workstr += '            <tr class="w3-win8-olive">\n'
                workstr += f'              <th>' + title + '</th><td><a href="' + TripWebRoot + '/biotablog/blog index-' + language + '.html">' + trip["title_" + language] + '</a> '
                workstr += f'<a href="' + TripWebRoot + '/biota/biota-' + language + '.html">(' + soorten + ')</a></td>\n'
                workstr += '            </tr>\n'
                workstr += '            <tr>\n'
                workstr += '              <th></th><td>' + trip["message_" + language] + '</td>\n'
                workstr += '            </tr>\n'
                workstr += '            <tr>\n'                
                if language == 'nl':
                    workstr += '          <th>Periode:</th><td>' + trip["fromdate"] + ' tot ' + trip["todate"] + '</td>\n'
                else:
                    workstr += '          <th>Period:</th><td>' + trip["fromdate"] + ' to ' + trip["todate"] + '</td>\n'
                workstr += '            </tr>\n'                  
                workstr += '          </table>\n'
                workstr += '        </div>\n'
                workstr += '      </div>\n'
                workstr += '    </div>\n'
            return workstr
        elif tokenno == 148 or tokenno == 149:            # banner link
            language = 'nl' if tokenno == 148 else 'en'
            if MyProject.Trip != None:
                directory = MyProject.Trip.getMasterBiotaWebRoot()     # path to current main repository, NOT to trip also for subdirectories   
            else:
                directory = BiotaWebRoot
            workstr = '<li><a href="' + directory + 'tripindex-' + language + '.html">Trips</a></li>'         
            return workstr
        elif tokenno == 150 or tokenno == 151:
            language = 'nl' if tokenno == 150 else 'en'
            if MyProject.Trip != None:
                tn = MyProject.Trip.getTripName()
                if tn in MyProject.Trip.trip_dict:
                    workstr = MyProject.Trip.getTrip(tn)['title_' + language]
                else:
                    workstr = tn.capitalize()
            return workstr
        # 160-162: functions for slider pages
        elif tokenno == 160:
            pictures = len(MyProject.Blog.BestJPGs)
            count = 1
            workstr = ""
            for jpgs in MyProject.Blog.BestJPGs:
                workstr += '        <div class="mySlides w3-win8-olive">\n'
                workstr += '            <div class="numbertext">' + str(count) + ' / ' + str(pictures) + '</div>\n'
                workstr += '            <img src="' + MyProject.Blog.blogday + '/' + jpgs  + '" style="padding:8px;width:100%">\n'
                workstr += '        </div>\n'          
                count += 1  
            return workstr
        elif tokenno == 161:
            pictures = len(MyProject.Blog.BestJPGs)
            count = 1
            workstr = ""            
            for jpgname in MyProject.Blog.BestJPGs:
                picture = MyProject.UniquePictureName_dict[MyProject.files_dict[jpgname]['UniquePictureName']]
                workstr += '        <div class="column">\n'
                workstr += '            <img class="demo cursor" src="' + MyProject.Blog.blogday + '/' + jpgname.replace('.jpg', '-small.jpg')  + '" style="width:100%"\n'
                workstr += '                 onclick="currentSlide(' + str(count) + ')" alt="<h3>' + getAltTextForJPG(MyProject, jpgname) +  '</h3>' + getCameraSettings(MyProject, picture) + '">\n'
                workstr += '        </div>\n'
                count += 1                  
            return workstr
        elif tokenno == 162:
            pictures = len(MyProject.Blog.BestJPGs)
            if pictures == 0:
                return ""
            else:
                if pictures < 8:
                    return str(round((1/pictures)*100, 1))
                else:
                    # groups = (pictures + 7) // 8 .. other numbers than 8 give (rounding?) errors
                    groupsize = 8
                    return str(round((1/groupsize)*100, 1))
        elif tokenno == 163:    # link to blog slider
            return MyProject.getBlogDateFromPictures() + '-slider-' + MyProject.getLanguage() + '.html?slide=' + str(MyProject.Blog.CountJPG)
        elif tokenno == 164:    # All / Trip spacies
            language = MyProject.getLanguage()
            if MyProject.Trip == None:
                directory = MyProject.Trip.getMasterBiotaWebRoot()     # path to current main repository, NOT to trip also for subdirectories 
                if language == 'nl': 
                    tripormaster = 'Alle Soorten'  
                else: tripormaster = 'All Species'  
            else:
                directory = BiotaWebRoot
                if language == 'nl': 
                    tripormaster = 'Trip Soorten'  
                else: tripormaster = 'Trip Species'                  
                
            workstr = '<li><a href="' + directory + 'summaries/all biota species-' + language + '.html">' + tripormaster + '</a></li>'         
            return workstr        
        elif tokenno == 170:
            workstr = MyProject.breadcrumb.makeCrumb('test')
            return workstr
        else:
            return '<token ' + tokenno + '>'
    except Exception as e:
        logger.info(f"{'<Failed to handle token ' + str(tokenno) + '>'} Error: {e.args[0]}")
        # {species_param['ScientificName']}:
        return '<Failed to handle token '  + str(tokenno) + '>' + e.arg[0]

# logic selected species
def writeOneSpecies(MyProject, leaf, temptextfile):
    species = MyProject.sp.species_dict[leaf]
    if species["TargetPath"] == "":
        logger.warning(f"{species['ScientificName']} has no TargetPath, review naming for parents.")
        return
    #print(f"working {species['ScientificName']} {species['EnglishName']}")
    language = MyProject.getLanguage()
    BiotaWebRoot = MyProject.getBiotaWebRoot()
    try:
        print('                <div>\n			        <a href="' + \
            BiotaWebRoot + species["TargetPath"] + species["ScientificName"] +  '-' + language + '.html' + '">\n', file=temptextfile)
        if species['BestJPG'] != "" and species['BestJPGPathName'] != "":
            print('			            <img src="' + BiotaWebRoot + \
            species['TargetPath'] + getSmallJPG(species['BestJPG']) + '" style=\"width:100%\">\n			        </a>', file=temptextfile)            
        # texts for 3 languages in 3 lines
        print('<p>' + species['ScientificName'].strip('0123456789 ').capitalize() + '</p>', file=temptextfile)
        print('<p>' + species['EnglishName'].capitalize() + '</p>', file=temptextfile)
        print( '<p>' + species['DutchName'].capitalize() + '</p>', file=temptextfile)
        # end
        print( '                </div>\n', file=temptextfile)
    except:
        a = 1
        # be sure the JPG is included, unless we do want it / need it
    if not MyProject.getSkipJPGs():
        sourcestr = MyProject.files_dict[species['BestJPG']]['WindowsFileName']
        targetstr = os.path.join(MyProject.ReviewFrozen(),species['TargetPath'], species['BestJPG'])
        try:  
            os.makedirs(os.path.join(MyProject.ReviewFrozen(), species['TargetPath']), exist_ok=True) 
            rc = shutil.copy(sourcestr, targetstr)
            # not sure about the logic :-(
            # upn = UniquePictureName_dict[MyProject.files_dict[species['BestJPG']]['UniquePictureName']]
            # species = MyProject.sp.species_dict[upn['WetSoortName']]
            # if species['iPictureWithJPGsCollection'] == 0:
            MyProject.addAllSpeciesFiles(sourcestr,os.path.join(MyProject.getBiotaWebRoot(), species['TargetPath'], species['BestJPG']))
            # print(f'New species {species['EnglishName']}')
        except:
            print(f"failed copy {sourcestr}\n            {targetstr}")
    else:
        pass
        #print(f"Done with text")
    #print(f"Done with function")        
    return

# using a temp file to avoid pass around very large strings
# not sure this matters
def addAllSpeciesWrapper(MyProject, speciesname, textCreated):
    try:
        with tempfile.SpooledTemporaryFile(mode='w+t', encoding='utf-8') as temptextfile:
            try:
                addAllSpeciesPage(MyProject, speciesname, temptextfile)
            except:
                pass
            temptextfile.seek(0)
            textlist = temptextfile.readlines()
            textCreated += '\n'.join(textlist)
            temptextfile.close()
            # write 
            with open(os.path.join(MyProject.ReviewFrozen(), 'allspecies.csv'), mode="w", newline="", encoding='utf-8') as allspeclist:
                for file in MyProject.getAllSpeciesFiles():
                    ftpfile = MyProject.AllSpeciesFiles[file]
                    print(f'"{file}", "{ftpfile}"', file=allspeclist)
    except:
        print(f"Unexpected error creating all species webpage.")
    return textCreated

# create overview page for all species, in order of classification
def addAllSpeciesPage(MyProject, speciesname, temptextfile):
    leaves = sorted(MyProject.getSpecies(speciesname)['Leaves'])
    for leaf in leaves:
        species = MyProject.sp.species_dict[leaf]
        if (int(species['iPicturesWithJPGsCollection']) + int(species['iPicturesWithJPGsThisRun'])) > 0:
            if species['Level'] == Level.SPECIES:
                writeOneSpecies(MyProject, leaf, temptextfile)
            else:
                textCreated = addAllSpeciesPage(MyProject, leaf, temptextfile)
   
    return

def addOneSpeciesPage(MyProject, species_param):
    BiotaDiskRoot = MyProject.getBiotaAllorNewDir()
    workstr = ""
    # a smarter selection is useful, especially to avoid very similar pictures
    try:
        rateorder = {}
        for JPGname in list(species_param['JPGmap']):
            JPGname_no_path = MyProject.files_dict[os.path.basename(JPGname)]
            diversity = MyProject.getPictureAgeSexePlumage(MyProject.UniquePictureName_dict[JPGname_no_path['UniquePictureName']])
            rating = MyProject.computeRatingFile(JPGname_no_path)
            if '-small' not in JPGname:
                rateorder[(diversity, rating, JPGname)] = JPGname
        # keep all JPGs sorted, not yet by male/female, breeding etc
        # Sort the dictionary by rating in descending order
        # Sort the items by diversity, rating, and JPGname in descending order
        sorted_items = sorted(rateorder.items(), key=lambda item: (item[0][0], item[0][1], item[0][2]), reverse=True)

        # Create a defaultdict to group items by diversity
        grouped_items = defaultdict(list)
        for (diversity, rating, JPGname), value in sorted_items:
            grouped_items[diversity].append((rating, JPGname, value))

        diversity_groups = len(grouped_items)
        maxpicturesperAgeSexePlumage = 2
        if diversity_groups == 1:
            maxpicturesperAgeSexePlumage = 4
        elif diversity_groups == 2:
            maxpicturesperAgeSexePlumage = 3

        # make sure only use the first occurrance of an UPN, which is the best (ie, with the highest rate)

        # print(f'{len(sorted_items)} JPGs for {species_param['DutchName']}/{species_param['ScientificName']} with {len(species_param['JPGmap'])}')            
        for diversity, items in grouped_items.items():
            filesfound = 0
            usedUPNs = {}
            rejectUPNs = 0            
            for rating, JPGname, value in items:
                JPGname_no_path = MyProject.files_dict[os.path.basename(JPGname)]
                if (JPGname_no_path != "") and (JPGname_no_path['UniquePictureName'] not in usedUPNs.keys()):
                    # do we have a picture close to it
                    JPGtime = getJPGtime(JPGname_no_path, MyProject)
                    dropit = False
                    for UPNshownsofar in usedUPNs:
                        showntime = usedUPNs[UPNshownsofar]
                        deltahours = abs(showntime - JPGtime).total_seconds()/3600
                        if deltahours < 0.25: dropit = True
                    # more conditions we can try

                    if not dropit:
                        usedUPNs[JPGname_no_path['UniquePictureName']] = JPGtime
                        for line in MyProject.getTemplateLines("vogelfoto.html"):
                            workstr += getTemplateOutputString(MyProject, species_param, JPGname_no_path,
                                                                MyProject.UniquePictureName_dict[JPGname_no_path['UniquePictureName']], line)
                        picture = MyProject.UniquePictureName_dict[JPGname_no_path['UniquePictureName']]
                        targetstr =  os.path.join(BiotaDiskRoot, picture['TargetPath'], picture['BestJPG'])
                        JPGname_no_path = MyProject.files_dict[os.path.basename(JPGname)]
                        if not MyProject.getSkipJPGs():
                            shutil.copy(JPGname_no_path['WindowsFileName'], targetstr)
                        filesfound += 1                            
                    else:
                        rejectUPNs += 1

                if (filesfound == maxpicturesperAgeSexePlumage):
                    break   # go to next man/female/young/breeding etc
    except:
        workstr = ""
    return workstr

# create summary page, that should become Wordpress
# also test best picture function.
# the first def is recursively calling itself, the second is the package
def addSpeciesToSummaryPost(MyProject, speciesname, selected_species):
    try:
        species = MyProject.sp.species_dict[speciesname]
        for leaf in species['Leaves']:
            species = MyProject.sp.species_dict[leaf]
            if int(species['iPictureThisRunCount']) > 0:            # brute force scan
                if species['Level'] == Level.SPECIES:
                    if MyProject.Blog.getSelectedSpeciesGroup() in species['TargetPath'] and \
                    (MyProject.Blog.getOnlyNewSpecies() and int(species['iSpeciesThisRunCount']) > 0) or (not MyProject.Blog.OnlyNewSpecies) and \
                    MyProject.Blog.getMinimumScore() <= species['currentHighestRating']:                 # Last is BUGBUG if high score is from past??
                        # picture species and time 
                        collectBestJPGs(MyProject, species['BestUPN'], selected_species)
                else:
                    addSpeciesToSummaryPost(MyProject, leaf, selected_species)
    except:
        print("addSpeciesToSummaryPost")                
    return

# species['iPictureThisRunCount'] tells me if the picture is seen for this species, but we do not yet
# know what is best picture for that species, because that use the overall best picture.  Also, that
# does not include male/female info.
# so ... we need a more complex algorithm:
# 1. Inspect the pictures with JPGs within the time window (from blog.from_time/last_time)
# 2. Add the "getPictureAgeSexePlumage" to the name for uniqueness, 
#    so one species can generate two pictures (adult/juv, male/female, breeding)
# 3. sort the best pictures of each type
def collectBestJPGs(MyProject, upn, selected_species):
    try:
        # get the species name that indirectly
        picturedata = MyProject.UniquePictureName_dict[upn]
        species_scientific_name = picturedata['WetSoortNaam']
        #species = MyProject.sp.species_dict[species_scientific_name]
        
        candidate_jpgs = {}

        for upn_iter in MyProject.UniquePictureName_dict:
            upn_item = MyProject.UniquePictureName_dict[upn_iter]
            # select only this species, that have a JPG, and that are within this time range 
            if upn_item['WetSoortNaam'] == species_scientific_name:
                if upn_item['BestJPG'] != "":
                    if upn_item['DateTimeOriginal'] >= MyProject.Blog.from_time and upn_item['DateTimeOriginal'] <= MyProject.Blog.last_time:
                        sortname = species_scientific_name + MyProject.getPictureAgeSexePlumage(upn_item)
                        info = [upn_item['BestJPG'], upn_item['Rate']]; 
                        if sortname in candidate_jpgs:
                            candidate_jpgs[sortname].append(info)
                        else:
                            candidate_jpgs[sortname] = [info]
        # see if we have different sex / age / plumage, pick the best for each one
        if len(candidate_jpgs) > 0:
            for candidate in candidate_jpgs:               # the best JPG for every candidate
                bestitem = candidate_jpgs[candidate][0]
                if len(candidate_jpgs[candidate])>1:
                    for item in candidate_jpgs[candidate][1:]:
                        if bestitem[1] < item[1]:
                            bestitem = item
                # remember the JPG we want to use
                selected_species[bestitem[0]] = MyProject.UniquePictureName_dict[MyProject.files_dict[bestitem[0]]['UniquePictureName']]['DateTimeOriginal']
    except:
        print("collectBestJPGs crashed.")               
    return

def addOneSpeciesJPG(MyProject, jpgname, textCreated, templatehtml):
    try:
        filedata = MyProject.files_dict[jpgname]
        picturedata = MyProject.UniquePictureName_dict[filedata['UniquePictureName']]
        species = MyProject.sp.species_dict[picturedata['WetSoortNaam']]
        MyProject.Blog.addBestJPG(filedata)
        # print(f"Picture {picturedata['NLSoortNaam']} {picturedata['UniquePictureName']} {picturedata['Description']}")
        for rawline in MyProject.getTemplateLines(templatehtml):
            textCreated += getTemplateOutputString(MyProject, species, filedata, picturedata, rawline.rstrip('\n')) + "\n"
        # copy a picture in subdirectory, named after file
        sourcestr = filedata['WindowsFileName']
        targetstr = MyProject.getBiotaBlogDir() + MyProject.Blog.getBlogDay() + '/' + filedata['File:FileName']
        #print(f'from {sourcestr} to {targetstr}')
        if not MyProject.getSkipJPGs():
            shutil.copy(sourcestr, targetstr)
            jpgResizeCopy(targetstr, horizontalsize = 600)
        if MyProject.getLanguage() == 'en':
            logger.info(f"Rated {species['currentHighestRating']:3}  {species['EnglishName']:32}  Species path {species['TargetPath']}")
    except:
        print(f"addOneSpeciesJPG failed for {picturedata}")
        pass
    return textCreated