# utility to determine which steps are due/possible
from urllib.parse import urlparse
import os
import logging
import configparser
import logging

logger = logging.getLogger(__name__)

STATE_NAMES = [
    "Unknown", 
    "MakeRepository",
    "ReadDirExif", 
    "WritePictures", 
    "WriteBlog", 
    "BuildWebsite",
    "Freeze",
    "WriteCommit"
]

class StateMachine:
    Directory = None
    Version = '0.0'
    taskstate = {}
    
    def __init__(self, directory):
        if directory != None:
            self.Directory = directory
        return 
    
    def getUTF8(self, state, ansistring):
        workstr = state.get("lastrunmessages", ansistring)
        try:
            workstr = workstr.encode('latin1').decode('utf-8')          # we should validate this upon input
            workstr = workstr.replace("<female>", "♀")
            workstr = workstr.replace("<male>", "♂")
        except:
            pass  # too bad, we tried
        return workstr    

    # get status/task information for this task
    def readState(self):
        # first   task number
        # second  last run ok?
        # third   message from last run if even
        # forth   list (numbers) that are permitted (empty)
        statemachine = os.path.join(self.Directory, "statemachine.ini")
        if os.path.exists(statemachine):
            # create a separete config instance
            state = configparser.ConfigParser()
            state.read(statemachine)
            # code state
            # selectspecies = state.get("SpeciesViewer","version")
            self.taskstate["MakeRepository"] = [state.getboolean("state","sv1MakeRepository"), 
                            self.getUTF8(state, "sv1MakeRepositoryMessage"), 
                            ["ReadDirExif"], []]       
            self.taskstate["ReadDirExif"] = [state.getboolean("state","sv2ReadDirExif"), 
                            self.getUTF8(state, "sv2ReadDirExifMessage"), 
                            ["ReadDirExif", "WritePictures"], ["MakeRepository"]]
            self.taskstate["WritePictures"] = [state.getboolean("state","sv3WritePictures"), 
                            self.getUTF8(state,"sv3WritePicturesMessage"), 
                            ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite"], ["ReadDirExif"]]
            self.taskstate["WriteBlog"] = [state.getboolean("state","sv4WriteBlog"),
                            self.getUTF8(state,"sv4WriteBlogMessage"), 
                            ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite"], ["ReadDirExif", "WritePictures"]]
            self.taskstate["BuildWebsite"] = [state.getboolean("state","sv5BuildWebsite"),
                            self.getUTF8(state,"sv5BuildWebsiteMessage"), 
                            ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite", "Freeze"], ["ReadDirExif", "WritePictures"]]
            self.taskstate["Freeze"] = [state.getboolean("state","sv6Freeze"), 
                            self.getUTF8(state,"sv6FreezeMessage"), 
                            ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite", "Freeze", "WriteCommit"],
                            ["ReadDirExif", "WritePictures", "BuildWebsite"]]
            self.taskstate["WriteCommit"] = [state.getboolean("state","sv7WriteCommit"), 
                            self.getUTF8(state,"sv7WriteCommitMessage"), 
                            ["ReadDirExif", "WritePictures"], ["ReadDirExif", "WritePictures", "BuildWebsite", "Freeze"]]
        else:
            self.taskstate = {"MakeRepository" : [True, "", 
                                                 ["ReadDirExif"], []],
                         "ReadDirExif" : [False, "",  
                                               ["ReadDirExif", "WritePictures"], ["MakeRepository"]],
                         "WritePictures" : [False, "",  
                                               ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite"], ["ReadDirExif"]],
                         "WriteBlog" : [False, "",  
                                            ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite"], 
                                            ["ReadDirExif", "WritePictures"]],
                         "BuildWebsite" : [False, "",  
                                               ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite", "Freeze"], 
                                               ["ReadDirExif", "WritePictures"]],
                         "Freeze" : [False, "",  
                                        ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite", "Freeze", "WriteCommit"],
                                        ["ReadDirExif", "WritePictures", "BuildWebsite"]],
                         "WriteCommit" : [False, "",  
                                              ["ReadDirExif", "WriteCommit"],
                                              ["ReadDirExif", "WritePictures", "BuildWebsite", "Freeze"]]
            }
        return self.taskstate

    def writeState(self):
        # 
        statemachine = os.path.join(self.Directory, "statemachine.ini")
        # create a separete config instance
        state = configparser.ConfigParser()

        state['SpeciesViewer'] = {'version' : self.Version}
        state['state'] = {
            'sv1MakeRepository'  : True,
            'sv2ReadDirExif'  : self.taskstate["ReadDirExif"][0],
            'sv3WritePictures'  : self.taskstate["WritePictures"][0],
            'sv4WriteBlog'  : self.taskstate["WriteBlog"][0],
            'sv5BuildWebsite'  : self.taskstate["BuildWebsite"][0],
            'sv6Freeze'  : self.taskstate["Freeze"][0],
            'sv7WriteCommit'  : self.taskstate["WriteCommit"][0]
        }
        state['lastrunmessages'] = {
            'sv1MakeRepositoryMessage'  : "sv1MakeReposity succeeded.",
            'sv2ReadDirExifMessage'  : self.taskstate["ReadDirExif"][1],
            'sv3WritePicturesMessage'  : self.taskstate["WritePictures"][1],
            'sv4WriteBlogMessage'  : self.taskstate["WriteBlog"][1],
            'sv5BuildWebsiteMessage'  : self.taskstate["BuildWebsite"][1],
            'sv6FreezeMessage'  : self.taskstate["Freeze"][1],
            'sv7WriteCommitMessage'  : self.taskstate["WriteCommit"][1]
        }    

        with open(statemachine, 'w') as statefile:
            statefile.write('# This is an automatically generated file to track state for SpeciesViwer\n')
            state.write(statefile)
        return
    
    def stateOKtorun(self, statetotest):
        # all pre-req must be met
        for states in self.taskstate[statetotest][3]:
            if not self.taskstate[states][0]: 
                return False
        return True

    
    def updateState(self, statejustdone, ok, lastmessage):
        self.taskstate[statejustdone][0] = ok
        self.taskstate[statejustdone][1] = lastmessage
        return
    
