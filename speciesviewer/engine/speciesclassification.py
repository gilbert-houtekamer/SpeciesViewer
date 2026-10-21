import os
import io
import csv
import time
import logging
from pygbif import species

from engine import COLUMN_NAMES, SELECTED_FIELDS, SPECIES_FIELDS
import engine
import engine.projectlib

from enum import Enum, auto

logger = logging.getLogger("speciesviewer")

# Column order of speciescollection.csv. One definition, used by the full
# writer and the appender alike — an appended row whose columns disagree with
# the header would be silently misread.
SC_HEADERS = ['Level', 'ScientificName', 'DutchName', 'EnglishName', 'Up', 'Leaves']
# A converted species list (ch.6) adds these at the end: the sort number among
# siblings, and for a subspecies-beside-its-species the species it belongs to.
SC_OPTIONAL = ['Sequence', 'SubspeciesOf']


# The alias table beside a converted speciescollection.csv (ch.6).
ALIAS_FILE = 'speciesnames.csv'
# On a converted list these classes come from their own list only (AviList,
# MDD) — GBIF is never asked to add one.
AUTHORITY_CLASSES = {'aves': 'AviList', 'mammalia': 'MDD'}


def _fileHeaders(path):
    """The column names on the first line of an existing collection file."""
    with open(path, encoding='utf-8', newline="") as f:
        first = f.readline().strip()
    return first.split(';') if first else list(SC_HEADERS)


# Names GBIF has no answer for, shared by every SpeciesClassification in this
# process. Per-instance it was near-useless: the GUI builds a fresh
# classification per viewer session, so one invalid name — and the GUI allows
# invalid names on purpose — cost a network round trip again and again. Not
# persisted between runs: a name GBIF learns later must not stay unknown
# forever, and a run is long enough to be worth it.
_NAMES_NOT_FOUND = set()

# Seconds to wait on any GBIF call. pygbif passes **kwargs straight to
# requests.get, which has NO timeout of its own — so without this a network that
# accepts the connection but never answers (hotel/campground wifi, a captive
# portal) hangs the run forever instead of failing. A refused/unroutable network
# already fails fast; this covers the half-open case. On timeout the caller
# flags the run offline and stops looking names up, so one wait is the most this
# ever costs.
GBIF_TIMEOUT = 15


def _endsWithNewline(speciesfilename):
    """False when the file's last line has no line ending — appending onto it
    would glue the new row onto the old one and corrupt both."""
    try:
        with io.open(speciesfilename, "rb") as f:
            if f.seek(0, os.SEEK_END) == 0:
                return True
            f.seek(-1, os.SEEK_END)
            return f.read(1) in (b"\n", b"\r")
    except OSError:
        return True


def appendRowsToSpeciesCollection(speciesfilename, rows):
    """Append already-shaped CSV rows to a collection file. For callers that
    hold rows rather than a loaded tree — the Edit Species List dialog mirroring
    an edit into a trip's copy. A later row wins on read, so this updates."""
    rows = [r for r in rows if r]
    if not rows or not os.path.exists(speciesfilename):
        return 0
    with io.open(speciesfilename, "a", newline="", encoding='utf-8') as csvfile:
        if not _endsWithNewline(speciesfilename):
            csvfile.write("\r\n")
        writer = csv.DictWriter(csvfile, fieldnames=SC_HEADERS,
                                quoting=csv.QUOTE_MINIMAL, delimiter=';',
                                extrasaction='ignore')
        for row in rows:
            writer.writerow(row)
    logger.info(f"Appended {len(rows)} edited species row(s) to {speciesfilename}.")
    return len(rows)


def namesInSpeciesCollection(speciesfilename):
    """The ScientificNames already in a collection file, without building a
    whole SpeciesClassification. Splits on ';' exactly like
    readSpeciesCollection, so it sees the same names the reader will."""
    names = set()
    try:
        with io.open(speciesfilename, mode="r", newline="", encoding='utf-8') as f:
            f.readline()                      # header
            for line in f:
                parts = line.strip().split(';')
                if len(parts) > 1 and parts[1]:
                    names.add(parts[1])
    except OSError as e:
        logger.warning(f"Could not read species collection {speciesfilename}: {e}")
    return names


def _flatten_name_backbone(r):
    """Adapt pygbif 0.6.6's nested name_backbone response back to the flat dict
    this module was written against. The GBIF match API changed BOTH the request
    parameter (name -> scientificName) and the response shape: matchType /
    confidence now live under 'diagnostics', and the taxonomy under a
    'classification' list of {rank, name, key}. We rebuild kingdom/phylum/class/
    order/family/genus/species (+ their <rank>Key for vernacular lookups),
    canonicalName, matchType and confidence so addSpecies works unchanged."""
    if not isinstance(r, dict):
        return {}
    diag = r.get("diagnostics") or {}
    usage = r.get("usage") or {}
    flat = {
        "matchType":     diag.get("matchType"),
        "confidence":    diag.get("confidence", 0),
        "canonicalName": usage.get("canonicalName", ""),
        "synonym":       r.get("synonym", False),
    }
    rankmap = {"KINGDOM": "kingdom", "PHYLUM": "phylum", "CLASS": "class",
               "ORDER": "order", "FAMILY": "family", "GENUS": "genus",
               "SPECIES": "species"}
    for c in r.get("classification") or []:
        name = rankmap.get((c.get("rank") or "").upper())
        if name:
            flat[name] = c.get("name", "")
            flat[name + "Key"] = c.get("key", "")
    return flat


# Biota, Kingdom, Phylum, Class, Order, Family, Species
class Level(Enum):
    SPECIES = auto()
    FAMILY = auto()
    ORDER = auto()
    CLASS = auto()
    PHYLUM = auto()
    KINGDOM = auto()
    BIOTA = auto()
    # Non-taxonomic "topics" branch — see engine/topics.py. Appended last so the
    # taxonomic members keep their .value (ClassificationGroupName indexing).
    TOPICS = auto()
    TOPIC = auto()
    TOPICLEAF = auto()


class SpeciesClassification:
    sc_dict = {}
    dutch_dict = {}         # all species we have the Dutch name for, points to scientific name
    english_dict = {}       # all species we have the English name for, points to scientific name

    DUMMYSPECIES = ['vogel', 'vogels', 'bird', 'libel', 'libellen', 'onbekend', 
                    'rups', 'panama', 'californie', 'onbekend', 'sprinkhaan', 'turkey',
                    'blog', 'goodidpicture',
                    'juvenile', 'juv.', 'ímmature', 'imm.', 'onvolwassen',  'adult', 'volwassen', 
                    'male', 'man', 'female', 'vrouw', 'couple', 'paar', 'paartje', 
                    'summer', 'zomer', 'winter', 'breeding', 'non-breeding']

    # Marker keywords for non-species "blog extras": landscapes, context shots,
    # the birding group, etc. A photo carrying one of these (and no resolvable
    # species) belongs in the day blog but has no taxon. See isBlogExtra().
    LANDSCAPE_KEYWORDS = {'landscape', 'landschap'}

    @classmethod
    def _subject_tokens(cls, subject):
        """Split a stored XMP:Subject into lowercase tokens. Handles both a
        bare keyword ("landschap") and a list-repr ("['robin', 'landscape']")."""
        s = str(subject).strip()
        if s.startswith('[') and s.endswith(']'):
            s = s[1:-1]
        return [t.strip().strip("'\"").lower() for t in s.split(',') if t.strip()]

    @classmethod
    def isBlogExtra(cls, subject):
        """True if the subject marks a non-species blog extra (landscape /
        context photo)."""
        if not subject:
            return False
        return any(t in cls.LANDSCAPE_KEYWORDS for t in cls._subject_tokens(subject))


    def __init__(self, MyProject):
        # Per-instance state. These were declared at class level above, so in the
        # long-lived GUI process they were shared across every batch run and
        # persisted stale species/counters (fine pre-GUI, one process per job).
        # Reset per instance; DUMMYSPECIES / LANDSCAPE_KEYWORDS stay class-level
        # (they are constants).
        self.sc_dict = {}
        self.dutch_dict = {}
        self.english_dict = {}
        self.alias_dict = {}      # old/other name -> scientific name (ch.6, speciesnames.csv)
        self.contested = set()    # old names a split gave to another species: not used
        self.converted = False    # True when the list carries Sequence (ch.6 conversion)
        # Species learned since this object was built, buffered rather than
        # written per species: flushSpeciesLearned() appends them in one go
        # when the viewer or batch run ends. Losing the buffer to a crash only
        # costs the lookups again next run.
        self.learned = set()
        if MyProject != None:
            MyProject.registerSpeciesClassification(self)
        return
    
    def clearDict(self):
        self.sc_dict = {}
        # Anything learned refers to the tree just discarded; keeping it would
        # append rows this object can no longer produce.
        self.learned = set()

    def scCopy(self):
        return self.sc_dict.copy()

    def createSpeciesRecord(self, level, scientificname, dutchname, englishname, upname):
        sc_record = {
            "Level": level, 
            "ScientificName": scientificname,
            "DutchName": dutchname,
            "EnglishName": englishname,
            "Up": upname,
            "Leaves": []
        }
        return sc_record    
 
    def readSpeciesCollection(self, speciesfilename, donotaddLanguageIndices=False):
        # Reads a CSV file line by line, splits by delimiter, and creates dictionaries.
        # Assumes the first row contains column headers.

        with io.open(speciesfilename, mode="r", newline="", encoding='utf-8') as csvfile:
            # Read the first line (header) to get column names
            header = csvfile.readline().strip().split(';')
            # column that needs special treatment
            list_columns = ['Leaves']

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
                elif level == "Level.TOPICS":
                    row_dict['Level'] = Level.TOPICS
                elif level == "Level.TOPIC":
                    row_dict['Level'] = Level.TOPIC
                elif level == "Level.TOPICLEAF":
                    row_dict['Level'] = Level.TOPICLEAF


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

                self.sc_dict[row_dict['ScientificName']] = row_dict

        # Remember which file this tree came from. Without it, a caller holding
        # a classification cannot say where to write it back, and the species
        # editor's flush silently reached only the master — leaving the trip's
        # copy behind, so the very next pipeline step looked the species up in
        # GBIF again.
        self._source_path = speciesfilename

        logger.debug(f"Read specification file, now create links.")
        # The Leaves column on disk is NOT authoritative: every parent's Leaves
        # list is rebuilt here from the children's Up pointers. That is what
        # makes appending to the file safe (see appendSpeciesCollection).
        #
        # One try per row, deliberately. This loop used to be wrapped in a
        # single try/except, so one row pointing at an Up that was not in the
        # file stopped the linking for EVERY remaining species — leaving a
        # half-linked tree behind one log line. A bad row must cost that row.
        broken = []
        for speciesname in self.sc_dict:
            try:
                species = self.sc_dict[speciesname]
                ScientificName = species['ScientificName']
                UpName = species['Up']
                if UpName != "" and ScientificName not in self.sc_dict[UpName]['Leaves']:
                    self.sc_dict[UpName]['Leaves'].append(ScientificName)

                if not donotaddLanguageIndices:
                    # add Nederlandse naam index
                    dutchname = species['DutchName']
                    if dutchname != "":
                        if dutchname not in self.dutch_dict:
                            self.dutch_dict[dutchname] = ScientificName

                    # add Nederlandse naam index
                    englishname = species['EnglishName']
                    if englishname != "":
                        if englishname not in self.english_dict:
                            self.english_dict[englishname] = ScientificName
            except Exception as e:
                broken.append(f"{speciesname} ({e})")

        if broken:
            shown = ", ".join(broken[:10]) + (" …" if len(broken) > 10 else "")
            logger.error(f"{len(broken)} row(s) in {speciesfilename} have an "
                         f"inconsistent reference and were not linked: {shown}")
        logger.debug(f'Read {len(self.sc_dict)} predefined species from {speciesfilename}.')
        self.converted = any('Sequence' in r for r in self.sc_dict.values())
        self._readAliases(os.path.join(os.path.dirname(speciesfilename), ALIAS_FILE))
        return

    def _readAliases(self, path):
        """The alias table of a converted list (ch.6): an old scientific name, an
        older or other Dutch/English name -> the scientific name it means now.
        A name that points at two species is refused, never guessed: better
        unresolved than filed under the wrong bird. A name that is itself a taxon
        keeps its own meaning."""
        self.alias_dict = {}
        self.contested = set()
        if not os.path.exists(path):
            return
        targets = {}
        indices = {'nl': self.dutch_dict, 'en': self.english_dict}
        with open(path, encoding='utf-8', newline="") as f:
            for row in csv.DictReader(f, delimiter=';'):
                name = (row.get('Name') or "").strip().lower()
                to = (row.get('PointsTo') or "").strip().lower()
                if name and to in self.sc_dict and name not in self.sc_dict:
                    targets.setdefault(name, set()).add(to)
                # CONTESTED: an old name that a split handed to the OTHER half —
                # "mangrovereiger" meant Butorides striata, IOC 15.2 gives it to
                # B. atricapilla. A keyword with it is then neither: it resolves
                # to nothing, so the species step asks (decided 2026-10-17).
                index = indices.get((row.get('Language') or "").strip().lower())
                if index is not None and name in index and index[name].strip().lower() != to:
                    self.contested.add(name)
        for name in self.contested:
            self.dutch_dict.pop(name, None)
            self.english_dict.pop(name, None)
            targets.pop(name, None)
        # Both are facts about the species LIST, the same on every load, so they
        # go to the debug log. What the user needs is a picture whose keyword is
        # one of them — projectlib says that per picture (contested_note).
        if self.contested:
            logger.debug(f"{len(self.contested)} name(s) now mean another species than before "
                         f"and are not used: {', '.join(sorted(self.contested))}")
        refused = []
        for name, tos in targets.items():
            if len(tos) == 1:
                self.alias_dict[name] = next(iter(tos))
            else:
                refused.append(f"{name} ({' / '.join(sorted(tos))})")
        logger.debug(f"Read {len(self.alias_dict)} alias(es) from {path}"
                     + (f"; refused, pointing at two species: {', '.join(sorted(refused))}."
                        if refused else "."))

    # save last version, always save last version under .csv_old  
    def _csvRow(self, name):
        spec_dict = self.sc_dict[name]
        row = {"Level":          spec_dict['Level'],
               "ScientificName": spec_dict['ScientificName'],
               "DutchName":      spec_dict['DutchName'],
               "EnglishName":    spec_dict['EnglishName'],
               "Up":             spec_dict['Up'],
               "Leaves":         spec_dict['Leaves']}
        for col in SC_OPTIONAL:        # a converted list (ch.6) carries these too
            if col in spec_dict:
                row[col] = spec_dict[col]
        return row

    def _headers(self):
        """SC_HEADERS, plus the optional columns when this list has them — so a
        rewrite never drops the sort number (ch.6)."""
        return SC_HEADERS + [c for c in SC_OPTIONAL
                             if any(c in r for r in self.sc_dict.values())]

    def writeSpeciesCollection(self, speciesfilename):
        # Write Picture CSV file
        logger.debug(f'Write {len(self.sc_dict)} updated species data CSV file "{speciesfilename}".')
        with open(speciesfilename, "w", newline="", encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=self._headers(), restval="",
                                    quoting=csv.QUOTE_MINIMAL, delimiter=';')
            writer.writeheader()
            for species in self.sc_dict:
                writer.writerow(self._csvRow(species))
        return

    # ------------------------------------------------------------------
    # Appending, instead of rewriting the whole 2 MB file.
    #
    # Safe because readSpeciesCollection rebuilds every parent's Leaves from
    # the children's Up pointers: no existing row has to change when a species
    # is added, only its own row and any ancestor rows not yet in the file.
    # A later duplicate wins on read, so an appended row also serves to update
    # one (e.g. a vernacular name filled in afterwards).
    #
    # Matters because the master collection lives in Dropbox, and because the
    # rewrite path (updateSpeciesCollection) has a window in which neither the
    # file nor its .old backup is complete.
    # ------------------------------------------------------------------
    def ancestryOf(self, name):
        """`name` and its ancestors, topmost first. Stops at an unknown or
        cyclic Up rather than looping."""
        chain = []
        seen = set()
        cur = name
        while cur and cur in self.sc_dict and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = (self.sc_dict[cur].get('Up') or "").strip()
        chain.reverse()
        return chain

    def appendSpeciesCollection(self, speciesfilename, names):
        """Append `names` (with any missing ancestors, ancestors first) to an
        existing collection file. Returns the list of names actually written.

        Falls back to a full write when the file does not exist yet — there is
        nothing to append to, and a headerless fragment would be unreadable."""
        wanted = [n for n in names if n in self.sc_dict]
        if not wanted:
            return []
        if not os.path.exists(speciesfilename):
            self.writeSpeciesCollection(speciesfilename)
            return wanted

        present = namesInSpeciesCollection(speciesfilename)
        to_write = []
        for name in wanted:
            for step in self.ancestryOf(name):
                # An ancestor already on file is left alone; the species itself
                # is written even when present, so an enrichment lands too.
                if step in to_write:
                    continue
                if step in present and step != name:
                    continue
                to_write.append(step)
        if not to_write:
            return []

        with open(speciesfilename, "a", newline="", encoding='utf-8') as csvfile:
            if not _endsWithNewline(speciesfilename):
                csvfile.write("\r\n")
            # the file's own columns, so an appended row lines up with its header
            writer = csv.DictWriter(csvfile, fieldnames=_fileHeaders(speciesfilename),
                                    restval="", extrasaction="ignore",
                                    quoting=csv.QUOTE_MINIMAL, delimiter=';')
            for name in to_write:
                writer.writerow(self._csvRow(name))
        logger.info(f"Appended {len(to_write)} species row(s) to {speciesfilename}.")
        return to_write

    def flushSpeciesLearned(self, paths):
        """Append everything learned this session to each collection file, once.
        Returns {path: [names written]}.

        Pass the MASTER first. On failure the exception propagates with the
        buffer intact, so nothing has been half-written and the caller can
        retry or report; a partial success followed by a retry only re-appends
        rows, which a later-wins read absorbs.
        """
        if not self.learned:
            return {}
        names = sorted(self.learned)
        written = {}
        for path in paths:
            if path:
                written[path] = self.appendSpeciesCollection(path, names)
        self.learned = set()
        return written

    # merge both SpeciesCollections
    def mergeSpeciesCollections(self, newSC):
        for species in newSC:
            if species in self.sc_dict: continue
            speciesobject = newSC[species] 
            self.sc_dict[species] = speciesobject
    
        return


    # keep previous version and latest
    def updateSpeciesCollection(self, speciesfilename):
        if os.path.exists(speciesfilename + 'old'):  
            os.remove(speciesfilename + 'old')
        if os.path.exists(speciesfilename):  
            os.rename(speciesfilename, speciesfilename + 'old')
        # make new file with latest additions
        self.writeSpeciesCollection(speciesfilename)

    # getCommonVernacularNames: Process all languages, and sort the names based on frequency.
    # Some species have many names, like 
    # Engels: common stork's-bill 
    #           ["common stork's-bill", 'alfilaria', 'common crowfoot', 'common erodium', 
    #            "common heron's-bill", "heron's-bill", 'red-stem filaree', "stork's-bill"]
    # NL:     duinreigersbek ['duinreigersbek', 'gewone reigersbek', 'reigersbek']
    #
    # Parameters:
    #   soort:    a dictionary describing the species from the GBIF (backbone) data
    #   rankname: the rank we are looking, eg kingdom, class or species
    #             This specifie where to look
    # returns:
    #   The getCommonVernacularNames function returns the complete structure,
    #   the names by language in descending frequency
    #
    def getCommonVernacularNames(self, soort, rankname):
        language_counts  = {}
        key = rankname + 'Key'
        vernacularNames = species.name_usage(key=soort[key], data='vernacularNames', rank=rankname,
                                           timeout=GBIF_TIMEOUT)

        # proposed from CoPilot ok
        for v in vernacularNames['results']:
            # Update the dictionary with the species name and language
            language_name = v['language']
            if language_name not in language_counts:
                language_counts[language_name] = []
            language_counts[language_name].append(v["vernacularName"].lower())

        # Sort species lists by occurrence count (descending)
        # it gets tricky for CoPilot :-).  But together we make this 
        language_totals  = {}
        for l in language_counts:
            language_totals[l] = []
            #print(f"for {l} we have")
            name_counts = {}
            for name in language_counts[l]:
                name_counts[name] = name_counts.get(name, 0) + 1

            sorted_names = sorted(name_counts.items(), key=lambda x: (-x[1], x[0]))
            unique_names = [name for name, _ in sorted_names]
            for name in unique_names:
                language_totals[l].append(name)
            # print(f"sorted_names={sorted_names}\n unique_names={unique_names}")

        return language_totals

    # getNldEngVernacularNames: Extract the most often referenced English and Dutch 
    #                           names from getCommonVernacularNames
    # 
    # Engels: common stork's-bill 
    #           ["common stork's-bill", 'alfilaria', 'common crowfoot', 'common erodium', 
    #            "common heron's-bill", "heron's-bill", 'red-stem filaree', "stork's-bill"]
    # NL:     duinreigersbek
    #           ['duinreigersbek', 'gewone reigersbek', 'reigersbek']
    #
    # Parameters:
    #   soort:    a dictionary describing the species from the GBIF (backbone) data
    #   rankname: the rank we are looking, eg kingdom, class or species
    #             This specifie where to look
    # returns:
    #   The English and Dutch names
    #
    def getNldEngVernacularNames(self, soort, rankname):
        try:
            names = self.getCommonVernacularNames(soort, rankname)
            nederlands = names.get('nld', [''])[0]
            engels = names.get('eng', [''])[0]
        except:
            nederlands = ""
            engels = ""
        return nederlands, engels

    # Call createSpeciesRecord to add entry, and add uplink and child in up
    def addSpeciesLevel(self, level, scientificname, dutchname, englishname, upname):
        # Every insertion into the tree passes through here, so this is the one
        # place that can honestly answer "what did we learn this session?".
        # Enrichments are recorded too: a later row wins on read, so appending
        # the updated row is also how a filled-in vernacular name is persisted.
        if scientificname not in self.sc_dict:
            # create it
            self.sc_dict[scientificname] = self.createSpeciesRecord(level, scientificname, dutchname, englishname, upname)
            self.learned.add(scientificname)
        else:
            # maybe we get more info
            if dutchname != "" and self.sc_dict[scientificname]['DutchName'] == "":
                self.sc_dict[scientificname]['DutchName'] = dutchname
                self.learned.add(scientificname)
            if englishname != "" and self.sc_dict[scientificname]['EnglishName'] == "":
                self.sc_dict[scientificname]['EnglishName'] = englishname
                self.learned.add(scientificname)

        # save as a child if up is defined
        if upname != "" and scientificname not in self.sc_dict[upname]['Leaves']:            
            self.sc_dict[upname]['Leaves'].append(scientificname)        
        return

    def getVernacularNames(self, soort, key):
        engels = "" 
        nederlands = ""
        vernacularNames = species.name_usage(key=soort[key], data='vernacularNames',
                                           timeout=GBIF_TIMEOUT)
        for v in vernacularNames['results']:
    #       print(f'{v["vernacularName"]} in taal {v["language"]}')
            if v["language"] == "eng" and engels == "":
                engels = v["vernacularName"]
            if v["language"] == "nld" and nederlands == "":
                nederlands = v["vernacularName"]
            if engels != "" and nederlands != "":
                break
        return engels.lower(), nederlands.lower()

    # add species based on metadata from GBIF
    def addSpecies(self, gbif):
        # A converted list takes birds from AviList and mammals from MDD only:
        # GBIF never adds one (its names and families differ, and it lags splits).
        source = AUTHORITY_CLASSES.get((gbif.get('class') or "").lower())
        if getattr(self, "converted", False) and source:
            logger.warning(f"'{gbif.get('canonicalName', '')}' is not in this collection's {source} "
                           f"list; GBIF is not used for this group. Check the spelling, or add "
                           f"the name to the species list's aliases.")
            return ""
        namen = self.getNldEngVernacularNames(gbif, 'kingdom') if 'kingdomKey' in gbif else ("", "")
        self.addSpeciesLevel(Level.KINGDOM, gbif['kingdom'].lower(), namen[0], namen[1], 'biota')
        namen = self.getNldEngVernacularNames(gbif, 'phylum') if 'phylumKey' in gbif else ("", "")
        self.addSpeciesLevel(Level.PHYLUM, gbif['phylum'].lower(), namen[0], namen[1], gbif['kingdom'].lower())
        namen = self.getNldEngVernacularNames(gbif, 'class') if 'classKey' in gbif else ("", "")    
        self.addSpeciesLevel(Level.CLASS, gbif['class'].lower(), namen[0], namen[1], gbif['phylum'].lower())
        ordernames = self.getNldEngVernacularNames(gbif, 'order') if 'orderKey' in gbif else ("", "")      
        namen = self.getNldEngVernacularNames(gbif, 'family') if 'familyKey' in gbif else ("", "")
        try:
            self.addSpeciesLevel(Level.ORDER, gbif['order'].lower(), ordernames[0], ordernames[1], gbif['class'].lower())
            self.addSpeciesLevel(Level.FAMILY, gbif['family'].lower(), namen[0], namen[1], gbif['order'].lower())
        except:
            try: 
                print(f"Will link Family to Class because there is no ORDER known for {gbif['canonicalName']}.")       
                self.addSpeciesLevel(Level.FAMILY, gbif['family'].lower(), namen[0], namen[1],  gbif['class'].lower())
            except:
                print(f"No ORDER or FAMILY for {gbif['canonicalName']}, that is weird")
        namen = self.getNldEngVernacularNames(gbif, 'species') if 'speciesKey' in gbif else ("", "")            
        self.addSpeciesLevel(Level.SPECIES, gbif['canonicalName'].lower(), namen[0], namen[1],  gbif['family'].lower())

        return gbif['canonicalName'].lower()

    # look for names in all three tables
    # add some logic to massage species
    @staticmethod
    def _normalize_subspecies(name):
        """Strip taxonomic qualifiers (ssp, subsp., var.) from species names."""
        parts = name.split()
        cleaned = [p for p in parts if p not in ('ssp', 'ssp.', 'subsp', 'subsp.', 'var', 'var.')]
        return ' '.join(cleaned)

    def getKnownSpecies(self, subject, nested, LookUpInGBIF=False, fuzzy=False):
        scientificname = ''
        if LookUpInGBIF:
            timesoort = time.time()
        if subject == "" or subject in self.DUMMYSPECIES or subject in self.LANDSCAPE_KEYWORDS:
            return ""
        elif subject in self.sc_dict:
            return self.sc_dict[subject]['ScientificName']
        elif subject in self.dutch_dict:
            return self.dutch_dict[subject]
        elif subject in self.english_dict:
            return self.english_dict[subject]
        elif subject in getattr(self, "alias_dict", {}):
            return self.alias_dict[subject]
        elif LookUpInGBIF and not getattr(self, "_gbif_offline", False) and (',' not in subject):
            # Normalize subspecies qualifiers before lookup
            normalized = self._normalize_subspecies(subject)
            if normalized != subject and normalized in self.sc_dict:
                return normalized
            try:
                # check if we already searched for this species
                if subject not in _NAMES_NOT_FOUND:
                    # pygbif 0.6.6: param is scientificName (not name), and the
                    # response is nested — flatten it back to the old flat shape.
                    gbif = _flatten_name_backbone(species.name_backbone(scientificName=subject,
                                                                 timeout=GBIF_TIMEOUT))
                    if not fuzzy: # batch  mode
                        if gbif['matchType'] == 'EXACT' and gbif.get('species'):
                            scientificname = self.addSpecies(gbif).lower()
                            timesoort = time.time() - timesoort
                            logger.info(f'The species "{gbif["canonicalName"].lower()}" is found in the GBIF database in {timesoort:.3f} seconds, class {gbif["class"]} in {gbif["kingdom"]}.')
                        else:
                            timesoort = time.time() - timesoort
                            logger.info(f"""The species "{subject}" is not found in the GBIF database as a scientific name, took {timesoort:.3f} seconds.""")
                            # Only blacklist when GBIF found NOTHING at all. A
                            # FUZZY match GBIF DID offer here (just rejected by
                            # strict batch-mode rules) must stay retryable by a
                            # later interactive fuzzy=True call in this same
                            # process — the guard above wraps the WHOLE lookup,
                            # so blacklisting a typo here silently blocked every
                            # later "Validate"/"Check online" attempt for it too,
                            # even though GBIF would have confidently corrected it.
                            if not (gbif.get('matchType') == 'FUZZY' and gbif.get('species')):
                                _NAMES_NOT_FOUND.add(subject)
                    else: # GUI
                        # The batch branch above reassigns timesoort to the
                        # elapsed time; this one never did, so it logged the
                        # clock (…"in 1786181395.030 seconds").
                        elapsed = time.time() - timesoort
                        if gbif['matchType'] in ['EXACT', 'FUZZY'] and gbif.get('species'):
                            if gbif['confidence'] >= 90:
                                scientificname = self.addSpecies(gbif).lower()
                                logger.info(f"'{subject}' matched GBIF as '{gbif['canonicalName']}' with confidence {gbif['confidence']} ({gbif['matchType']}) in {elapsed:.3f} seconds")
                            else:
                                logger.info(f"'{subject}' matched GBIF fuzzily but with low confidence ({gbif['confidence']}) in {elapsed:.3f} seconds")
                        else:
                            logger.info(f"'{subject}' not found in GBIF with FUZZY or EXACT matching.")
                    # Cache the original input so repeated lookups don't hit GBIF again
                    if scientificname and subject != scientificname and subject not in self.sc_dict:
                        self.sc_dict[subject] = self.sc_dict[scientificname]
            except Exception as e:
                # Report the REAL error instead of mislabeling everything as a
                # timeout (a pygbif/GBIF-API change once hid here for ages as a
                # fake "timeout"). Log the actual type+message once per run.
                import requests as _requests
                # A raised lookup is NOT the same as "GBIF has no such name":
                # count it so the caller can avoid telling the user to fix a
                # spelling that was never actually checked.
                self._gbif_lookup_errors = getattr(self, "_gbif_lookup_errors", 0) + 1
                if not getattr(self, "_gbif_error_logged", False):
                    self._gbif_error_logged = True
                    logger.warning(f"GBIF lookup failed for '{subject}' "
                                   f"({type(e).__name__}: {e}).")
                # Only a genuine connectivity failure justifies giving up on
                # every remaining name for this run.
                if isinstance(e, (_requests.exceptions.ConnectionError,
                                  _requests.exceptions.Timeout)):
                    self._gbif_offline = True
                    logger.warning("Network/GBIF unreachable — skipping online "
                                   "species lookups for the rest of this run.")
                # Don't retry the same name again this run regardless.
                _NAMES_NOT_FOUND.add(subject)
        elif not nested:
            # likely a list of variables, decode it
            if len(subject.split(','))>1:
                subject = subject.strip('[]')
                #print(f"{subject.split(',')}")
                for subjects in subject.split(','):
                    subjects = subjects.strip().strip("'\"")
                    if not subjects:
                        continue
                    # categories are interpreted as Aves etc, avoid this
                    if subjects.lower() not in self.DUMMYSPECIES and subjects.lower() not in self.LANDSCAPE_KEYWORDS:
                        scientificname = self.getKnownSpecies(subjects, True, LookUpInGBIF)
                        if scientificname != "":
                            # if self.SpeciesDebug > 8:
                            #     print(f"In {subject.split(',')} - Found {subjects} -> {scientificname}")
                            return scientificname
            
        return scientificname

    # See if we recognize the species from either languages, if not call GBIF
    def contested_note(self, rawsubject) -> str:
        """Why a keyword resolves to nothing although it is a well-known name:
        a split gave it to another species (ch.6), so it no longer says which
        one is meant. "" when that is not the reason."""
        name = (rawsubject or "").strip().lower()
        if name in self.contested:
            return (f'"{rawsubject}" is an old name that a split gave to another '
                    f'species, so it no longer says which one is meant')
        return ""

    def getSpecies(self, rawsubject, fuzzy=False):       
        # lower case
        subject = rawsubject.lower().lstrip().rstrip()
        nested = False

        scientificname = self.getKnownSpecies(subject, nested, LookUpInGBIF=False)

        # not found, see if it is a new species
        if (scientificname == ""):
            scientificname = self.getKnownSpecies(subject, nested,  LookUpInGBIF=True, fuzzy=fuzzy)   
    
        return scientificname

