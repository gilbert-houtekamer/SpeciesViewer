# utility to determine which steps are due/possible
from urllib.parse import urlparse
import os
import logging
import configparser
from pathlib import Path

import engine
from engine.exceptions import SVException

logger = logging.getLogger("speciesviewer")

STATE_NAMES = [
    "Unknown",
    "MakeRepository",
    "ReadDirExif",
    "WritePictures",
    "WriteBlog",
    "Freeze",
    "WriteCommit"
]

# NOTE: "BuildWebsite" (sv5) was retired as a pipeline step — Freeze (sv6) already
# builds the website (makeWebDirectories), so a separate step was redundant. It no
# longer gates Freeze and is not tracked here. Old statemachine.ini files may still
# carry an sv5BuildWebsite key; it is simply ignored on read (read-tolerant).

# A step can finish cleanly having produced nothing, because the day holds no
# recognized species at all (JPGs but no determinations, and no landscape
# extras). That is neither a success nor a failure: the state is stored done=True
# — so downstream steps stay eligible — but with a message carrying this prefix,
# which the trip overview renders as 🚫 instead of ✅. Modules signal it by
# RETURNING such a message from main(); the GUI runners store it verbatim.
NOTHING_TO_DO_PREFIX = "Nothing to do: "


def nothing_to_do(reason):
    """Build the state message a batch module returns when it had nothing to do."""
    return NOTHING_TO_DO_PREFIX + reason


def is_nothing_to_do(message):
    """True for a state message written by a step that completed with nothing to do."""
    return isinstance(message, str) and message.startswith(NOTHING_TO_DO_PREFIX)


class StateMachine:
    # These MUST be per-instance. They used to be class attributes, and because
    # readState() assigns INTO Taskstate rather than rebinding it, every
    # StateMachine in the process shared one dict: reading day B's state
    # silently overwrote day A's, and the next writeState() for day A persisted
    # day B's values into day A's statemachine.ini. That is how a whole trip's
    # Write Blog column reverted to "Invalidated due to rerun of WritePictures"
    # — the value of the few days that had never been blogged, smeared over the
    # days that had just succeeded.
    def __init__(self, biotadb, pictdir, readState=True):
        self.Directory = None
        self.Projectname = None
        self.Version = '0.0'
        self.Taskstate = {}
        self.Project = None
        if biotadb != None and biotadb != '':
            # references to the Web
            self.Project = Path(pictdir.replace('"', "").replace("'", "").replace('*','')).stem
            self.Directory = os.path.join(biotadb.replace('"', "").replace("'", ""), 'metadata/review/', self.Project)            
            if readState: 
                self.readState()
        return
 
    def getUTF8(self, state, ansistring):
        # A missing message (a damaged or older file) reads as empty: the state
        # file must never stop SpeciesViewer from starting.
        workstr = state.get("lastrunmessages", ansistring, fallback="")
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
            # UTF-8 with the legacy fallback — files written before the fix in
            # writeState are cp1252 (utils/ini_io, the rule for every .ini).
            from utils.ini_io import read_ini
            read_ini(state, statemachine)
            # code state
            # selectspecies = state.get("SpeciesViewer","version")
            self.Taskstate["MakeRepository"] = [state.getboolean("state", "sv1MakeRepository", fallback=False), 
                            self.getUTF8(state, "sv1MakeRepositoryMessage"), 
                            ["ReadDirExif"], []]       
            self.Taskstate["ReadDirExif"] = [state.getboolean("state", "sv2ReadDirExif", fallback=False), 
                            self.getUTF8(state, "sv2ReadDirExifMessage"), 
                            ["ReadDirExif", "WritePictures"], ["MakeRepository"]]
            self.Taskstate["WritePictures"] = [state.getboolean("state", "sv3WritePictures", fallback=False),
                            self.getUTF8(state,"sv3WritePicturesMessage"),
                            ["ReadDirExif", "WritePictures", "WriteBlog"], ["ReadDirExif"]]
            self.Taskstate["WriteBlog"] = [state.getboolean("state", "sv4WriteBlog", fallback=False),
                            self.getUTF8(state,"sv4WriteBlogMessage"),
                            ["ReadDirExif", "WritePictures", "WriteBlog"], ["ReadDirExif", "WritePictures"]]
            self.Taskstate["Freeze"] = [state.getboolean("state", "sv6Freeze", fallback=False),
                            self.getUTF8(state,"sv6FreezeMessage"),
                            ["ReadDirExif", "WritePictures", "WriteBlog", "Freeze", "WriteCommit"],
                            ["ReadDirExif", "WritePictures"]]
            self.Taskstate["WriteCommit"] = [state.getboolean("state", "sv7WriteCommit", fallback=False),
                            self.getUTF8(state,"sv7WriteCommitMessage"),
                            ["ReadDirExif", "WritePictures"], ["ReadDirExif", "WritePictures", "Freeze"]]
        else:
            self.Taskstate = {"MakeRepository" : [True, "MakeRepository", 
                                                 ["ReadDirExif"], []],
                         "ReadDirExif" : [False, "This step has not yet been run.",  
                                               ["ReadDirExif", "WritePictures"], ["MakeRepository"]],
                         "WritePictures" : [False, "This step has not yet been run.",
                                               ["ReadDirExif", "WritePictures", "WriteBlog"], ["ReadDirExif"]],
                         "WriteBlog" : [False, "This step has not yet been run.",
                                            ["ReadDirExif", "WritePictures", "WriteBlog"],
                                            ["ReadDirExif", "WritePictures"]],
                         "Freeze" : [False, "This step has not yet been run.",
                                        ["ReadDirExif", "WritePictures", "WriteBlog", "Freeze", "WriteCommit"],
                                        ["ReadDirExif", "WritePictures"]],
                         "WriteCommit" : [False, "This step has not yet been run.",
                                              ["ReadDirExif", "WriteCommit"],
                                              ["ReadDirExif", "WritePictures", "Freeze"]]
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
            'sv6Freeze'  : self.Taskstate["Freeze"][0],
            'sv7WriteCommit'  : self.Taskstate["WriteCommit"][0]
        }
        state['lastrunmessages'] = {
            'sv1MakeRepositoryMessage'  : "MakeRepository succeeded.",
            'sv2ReadDirExifMessage'  : self.Taskstate["ReadDirExif"][1],
            'sv3WritePicturesMessage'  : self.Taskstate["WritePictures"][1],
            'sv4WriteBlogMessage'  : self.Taskstate["WriteBlog"][1],
            'sv6FreezeMessage'  : self.Taskstate["Freeze"][1],
            'sv7WriteCommitMessage'  : self.Taskstate["WriteCommit"][1]
        }    

        os.makedirs(self.Directory, exist_ok=True)
        # UTF-8 and atomic. The platform default (cp1252 on Windows) cannot hold
        # "▸"; a step message with one ("Choose where … File ▸ Settings") raised
        # halfway through the write, left a truncated file and took the app
        # down — and every start after that crashed reading it (2026-10-11).
        # Writing a temporary file and swapping it in means a failed write can
        # never leave half a file behind.
        tmp = statemachine + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as statefile:
            statefile.write('# This is an automatically generated file to track state for SpeciesViewer\n')
            state.write(statefile)
        # Dropbox may be reading the old file this instant: wait it out.
        from utils.atomic_replace import replace_with_retry
        replace_with_retry(tmp, statemachine)
        return
    
    def stateOKtorun(self, statetotest):
        # If WriteCommit is done, allow WriteBlog unconditionally
        if statetotest == "WriteBlog":
            if self.Taskstate.get("WriteCommit", [False])[0]:
                return True
            # Otherwise, check prerequisites
            for prereq in self.Taskstate.get("WriteBlog", [None, None, None, []])[3]:
                if not self.Taskstate.get(prereq, [False])[0]:
                    return False
            return True

        # Standard logic for all other steps
        if self.Taskstate.get("WriteCommit", [False])[0]:
            return False  # No other steps allowed after WriteCommit

        for prereq in self.Taskstate.get(statetotest, [None, None, None, []])[3]:
            if not self.Taskstate.get(prereq, [False])[0]:
                return False
        return True
    
    # we know that the step will fail, explain why and abort
    def explainAbort(self, statetotest, raiseSV=True):
        # Blocked because the day is already committed: after WriteCommit every
        # step except WriteBlog is locked (see stateOKtorun), and WriteCommit
        # itself can't be re-run. Say that, instead of the bogus empty-list
        # "the steps  need to be run first" message this produced otherwise.
        if statetotest != "WriteBlog" and self.Taskstate.get("WriteCommit", [False])[0]:
            msg = (f"Task {statetotest} cannot be run: this day is already "
                   f"committed (frozen after Write Commit).")
            if raiseSV:
                raise SVException(msg)
            return msg
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
        if raiseSV:    
            raise SVException(f"Task {statetotest} cannot be run, the {steps} {prereq} {need} to be run first.")
        else:
            return f"Task {statetotest} cannot be run, the {steps} {prereq} {need} to be run first."

    # update the state after the job is completed
    # also disable steps that must be run again as previous step is now (potentially) invalid
    #
    # invalidate_downstream=False suppresses the cascade: when a step is re-run
    # but its output is byte-identical to before (the EXIF cache makes
    # re-reading cheap, so a no-op re-read of Read/Summarize is common), the
    # caller can keep downstream Blog/Website/Freeze states intact instead of
    # forcing the whole chain to redo. The step's OWN state is always updated.
    def updateState(self, statejustdone, ok, lastmessage, invalidate_downstream=True):
        try:
            self.Taskstate[statejustdone][0] = ok
            self.Taskstate[statejustdone][1] = lastmessage
        except:
            pass  # test modules are not listed

        # Invalidate downstream steps if needed
        invalidation_map = {
            "ReadDirExif": ["WritePictures", "WriteBlog", "Freeze"],
            "WritePictures": ["WriteBlog", "Freeze"],
        }

        if invalidate_downstream and statejustdone in invalidation_map:
            for step in invalidation_map[statejustdone]:
                if step in self.Taskstate:
                    self.Taskstate[step][0] = False
                    self.Taskstate[step][1] = "Invalidated due to rerun of " + statejustdone

        self.writeState()
        return
    
    # return the return string from task that was (just or last) created
    # return True for "MakeRepository", since it passes no return code (yet?)
    def getState(self, statejustdone):
        try:
            if self.Taskstate[statejustdone] == "MakeRepository":
                return True, ""
            if not self.Taskstate[statejustdone][0]:
                return False, self.Taskstate[statejustdone][1]
        except: 
            pass
        return True, "" # test modules are not listed, return True


    
def getAllProjectStatus(MyProject, directory, silent=False):
    days_dict = {}
    directory = os.path.normpath(directory)
    # A day's state lives at <review>/<day>/statemachine.ini — always exactly one
    # level deep, because writeState() writes it to the day directory. This used
    # to os.walk() the whole review tree, which descends every day's frozen/
    # website output (5,512 dirs / 5,897 files in the largest trip) to find a
    # handful of .ini files one level down: ~285 ms per call, and growing with
    # the size of the PUBLISHED SITE rather than the number of days. Scan one
    # level instead. readProject(getEarlierDays=True) calls this on every batch
    # step, so it is on the hot path of the whole pipeline.
    try:
        day_dirs = [e for e in os.scandir(directory) if e.is_dir()]
    except OSError:
        day_dirs = []
    for entry in day_dirs:
        try:
            # Keep the old "any .ini in the day directory" test rather than
            # requiring statemachine.ini: a day with some other .ini but no
            # state file still gets a default (all-False) entry, so readProject
            # keeps treating it as uncommitted and reads its CSVs.
            if not any(f.endswith('.ini') for f in os.listdir(entry.path)):
                continue
        except OSError:
            continue
        statemachine = StateMachine(MyProject.getRepositoryPath(), entry.name)
        days_dict[entry.name] = statemachine.Taskstate.copy()
    # Sort by day name so iteration is chronological
    days_dict = dict(sorted(days_dict.items()))

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