import os
import shutil
import time
from datetime import datetime
  
global config

from ghexif import COLUMN_NAMES, SELECTED_FIELDS
from ghexif import files_dict, UniquePictureName_dict
import ghexif
from ghexif.specieslib import computeRatingFile
from ghexif.handling import UniquePictureName_dict
from ghexif.specieslib import Level
import ghexif.projectlib

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
        if picture['Country'] == "": return picture['Location'];
        if picture['Location'] == picture['City'] or picture['Location'] == picture['Country']: 
            return getFormattedCityStateCountry(picture)
        return picture['Location'] + " (" + getFormattedCityStateCountry(picture) + ")"

def makeLinkScienticficName(BiotaWebRoot, classification, workstr):
    if classification['iSpeciesCollectionCount'] == 0:
        workstr +=  classification['ScientificName'].strip('0123456789 ').capitalize()
    else:
        workstr +=  '<a href="' + BiotaWebRoot + classification["TargetPath"] + classification["ScientificName"] + '.html\"' + '>\n'
        workstr += '			         <img src="' + \
                BiotaWebRoot +  classification["BestJPGPathName"] + classification["BestJPG"] + '"' + ' style="width:100%">\n'
        workstr += "			     </a>"
    return workstr

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
    if species_param != None:
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
            order_local = MyProject.getSpecies(family_local['Up'])                          
            class_local = MyProject.getSpecies(order_local['Up'])                  
            phylum_local = MyProject.getSpecies(class_local['Up'])          
            kingdom_local = MyProject.getSpecies(phylum_local['Up'])  
        elif species_param['Level'] == Level.SPECIES:
            # species_local = species_param                         # Already defaulted
            if species_local['Up'] != "":                           # Linked, assume all levels are ok
                family_local = MyProject.getSpecies(species_local['Up'])           
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
            return ClassificationGroupNameNL[species_param['Level'].value-1] + ': ' + species_param['DutchName'].capitalize()
        elif tokenno == 34:                                 # meervoud voor de children, dus 1 extra er af daarom -2                   
            return ClassificationGroupNameNLmeervoud[species_param['Level'].value-2]
        elif tokenno == 35:          
            workstr = ClassificationGroupNameNL[species_param['Level'].value-1] + \
                        species_param['ScientificName'].strip('0123456789 ').capitalize()
            if species_param['DutchName'] != "":
                workstr += species_param['DutchName'].capitalize()
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
                workstr = MyProject.ct.getOneLanguage(picture['Description'], language)
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
            return file["EXIF:DateTimeOriginal"]
        elif tokenno == 49: # banner, now by kingdom, but could be more specific
            if kingdom_local == {}:
                workstr = BiotaWebRoot + "includes web/keerkringvogel.jpg"
            else:
                if kingdom_local['ScientificName'] == "Animalia":
                    workstr = BiotaWebRoot + "includes web/keerkringvogel.jpg"
                elif kingdom_local['ScientificName'] == "Fungi":
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
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + kingdom_local['TargetPath'] + kingdom_local['ScientificName'] + '-' + language + '.html">Rijk ' + kingdom_local[languageName].capitalize()  + "</a></li>\n";
            if len(phylum_local)>0:
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + phylum_local['TargetPath'] + phylum_local['ScientificName']  + '-' + language + '.html">Stam ' + phylum_local[languageName].capitalize() + "</a></li>\n";
            if len(class_local)>0:
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + class_local['TargetPath'] + class_local['ScientificName'] + '-' + language + '.html">Klasse ' + class_local[languageName].capitalize() + "</a></li>\n";
            if len(order_local)>0:        
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + order_local['TargetPath'] + order_local['ScientificName'] + '-' + language + '.html">Orde ' + order_local[languageName].capitalize() + "</a></li>\n";
            if len(family_local)>0:
                workstr += "			<li><a href= ""\"" + BiotaWebRoot + family_local['TargetPath'] + family_local['ScientificName'] + '-' + language + '.html">Familie ' + family_local[languageName].capitalize() + "</a></li>\n";
            workstr += "		</ul>\n	</div>\n"
            return workstr
        elif tokenno == 93:         # Plaatjes en namen, zelfde voor beide talen
            language = MyProject.getLanguage()
            workstr = "<div id=""\"grid\""">\n"
            for speciesname in species_param['Leaves']:
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
            rateorder = {}
            for JPGname in list(species_param['JPGmap']):
                JPGname_no_path = files_dict[os.path.basename(JPGname)]
                rating = computeRatingFile(JPGname_no_path)
                rateorder[(rating, JPGname)] = JPGname
            # keep all JPGs sorted, only top 6 pictures are used
            sorted_items = sorted(rateorder.items(), key=lambda item: (item[0][0], item[0][1]), reverse=True)
            # print(f'{len(sorted_items)} JPGs for {species_param['DutchName']}/{species_param['ScientificName']} with {len(species_param['JPGmap'])}')            
            for (rating, JPGname) in sorted_items:
                JPGname_no_path = files_dict[os.path.basename(JPGname)]
                if JPGname_no_path != "":
                    for line in MyProject.getTemplateLines("vogelfoto.html"):
                        workstr += getTemplateOutputString(MyProject, species_param, JPGname_no_path,
                                                            UniquePictureName_dict[JPGname_no_path['UniquePictureName']], line)
                    picture = UniquePictureName_dict[JPGname_no_path['UniquePictureName']]
                    targetstr =  BiotaDiskRoot + picture['TargetPath'] + picture['BestJPG']
                    JPGname_no_path = files_dict[os.path.basename(JPGname)]
                    if not MyProject.getSkipJPGs():
                        shutil.copy(JPGname_no_path['WindowsFileName'], targetstr)
    # 				}
                filesfound += 1
                if (filesfound > 5): return workstr
            return workstr
        elif tokenno == 110:         # blog achtige samenvatting
            workstr = ""
            workstr = addSpeciesToSummaryPost(MyProject, species_param['ScientificName'], workstr, "oneblogphoto-en.html")
            return workstr
        elif tokenno == 111:         # Nederlandse tekst
            if kingdom_local == {}:
                workstr = "De foto toont de omgeving." 
            else:
                workstr = "De foto toont een "
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
                workstr += " uit de orde van de <i>"
                workstr = makeLinkScienticficName(BiotaWebRoot, order_local, workstr)
                workstr += "</i> en de familie <i>"
                workstr = makeLinkScienticficName(BiotaWebRoot, family_local, workstr)
                workstr +=  "</i>."
                parsed_date = datetime.strptime(file["EXIF:DateTimeOriginal"][0:10], '%Y:%m:%d') 
                workstr += " De foto is genomen "
                if picture != "":
                    workstr += " bij " + getFormattedLocation(picture) + " om " + file["EXIF:DateTimeOriginal"][11:16] + " op " + parsed_date.strftime('%d %b, %Y').lower()  + "."
            return workstr        
        elif tokenno == 112:         # Engelse tekst
            if kingdom_local == {}:
                workstr = "The photo shows the environment." 
            else:
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
                workstr += " in the order <i>"
                workstr = makeLinkScienticficName(BiotaWebRoot, order_local, workstr)
                workstr += "</i> and the family <i>"
                workstr = makeLinkScienticficName(BiotaWebRoot, family_local, workstr)
                workstr +=  "</i>."
                parsed_date = datetime.strptime(file["EXIF:DateTimeOriginal"][0:10], '%Y:%m:%d') 
                workstr += " The photo was taken "
                if picture != "":
                    workstr += " at " + getFormattedLocation(picture)
                workstr += " at " + file["EXIF:DateTimeOriginal"][11:16] + " on " + parsed_date.strftime('%A %B %d, %Y')  + "."
                 
            return workstr    
        elif tokenno == 113:         # blog achtige samenvatting
            workstr = ""
            workstr = addSpeciesToSummaryPost(MyProject, species_param['ScientificName'], workstr, "oneblogphoto-nl.html")
            return workstr
        elif tokenno == 114:
            return MyProject.getBiotaBlogLanguageSwitch().lower()
        elif tokenno == 115:
            return MyProject.Blog.getBlogDay().lower()
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
            workstr = addAllSpeciesPage(MyProject, species_param['ScientificName'], workstr)
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
                    workstr += '          <img\n'
                    workstr += '            src="' + logname + '/' + blog["BestJPGs"][0] + '"\n'
                    workstr += '            class="w3-border" style="padding:8px;max-width:100%">\n'
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
                workstr += '              <th>Datum:</th><td>' + blog["blog"][14:24] + '</td>\n'
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
                workstr = species_local[languageName].capitalize() + " <br><i><small>" + species_local['ScientificName'].strip('0123456789 ').capitalize() + "</small></i></br>"
            else:
                workstr = "<i>" + species_local['ScientificName'].capitalize() + "</i>"
            return workstr
        else:
            return '<token ' + tokenno + '>'
    except:
        print(f"{species_param['ScientificName']}:{'<Failed to handle token ' + tokenno + '>'}")
        return '<Failed to handle token '  + tokenno + '>'

# create overview page for all species, in order of classification
def addAllSpeciesPage(MyProject, speciesname, textCreated):
    language = MyProject.getLanguage()
    BiotaWebRoot = MyProject.getBiotaWebRoot()
    species = MyProject.getSpecies(speciesname)
    for leaf in species['Leaves']:
        species = MyProject.getSpecies(leaf)
        if (int(species['iPicturesWithJPGsCollection']) + int(species['iPicturesWithJPGsThisRun'])) > 0:
            if species['Level'] == Level.SPECIES:
                #textCreated += f"Create page for {species['ScientificName']} - {species['DutchName']} - {species['currentHighestRating']}\n"
                # picture, need to create links
                textCreated += '                <div>\n			        <a href="' + \
                    BiotaWebRoot + species["TargetPath"] + species["ScientificName"] +  '-' + language + '.html' + '">\n'
                if species['BestJPG'] != "" and species['BestJPGPathName'] != "":
                    textCreated += '			            <img src="' + BiotaWebRoot + \
                    species['TargetPath'] + species['BestJPG'] + '" style=\"width:100%\">\n			        </a>'              
                # texts for 3 languages in 3 lines
                textCreated += '<p>' + species['ScientificName'].strip('0123456789 ').capitalize() + '</p>'
                textCreated += '<p>' + species['EnglishName'].capitalize() + '</p>'
                textCreated += '<p>' + species['DutchName'].capitalize() + '</p>\n'
                # end
                textCreated += '                </div>\n'
            else:
                textCreated = addAllSpeciesPage(MyProject, leaf, textCreated)
    return textCreated

# create summary page, that should become Wordpress
# also test best picture function.
# the first def is recursively calling itself, the second is the package
def addSpeciesToSummaryPost(MyProject, speciesname, textCreated, templatehtml):
    species = MyProject.sp.species_dict[speciesname]
    for leaf in species['Leaves']:
        species = MyProject.sp.species_dict[leaf]
        if int(species['iPictureThisRunCount']) > 0:            # brute force scan
            if species['Level'] == Level.SPECIES:
                if MyProject.Blog.getSelectedSpeciesGroup() in species['TargetPath'] and \
                   (MyProject.Blog.getOnlyNewSpecies() and int(species['iSpeciesThisRunCount']) > 0) or (not MyProject.Blog.OnlyNewSpecies) and \
                   MyProject.Blog.getMinimumScore() <= species['currentHighestRating']:                 # Last is BUGBUG if high score is from past??
                #textCreated += f"Create page for {species['ScientificName']} - {species['DutchName']} - {species['currentHighestRating']}\n"
                    filedata = files_dict[species['BestJPG']]
                    picturedata = UniquePictureName_dict[species['BestUPN']]
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
            else:
                textCreated = addSpeciesToSummaryPost(MyProject, leaf, textCreated, templatehtml)
    return textCreated