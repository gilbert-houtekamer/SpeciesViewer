# utility to determine which steps are due/possible
from urllib.parse import urlparse
import os
import logging
import configparser
from pathlib import Path

import ghexif
from ghexif import SVException

logger = logging.getLogger("speciesviewer")

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
    Projectname = None
    Version = '0.0'
    Taskstate = {}    
    
    def __init__(self, biotadb, pictdir, readState=True):
        if biotadb != None and biotadb != '':
            # references to the Web
            self.Project = Path(pictdir.replace('"', "").replace("'", "").replace('*','')).stem
            self.Directory = os.path.join(biotadb.replace('"', "").replace("'", ""), 'metadata/review/', self.Project)            
            if readState: 
                self.readState()
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

    # get status/task information for this task, normally run during init
    def readState(self):
        # key     task number
        # list 1: last run ok?
        # list 2: message from last run if even
        # list 3: list (numbers) that are permitted
        # list 4: list of tasks that are committed
        statemachine = os.path.join(self.Directory, "statemachine.ini")
        if os.path.exists(statemachine):
            # create a separete config instance
            state = configparser.ConfigParser()
            state.read(statemachine)
            # code state
            # selectspecies = state.get("SpeciesViewer","version")
            self.Taskstate["MakeRepository"] = [state.getboolean("state","sv1MakeRepository"), 
                            self.getUTF8(state, "sv1MakeRepositoryMessage"), 
                            ["ReadDirExif"], []]       
            self.Taskstate["ReadDirExif"] = [state.getboolean("state","sv2ReadDirExif"), 
                            self.getUTF8(state, "sv2ReadDirExifMessage"), 
                            ["ReadDirExif", "WritePictures"], ["MakeRepository"]]
            self.Taskstate["WritePictures"] = [state.getboolean("state","sv3WritePictures"), 
                            self.getUTF8(state,"sv3WritePicturesMessage"), 
                            ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite"], ["ReadDirExif"]]
            self.Taskstate["WriteBlog"] = [state.getboolean("state","sv4WriteBlog"),
                            self.getUTF8(state,"sv4WriteBlogMessage"), 
                            ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite"], ["ReadDirExif", "WritePictures"]]
            self.Taskstate["BuildWebsite"] = [state.getboolean("state","sv5BuildWebsite"),
                            self.getUTF8(state,"sv5BuildWebsiteMessage"), 
                            ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite", "Freeze"], ["ReadDirExif", "WritePictures"]]
            self.Taskstate["Freeze"] = [state.getboolean("state","sv6Freeze"), 
                            self.getUTF8(state,"sv6FreezeMessage"), 
                            ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite", "Freeze", "WriteCommit"],
                            ["ReadDirExif", "WritePictures", "BuildWebsite"]]
            self.Taskstate["WriteCommit"] = [state.getboolean("state","sv7WriteCommit"), 
                            self.getUTF8(state,"sv7WriteCommitMessage"), 
                            ["ReadDirExif", "WritePictures"], ["ReadDirExif", "WritePictures", "BuildWebsite", "Freeze"]]
        else:
            self.Taskstate = {"MakeRepository" : [True, "MakeRepository", 
                                                 ["ReadDirExif"], []],
                         "ReadDirExif" : [False, "This step has not yet been run.",  
                                               ["ReadDirExif", "WritePictures"], ["MakeRepository"]],
                         "WritePictures" : [False, "This step has not yet been run.",    
                                               ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite"], ["ReadDirExif"]],
                         "WriteBlog" : [False, "This step has not yet been run.",    
                                            ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite"], 
                                            ["ReadDirExif", "WritePictures"]],
                         "BuildWebsite" : [False, "This step has not yet been run.",    
                                               ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite", "Freeze"], 
                                               ["ReadDirExif", "WritePictures"]],
                         "Freeze" : [False, "This step has not yet been run.",    
                                        ["ReadDirExif", "WritePictures", "WriteBlog", "BuildWebsite", "Freeze", "WriteCommit"],
                                        ["ReadDirExif", "WritePictures", "BuildWebsite"]],
                         "WriteCommit" : [False, "This step has not yet been run.",    
                                              ["ReadDirExif", "WriteCommit"],
                                              ["ReadDirExif", "WritePictures", "BuildWebsite", "Freeze"]]
            }
        return self.Taskstate

    def writeState(self):
        # 
        statemachine = os.path.join(self.Directory, "statemachine.ini")
        # create a separete config instance
        state = configparser.ConfigParser()

        state['SpeciesViewer'] = {'version' : self.Version}
        state['state'] = {
            'sv1MakeRepository'  : True,
            'sv2ReadDirExif'  : self.Taskstate["ReadDirExif"][0],
            'sv3WritePictures'  : self.Taskstate["WritePictures"][0],
            'sv4WriteBlog'  : self.Taskstate["WriteBlog"][0],
            'sv5BuildWebsite'  : self.Taskstate["BuildWebsite"][0],
            'sv6Freeze'  : self.Taskstate["Freeze"][0],
            'sv7WriteCommit'  : self.Taskstate["WriteCommit"][0]
        }
        state['lastrunmessages'] = {
            'sv1MakeRepositoryMessage'  : "MakeRepository succeeded.",
            'sv2ReadDirExifMessage'  : self.Taskstate["ReadDirExif"][1],
            'sv3WritePicturesMessage'  : self.Taskstate["WritePictures"][1],
            'sv4WriteBlogMessage'  : self.Taskstate["WriteBlog"][1],
            'sv5BuildWebsiteMessage'  : self.Taskstate["BuildWebsite"][1],
            'sv6FreezeMessage'  : self.Taskstate["Freeze"][1],
            'sv7WriteCommitMessage'  : self.Taskstate["WriteCommit"][1]
        }    

        with open(statemachine, 'w') as statefile:
            statefile.write('# This is an automatically generated file to track state for SpeciesViewer\n')
            state.write(statefile)
        return
    
    # see if the state can run
    def stateOKtorun(self, statetotest):
        # all pre-req must be met
        for states in self.Taskstate[statetotest][3]:
            if not self.Taskstate[states][0]: 
                return False
        return True
    
    # we know that the step will fail, explain why and abort
    def explainAbort(self, statetotest):
        prereq = ""
        missingcount = 0
        # run through all pre-reqs
        for states in self.Taskstate[statetotest][3]:
            if not self.Taskstate[states][0]:
                missingcount += 1                
                if prereq == "":
                    prereq = states
                else:
                    prereq += ', ' + states
        # report results niceline
        prereq = ' and'.join(prereq.rsplit(',', 1))
        if missingcount == 1: 
            steps = 'step'
            need = 'needs'
        else:
            steps = 'steps'
            need = 'need'            
        raise SVException(f"Task {statetotest} cannot be run, the {steps} {prereq} {need} to be run first.")

    # update the state after the job is completed
    def updateState(self, statejustdone, ok, lastmessage):
        try:
            self.Taskstate[statejustdone][0] = ok
            self.Taskstate[statejustdone][1] = lastmessage
        except:
            pass  # test modules are not listed
        return
    
def getAllProjectStatus(MyProject, directory, silent=False):
    days_dict = {}
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.ini'):
                file_path = os.path.join(root, file)
                parseddir = root.split('\\')
                name = parseddir[1]
                if name == "": name = parseddir[0]
                statemachine = ghexif.statemachine.StateMachine(MyProject.getRepositoryPath(), name) #, readState=False)
                days_dict[name] = statemachine.Taskstate.copy()

    if not silent:
        # make summary report
        print(f"Summary of projects and progress for each report")     
        print(f"{'Project':15}")   
        for statemachine in days_dict:
            line = f"{statemachine:15}"
            for task in days_dict[statemachine]:
                if task != "MakeRepository":
                    if days_dict[statemachine][task][0]:
                        line += f" {task:15}"
                    else:
                        line += f" {'':15}"
            print(line)
    return days_dict