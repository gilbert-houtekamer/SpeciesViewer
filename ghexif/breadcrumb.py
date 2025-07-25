import logging

logger = logging.getLogger("speciesviewer")

class Breadcrumb:
    breadcrumb = {}
    mp = None

    def __init__(self, MyProject):
        self.mp = MyProject
        return

    def clearBreadCrumb(self):
        self.breadcrumb = {}
        return

    def addBCsimple(self, link, item):
        self.breadcrumb[item] = ( link, 0, "", "", "")
        return
    
    def makeCrumb(self,  thisitem):
        workstr = '   <ul class="breadcrumb">\n'
        for item in list(self.breadcrumb.keys())[:-1]:
            workstr += '      <li><a href="' + self.breadcrumb[item][0] + '">' + item + '</a></li>\n'
        
        lastkey = next(reversed(self.breadcrumb.keys()))
        workstr += '      <li>' + lastkey + '</li>\n   </ul>\n'
        return workstr
    
    def appendLevelBC(self, speciesobj, prefix):
        workstr = ''
        if speciesobj['Up'] != "":
            workstr += self.appendLevelBC(self.mp.sp.species_dict[speciesobj['Up']], prefix)

        # kies goede taal
        # maak een URL
        #if  speciesobj['Level'].value != 1:
#            url = self.mp.sp.getPathToSpecies(speciesobj['ScientificName']) + speciesobj['ScientificName'] + '.html'
        url = prefix + '/' + speciesobj['TargetPath'] + speciesobj['ScientificName'] + '-nl.html'
        self.breadcrumb[speciesobj['ScientificName'].strip('0123456789 ').capitalize()] = (url, 
                    speciesobj['Level'].value,                                   
                    speciesobj['ScientificName'].strip('0123456789 ').capitalize(), 
                    speciesobj['EnglishName'], 
                    speciesobj['DutchName'])    
        # else:
        #     self.breadcrumb[speciesobj['ScientificName']] = ( "", 
        #                 speciesobj['Level'].value,"","","")
        return workstr

    def appendSpeciesBC(self, species, prefix):
        # find species
        try:
            speciesobj = self.mp.sp.species_dict[species]
            self.appendLevelBC(speciesobj, prefix)
        except:
            pass
        return