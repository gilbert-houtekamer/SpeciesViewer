import os
import shutil
import time
import tempfile
from datetime import datetime
from operator import itemgetter
import logging

global config

from ghexif import COLUMN_NAMES, SELECTED_FIELDS
from ghexif import files_dict, UniquePictureName_dict
from ghexif.handling import UniquePictureName_dict
from ghexif.specieslib import Level, computeRatingFile, getPictureAgeSexePlumage
import ghexif.projectlib

logger = logging.getLogger(__name__)

ClassificationGroupName = ["Species","Family", "Order", "Class", "Phylum", "Kingdom", "Biota"]
ClassificationGroupNameNL = ["Soort", "Familie", "Orde", "Klasse","Stam", "Rijk", "Biota"]
ClassificationGroupNameNLmeervoud = ["Soorten", "Families", "Orden", "Klassen","Stammen", "Rijken", "Biota"]
ClassificationGroupNameENmeervoud = ["Species","Families", "Orders", "Classes", "Phyla", "Kingdoms", "Biota"]

# web support 
def getTemplateOutputString(MyProject, species, file, picture, TemplateLine):
# 	Parse line in pieces between tokens (%%n%%) with n an integer that refers to a species-related blurp.
# 	The syntax implies that text and token %% seperators alternate, which we keep track off with TextOrToken 
# 	probably want nested call for subroutines, ie make this an output string.
# 	TemplateOutputString = ""
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

def getFormattedLocation(picture):
    if picture['Location'] == "":
        return getFormattedCityStateCountry(picture)
    else:
        if picture['Location'] != "" and len(picture['Location']) > 30:
            return picture['Location']
        if picture['Country'] == "": return picture['Location'];
        if picture['Location'] == picture['City'] or picture['Location'] == picture['Country'] or picture['Location'] == "Unnamed Road": 
            return getFormattedCityStateCountry(picture)
        return picture['Location'] + " (" + getFormattedCityStateCountry(picture) + ")"

def makeLinkScienticficName(MyProject, BiotaWebRoot, classification, workstr):
    if classification['iSpeciesCollectionCount'] == 0:
        workstr +=  classification['ScientificName'].strip('0123456789 ').capitalize()
    else:
        workstr +=  '<a href="' + BiotaWebRoot + classification["TargetPath"] + classification["ScientificName"] + '-' + MyProject.getLanguage() +  '.html\"' + '>\n'
        workstr += '			         ' + classification['ScientificName'] + '</a>'
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

def getJPGtime(JPGobject):
    try:
        JPGtime = datetime.strptime(JPGobject['EXIF:DateTimeOriginal'], "%Y:%m:%d %H:%M:%S")
    except:
        try:
            JPGtime = datetime.strptime(UniquePictureName_dict[JPGobject['UniquePictureName']]['DateTimeOriginal'], "%Y:%m:%d %H:%M:%S")
        except:
            JPGtime = ''
    return JPGtime

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
                return species_local['DutchName'].capitalize() + " (" + species_local['ScientificName'].capitalize() + ")"
            elif species_local['EnglishName'] != "" and language == 'en':
                return species_local['EnglishName'].capitalize() + " (" + species_local['ScientificName'].capitalize() + ")" 
            else:
                return species_local['ScientificName'].capitalize()
        elif tokenno == 30:
            # Convert time.time() to timestamp to a datetime object
            date = datetime.fromtimestamp(time.time())
            # Format the datetime object as "Wed Jan 24, 2024"
            return date.strftime("a %b %d, %Y")   
        elif tokenno == 31:
            #return species_param['DutchName'].capitalize() + ' ' + species_param['ScientificName'].strip('0123456789 ').capitalize()
            return species_param['ScientificName'].strip('0123456789 ').capitalize()
        elif tokenno == 32:                                 # one level lower (enkelvoud)
            return str(species_param['Level'])[6:].capitalize()
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
                return groupname + ': ' + species_param['ScientificName'].capitalize()
        elif tokenno == 34:                                 # meervoud voor de children, dus 1 extra er af daarom -2                   
            return ClassificationGroupNameNLmeervoud[species_param['Level'].value-2].lower()
        elif tokenno == 35:
            workstr =  ClassificationGroupNameNL[species_param['Level'].value-1] if MyProject.getLanguage() == "nl" else ClassificationGroupNameNL[species_param['Level'].value-1] 
            workstr +=  ' ' + species_param['ScientificName'].strip('0123456789 ').capitalize()
            languageName = 'DutchName' if MyProject.getLanguage() == "nl" else 'EnglishName'
            if species_param[languageName] != "":
                workstr += ' (' + species_param['DutchName'].capitalize() + ')'
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
            else: ""        
        elif tokenno == 43:# 	case 43: // was 60 = totaal aantal soorten
            return MyProject.getSpeciesCount('biota')
        elif tokenno == 44:
            return BiotaWebRoot + picture['TargetPath'] + picture['BestJPG']
        elif tokenno == 45:
            return MyProject.getBlogDateFromPictures() + '/' + file['File:FileName']    #BUGBUG
        elif tokenno == 46:
            return getJPGtime(file).strftime('%Y:%m:%d %H:%M:%S')
        elif tokenno == 49: # banner, now by kingdom, but could be more specific
            repository = MyProject.getRepositoryBanner().lower()
            if repository != "":
                workstr = BiotaWebRoot + "includes web/" + repository + ".jpg"
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

        elif tokenno == 90:
            return BiotaWebRoot
        elif tokenno == 91:
            language = MyProject.getLanguage()            
            workstr = "	<h4>Links</h4>\n	<div class=""\"content\""">\n		<ul class= ""\"w3-ul w3-hoverable\""">\n"
            workstr += '			<li class=""\"first\"""><a href="'+ BiotaWebRoot +'biotablog/blog index-' + language +'.html">Recent</a></li>\n'
            if language == 'nl':
                workstr += '			<li><a href= "'+ BiotaWebRoot +'summaries/all biota species-nl.html">Alle soorten</a></li>\n'
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + "biota/biota-nl.html" + "\""">Biota: Leven</a></li>\n"
                languageName = 'DutchName'
            else:
                workstr += '			<li><a href= "'+ BiotaWebRoot +'summaries/all biota species-en.html">All species</a></li>\n'
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + "biota/biota-en.html" + "\""">Biota: Life</a></li>\n"
                languageName = 'EnglishName'                
            if len(kingdom_local)>0: 
                rijk = 'Rijk: ' if language == 'nl' else 'Kingdom: '
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + kingdom_local['TargetPath'] + kingdom_local['ScientificName'] + '-' + language + '.html">' + rijk + \
                    kingdom_local[languageName].capitalize() if kingdom_local[languageName] != "" else  kingdom_local['ScientificName'].capitalize() + "</a></li>\n"
            if len(phylum_local)>0:
                stam = 'Stam: ' if language == 'nl' else 'Phylum: '
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + phylum_local['TargetPath'] + phylum_local['ScientificName']  + '-' + language + '.html">' + stam + \
                    phylum_local[languageName].capitalize() if phylum_local[languageName] != "" else phylum_local['ScientificName'].capitalize() + "</a></li>\n"
            if len(class_local)>0:
                klasse = 'Klasse: ' if language == 'nl' else 'Class: '                
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + class_local['TargetPath'] + class_local['ScientificName'] + '-' + language + '.html">' + klasse + \
                    class_local[languageName].capitalize() if class_local[languageName] != "" else class_local['ScientificName'].capitalize() + "</a></li>\n"
            if len(order_local)>0:
                orde = 'Orde: ' if language == 'nl' else 'Order: '                       
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + order_local['TargetPath'] + order_local['ScientificName'] + '-' + language + '.html">' + orde + \
                    order_local[languageName].capitalize() if order_local[languageName] != "" else order_local['ScientificName'].capitalize() + "</a></li>\n"
            if len(family_local)>0:
                fam = 'Familie: ' if language == 'nl' else 'Family: '                
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + family_local['TargetPath'] + family_local['ScientificName'] + '-' + language + '.html">' + fam + \
                    family_local[languageName].capitalize() if family_local[languageName] != "" else family_local['ScientificName'].capitalize() + "</a></li>\n"
            workstr += "		</ul>\n	</div>\n"
            return workstr
        elif tokenno == 93:         # Plaatjes en namen, zelfde voor beide talen
            language = MyProject.getLanguage()
            workstr = "<div id=""\"grid\""">\n"
            for speciesname in sorted(species_param['Leaves']):
                species = MyProject.getSpecies(speciesname)
                if (int(species['iPictureThisRunCount']) + int(species['iPictureCollectionCount'])) > 0:   # at least one
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
        elif tokenno == 100:         # Nederlandse plaatjes en namen
            BiotaDiskRoot = MyProject.getBiotaAllorNewDir()
            # if species_param['ScientificName'] in ['anser anser','platalea leucorodia','phalacrocorax carbo','charadrius dubius','numenius phaeopus','riparia riparia']:
            #     bug = 1
            filesfound = 0
            # a smarter selection is useful, especially to avoid very similar pictures
            # sort species_param['JPGmap'] by rating and JPGname
            # print(f"{species_param['EnglishName']}")
            # if species_param['EnglishName'] == "three-banded plover":
            #     filesfound = 0
            rateorder = {}
            for JPGname in list(species_param['JPGmap']):
                JPGname_no_path = files_dict[os.path.basename(JPGname)]
                rating = computeRatingFile(JPGname_no_path)
                rateorder[(rating, JPGname)] = JPGname
            # keep all JPGs sorted, only top 6 pictures are used
            sorted_items = sorted(rateorder.items(), key=lambda item: (item[0][0], item[0][1]), reverse=True)
            # make sure only use the first occurrance of an UPN, which is the best (ie, with the highest rate)
            usedUPNs = {}
            rejectUPNs = 0
            # print(f'{len(sorted_items)} JPGs for {species_param['DutchName']}/{species_param['ScientificName']} with {len(species_param['JPGmap'])}')            
            for (rating, JPGname) in sorted_items:
                JPGname_no_path = files_dict[os.path.basename(JPGname)]
                if (JPGname_no_path != "") and (JPGname_no_path['UniquePictureName'] not in usedUPNs.keys()):
                    # do we have a picture close to it
                    JPGtime = getJPGtime(JPGname_no_path)
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
                                                                UniquePictureName_dict[JPGname_no_path['UniquePictureName']], line)
                        picture = UniquePictureName_dict[JPGname_no_path['UniquePictureName']]
                        targetstr =  os.path.join(BiotaDiskRoot, picture['TargetPath'], picture['BestJPG'])
                        JPGname_no_path = files_dict[os.path.basename(JPGname)]
                        if not MyProject.getSkipJPGs():
                            shutil.copy(JPGname_no_path['WindowsFileName'], targetstr)
                        filesfound += 1                            
                    else:
                        rejectUPNs += 1

                if (filesfound == 4):
                    #print(f'Quit {species_param['DutchName']} after 4 images, {rejectUPNs} rejected.')
                    return workstr
            #print(f'Quit {species_param['DutchName']} after {filesfound} images, {rejectUPNs} rejected.')
            return workstr
        elif tokenno == 110 or tokenno == 113:         # blog achtige samenvatting
            language = 'nl' if tokenno == 113 else 'en'
            # step 1: collect selected species
            selected_dict = {}
            addSpeciesToSummaryPost(MyProject, species_param['ScientificName'], selected_dict, "oneblogphoto-" + language + ".html")
            # step 2: sort by time taken picture
            time_sorted_dict = dict(sorted(selected_dict.items(), key=itemgetter(1)))            
            # step 3: generate the webpage
            workstr = ""
            for file in time_sorted_dict:
                workstr = addOneSpeciesJPG(MyProject, file, workstr, "oneblogphoto-" + language + ".html")
            # return HTML to caller to put in webpage
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
                        workstr += "<i>" + species_local['ScientificName'].capitalize() + "</i>. "
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
                    parsed_date = getJPGtime(file)
                    workstr += " De foto is genomen "
                    if getFormattedLocation(picture) != "":
                        workstr += " bij " + getFormattedLocation(picture) 
                    workstr += " om " + parsed_date.strftime('%H:%M') + " op " + parsed_date.strftime('%d %b, %Y').lower()  + "."
                except:
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
                        workstr += "<i>" + species_local['ScientificName'].capitalize() + "</i>. "
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
                    parsed_date = getJPGtime(file) 
                    workstr += " The photo was taken "
                    if getFormattedLocation(picture) != "":
                        workstr += " at " + getFormattedLocation(picture)
                    workstr += " at " + parsed_date.strftime('%H:%M') + " on " + parsed_date.strftime('%A %B %d, %Y')  + "."
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
                else:
                    workstr += f'          <a href="{logname}-nl.html"><img\n'
                    workstr += '            src="' + logname + '/' + blog["BestJPGs"][0] + '"\n'
                    workstr += '            class="w3-border" style="padding:8px;max-width:100%" title="Click to open "' + blog["title_" + language] + '""></a>\n'
                workstr += '      </div>\n'
                workstr += '      <div class="w3-col" style="width:70%">\n'
                workstr += '        <div class="content" style="margin-left:10px">\n'
                workstr += '          <table class="w3-table w3-hoverable">\n'
                workstr += '            <tr class="w3-win8-olive">\n'
                workstr += f'              <th>Titel</th><td><a href="{logname}-nl.html">' + blog["title_" + language] + '</td>\n'
                workstr += '            </tr>\n'
                workstr += '            <tr>\n'
                workstr += '              <th></th><td>' + blog["message_" + language] + '</td>\n'
                workstr += '            </tr>\n'
                if blog["SelectedGroup"] != 'biota':
                    workstr += '            <tr>\n'
                    workstr += '              <th>Soorten</th><td>' + blog["SelectedGroup"].capitalize() + '</td>\n'
                    workstr += '            </tr>\n'
                workstr += '            <tr>\n'
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
            if MyProject.getLanguage() == 'nl': languageName = 'DutchName'
            else: languageName = 'EnglishName'   

            if species_local[languageName] != "":
                workstr = species_local[languageName].capitalize() + formatSexe(picture) + formatAgePlumage(picture) + \
                   " <br><i><small>" + species_local['ScientificName'].strip('0123456789 ').capitalize() + "</small></i></br>"
            else:
                workstr = "<i>" + species_local['ScientificName'].capitalize() + "</i>"
            return workstr
        elif tokenno == 126:
            w =  MyProject.Blog.getColumnWidth()
            return str(w)
        else:
            return '<token ' + tokenno + '>'
    except:

        logger.info(f"{species_param['ScientificName']}:{'<Failed to handle token ' + tokenno + '>'}")
        return '<Failed to handle token '  + tokenno + '>'

# logic selected species
def writeOneSpecies(MyProject, leaf, temptextfile):
    species = MyProject.sp.species_dict[leaf]
    #print(f"working {species['ScientificName']} {species['EnglishName']}")
    language = MyProject.getLanguage()
    BiotaWebRoot = MyProject.getBiotaWebRoot()
    try:
        print('                <div>\n			        <a href="' + \
            BiotaWebRoot + species["TargetPath"] + species["ScientificName"] +  '-' + language + '.html' + '">\n', file=temptextfile)
        if species['BestJPG'] != "" and species['BestJPGPathName'] != "":
            print('			            <img src="' + BiotaWebRoot + \
            species['TargetPath'] + species['BestJPG'] + '" style=\"width:100%\">\n			        </a>', file=temptextfile)            
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
        sourcestr = files_dict[species['BestJPG']]['WindowsFileName']
        targetstr = os.path.join(MyProject.ReviewFrozen(),species['TargetPath'], species['BestJPG'])
        try:  
            #print(f"start dir   {sourcestr}\n            {targetstr}")                   
            os.makedirs(os.path.join(MyProject.ReviewFrozen(), species['TargetPath']), exist_ok=True) 
            #print(f"make file   {sourcestr}\n            {targetstr}")                   
            rc = shutil.copy(sourcestr, targetstr)
            #print(f"copy ok     {sourcestr}\n            {targetstr}")
            MyProject.addAllSpeciesFiles(sourcestr,os.path.join(MyProject.getBiotaWebRoot(), species['TargetPath'], species['BestJPG']))
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

# create summary page, that should become Wordpress
# also test best picture function.
# the first def is recursively calling itself, the second is the package
def addSpeciesToSummaryPost(MyProject, speciesname, selected_species, templatehtml):
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
                addSpeciesToSummaryPost(MyProject, leaf, selected_species, templatehtml)
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
    # get the species name that indirectly
    picturedata = UniquePictureName_dict[upn]
    species_scientific_name = picturedata['WetSoortNaam']
    #species = MyProject.sp.species_dict[species_scientific_name]
    
    candidate_jpgs = {}

    for upn_iter in UniquePictureName_dict:
        upn_item = UniquePictureName_dict[upn_iter]
         # select only this species, that have a JPG, and that are within this time range 
        if upn_item['WetSoortNaam'] == species_scientific_name:
            if upn_item['BestJPG'] != "":
                if upn_item['DateTimeOriginal'] >= MyProject.Blog.from_time and upn_item['DateTimeOriginal'] <= MyProject.Blog.last_time:
                    sortname = species_scientific_name + getPictureAgeSexePlumage(upn_item)
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
            selected_species[bestitem[0]] = UniquePictureName_dict[files_dict[bestitem[0]]['UniquePictureName']]['DateTimeOriginal']
    return

def addOneSpeciesJPG(MyProject, jpgname, textCreated, templatehtml):
    filedata = files_dict[jpgname]
    picturedata = UniquePictureName_dict[filedata['UniquePictureName']]
    species = MyProject.sp.species_dict[picturedata['WetSoortNaam']]
    # filedata = files_dict[species['BestJPG']]
    # picturedata = UniquePictureName_dict[species['BestUPN']]
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
    if MyProject.getLanguage() == 'en':
        logger.info(f"Rated {species['currentHighestRating']:3}  {species['EnglishName']:32}  Species path {species['TargetPath']}")
    return textCreated