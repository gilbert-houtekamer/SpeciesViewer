import logging

logger = logging.getLogger(__name__)

class Breadcrumb:
    breadcrumb = {}
    mp = None

    def __init__(self, MyProject):
        self.mp = MyProject
        return

    def clearBreadCrumb(self):
        self.breadcrumb = {}
        return

    def addBCsimple(self, item, link):
        self.breadcrumb[item] = ( link, 0, "", "", "")
        return
    
    def makeCrumb(self,  thisitem):
        workstr = '   <ul class="breadcrumb w3-bar  w3-win8-olive">\n'
        for item in self.breadcrumb:
            workstr += '      <li><a href="' + self.breadcrumb[item][0] + '">' + item + '</a></li>\n'
        workstr += '      <li>' + thisitem + '</li>\n   </ul>\n'
        return workstr
    
    def appendLevelBC(self, speciesobj, prefix):
        workstr = ''
        if speciesobj['Up'] != "":
            workstr += self.appendLevelBC(self.mp.sp.species_dict[speciesobj['Up']], prefix)

        # kies goede taal
        # maak een URL
        if  speciesobj['Level'].value != 1:
#            url = self.mp.sp.getPathToSpecies(speciesobj['ScientificName']) + speciesobj['ScientificName'] + '.html'
            url = prefix + speciesobj['TargetPath'] + speciesobj['ScientificName'] + '.html'
            self.breadcrumb[speciesobj['ScientificName'].strip('0123456789 ').capitalize()] = ( url, 
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