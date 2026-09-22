from engine.specieslib import Species
from engine.speciesclassification import SpeciesClassification
from engine.filecsv import Files
from engine.picturecsv import Pictures
from engine.exif_writer import exiftool_write, run_exiftool_files
from engine.sidecarlib import push_metadata_to_dop
from utils.sidecar_policy import (
    color_rating_to_xmp_enabled, color_rating_to_dop_enabled,
)
import logging
import time
import os
from pathlib import Path
import subprocess
import json
import ast
import pdb  # For debugging
from utils.raw_extensions import DXO_RAW, RAW, RAW_OR_HEIC, VIDEO, bare


logger = logging.getLogger("speciesviewer")

# Write all trip data in CSV file "E:\Dropbox\naturedb\admin\trip_admin.csv".
# sv4WriteBlog: Create a blog for project 2017-08-09
# Read 15 predefined species from E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\species.csv.
# Read specification file, now create links.
# Read 19185 predefined species from E:\Dropbox\trips\2017 oostzeedb\metadata/active/speciescollection.csv.
# Read EXIF data for 94 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_files.csv.
# Read data for 42 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_pictures.csv.
# Read all committed data from the main repository in 0.1 seconds.
# Read EXIF data for 3 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\data_files.csv.
# Read data for 0 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\data_pictures.csv.
# Read all new data from project in 0.0 seconds.

def ensure_list(subject):
    if isinstance(subject, list):
        return subject
    elif isinstance(subject, str):
        return [token.strip() for token in subject.split(",")]
    else:
        raise TypeError(f"Unexpected subject type: {type(subject)}")
    

# File types the species keyword is written to: the XMP sidecar (canonical),
# JPGs (so shared/exported copies carry the name for friends), sidecar-less
# images (HEIC/PNG) and movies (MOV/MP4 — same reasoning as HEIC: there is no
# RAW+sidecar pipeline for video, so the keyword goes straight into the file,
# confirmed writable 2026-09-18 with exiftool against a real camera .MOV).
# RAW files are skipped — their XMP sidecar holds the keyword, so embedding in
# the raw is redundant — and the DxO .dop is skipped because it is a database
# with no XMP:Subject (writing there only produced a warning).
_SUBJECT_WRITE_EXT = {"xmp", "jpg", "jpeg", "heic", "heif", "png"} | bare(VIDEO)

# The RAW extensions, bare, for "which file does this picture's sidecar belong
# beside?". From the one table, like every other extension test in the tree.
_RAW_EXT = bare(RAW)


def format_subject_for_xmp(subject_text):
    try:
        parts = ast.literal_eval(subject_text)
        if isinstance(parts, list):
            return ", ".join(p.strip("'\"") for p in parts)
    except Exception:
        pass
    return subject_text.strip("'\"")    

# read files without MyProject
class FilePicture:
    def __init__(self, MyProject):
        self.sp = {}
        self.rs = {}
        self.sc = {}
        self.fi = {}
        self.upn = {}   

        if MyProject != None:
            MyProject.registerSpecies(self)
            self.mp = MyProject
        return 
    
    def clearCSVs(self):
        self.sp = {}
        self.rs = {}
        self.sc = {}
        self.fi = {}
        self.upn = {}        
        return
    
    # get color ode if any
    def getColor(self, filename):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            color = self.upn.UniquePictureName_dict[upn_name]["Label"]
            return color.lower()
        except Exception:
            return ""
        
    # returns a string
    def getErrorMsg(self, filename):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            errormsg = self.upn.UniquePictureName_dict[upn_name]["errormessage"]
            if errormsg != '':
                logger.info(f"getErrorMsg: {errormsg}")
            return errormsg
        except Exception:
            return ""
        
    # returns a list, eg ['bombylius venosus', 'zwartborstelwolzwever']
    # may need to handle  or simply knobbelzwaan
    def getSubject(self, filename):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            subject = self.upn.UniquePictureName_dict[upn_name]["subject"]
            return subject
        except Exception:
            return ""
        
    def getDescription(self, filename):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            return self.upn.UniquePictureName_dict[upn_name].get("Description", "")
        except Exception:
            return ""

    def updateDescription(self, filename, new_description):
        """Write XMP:Description to the XMP sidecar and any JPGs sharing the
        same UPN (RAW files are convention-immutable, so we skip those), then
        persist data_files.csv and data_pictures.csv. Mirrors updateSubject's
        propagation pattern. Returns the count of on-disk files rewritten."""
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
        except KeyError:
            logger.warning(f"updateDescription: no CSV row for {filename}")
            return 0
        raw_exts = bare(RAW_OR_HEIC)  # canonical table — see utils/raw_extensions.py
        files_written = 0
        for f_key, f_row in self.fi.files_dict.items():
            if f_row.get("UniquePictureName") != upn_name:
                continue
            f_row["XMP:Description"] = new_description
            ext = f_row.get("WindowsExtension", "").lower()
            if ext in raw_exts:
                continue  # RAW files stay immutable
            filepath = Path(f_row.get("WindowsFileName", ""))
            if not filepath.exists():
                continue
            try:
                exiftool_write(filepath, [f"-XMP:Description={new_description}"])
                files_written += 1
            except subprocess.CalledProcessError as e:
                logger.warning(
                    f"updateDescription: failed to write {filepath.name}: {e.stderr}"
                )
        self.fi.write_project_files()
        upn_obj = self.upn.UniquePictureName_dict.get(upn_name)
        if upn_obj is not None:
            upn_obj["Description"] = new_description
            self.upn.write_project_summary()
        return files_written

    def updateHeadline(self, filename, new_headline):
        """Write XMP:Headline to the XMP sidecar and any JPGs sharing the same
        UPN (RAW files are convention-immutable), then persist the CSVs. Parallel
        to updateDescription; used by the translation-validation panel to fix
        typos in the source Headline. Returns the count of on-disk files written."""
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
        except KeyError:
            logger.warning(f"updateHeadline: no CSV row for {filename}")
            return 0
        raw_exts = bare(RAW_OR_HEIC)  # canonical table — see utils/raw_extensions.py
        files_written = 0
        for f_key, f_row in self.fi.files_dict.items():
            if f_row.get("UniquePictureName") != upn_name:
                continue
            f_row["XMP:Headline"] = new_headline
            ext = f_row.get("WindowsExtension", "").lower()
            if ext in raw_exts:
                continue  # RAW files stay immutable
            filepath = Path(f_row.get("WindowsFileName", ""))
            if not filepath.exists():
                continue
            try:
                exiftool_write(filepath, [f"-XMP:Headline={new_headline}"])
                files_written += 1
            except subprocess.CalledProcessError as e:
                logger.warning(
                    f"updateHeadline: failed to write {filepath.name}: {e.stderr}"
                )
        self.fi.write_project_files()
        upn_obj = self.upn.UniquePictureName_dict.get(upn_name)
        if upn_obj is not None:
            upn_obj["Headline"] = new_headline
            self.upn.write_project_summary()
        return files_written

    def updateLabel(self, filename, new_label):
        """Write XMP:Label to the XMP sidecar and any JPGs sharing the same UPN
        (RAW files stay convention-immutable), then persist data_files.csv and
        data_pictures.csv. Mirrors updateDescription. Used by the large-image
        viewer to set/clear a colour label (e.g. a Blue/Purple deletion
        candidate). An empty new_label clears the label. Returns the count of
        on-disk files rewritten."""
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
        except KeyError:
            logger.warning(f"updateLabel: no CSV row for {filename}")
            return 0
        raw_exts = bare(RAW_OR_HEIC)  # canonical table — see utils/raw_extensions.py
        dxo_raw_exts = bare(DXO_RAW)  # canonical table — see utils/raw_extensions.py
        files_written = 0
        raw_path = None
        for f_key, f_row in self.fi.files_dict.items():
            if f_row.get("UniquePictureName") != upn_name:
                continue
            f_row["XMP:Label"] = new_label
            ext = f_row.get("WindowsExtension", "").lower()
            if ext in raw_exts:
                if ext in dxo_raw_exts and raw_path is None:
                    raw_path = f_row.get("WindowsFileName", "")  # for the .dop colour write
                continue  # RAW files stay immutable
            if ext == "xmp" and not color_rating_to_xmp_enabled():
                continue  # colour in .xmp suppressed (PhotoLab mistreats it); JPGs still written
            filepath = Path(f_row.get("WindowsFileName", ""))
            if not filepath.exists():
                continue
            try:
                exiftool_write(filepath, [f"-XMP:Label={new_label}"])
                files_written += 1
            except subprocess.CalledProcessError as e:
                logger.warning(
                    f"updateLabel: failed to write {filepath.name}: {e.stderr}"
                )
        # Colour into the DxO .dop (PhotoLab's native store, read on 'import sidecars').
        if raw_path and color_rating_to_dop_enabled():
            push_metadata_to_dop(raw_path, color=new_label)
        self.fi.write_project_files()
        upn_obj = self.upn.UniquePictureName_dict.get(upn_name)
        if upn_obj is not None:
            upn_obj["Label"] = new_label
            self.upn.write_project_summary()
        return files_written

    def getSpeciesName(self, filename):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            upn  = self.upn.UniquePictureName_dict[upn_name]
            if upn["NLSoortNaam"] != "":
                # logger.info(f"get: {upn["NLSoortNaam"]}")
                return upn["NLSoortNaam"]
            elif upn["EngSoortNaam"] != "":
                return upn["EngSoortNaam"]
            else:
                return upn["WetSoortNaam"]
        except Exception:
            return ""        
        
    def getUPN(self, filename):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            upn = self.upn.UniquePictureName_dict[upn_name]
            return upn
        except Exception:
            return None                
        
    def filteredFile(self, filename, allowed_colors = None):
        try:
            upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
            color = self.upn.UniquePictureName_dict[upn_name]["Label"]            
            if allowed_colors != None:
                if color.lower() in allowed_colors:
                    return True
            return False
        except Exception as e:
            logger.warning("Error in filteredFile for {filename} error {e}")
            return True

    
    # read just file and pictures for better experience
    def readTripDay(self, trippath, projectdirectory):
        metadatadir = os.path.join(trippath,  "metadata", "review", projectdirectory)
        self.fi = Files(None)
        filecount = self.fi.read_file(os.path.join(metadatadir, "data_files.csv"))

        # Read data for 42 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_pictures.csv.
        self.upn = Pictures(None)
        picturecount = self.upn.read_pictures(os.path.join(metadatadir, "data_pictures.csv"))
        
        return (filecount[0] > 0) and (picturecount > 0)  
    
    # replaceFileSubject.
    # 1. change actual file with EXIFtool
    # 2. Change the .csv file reflected in the internal structures
    # 3. Review Photolab and Lightroom
    # Returns True if the on-disk XMP:Subject was actually rewritten.
    def replaceFileSubject(self, filename, original_subject, correct_subject):
        filepath = Path(self.fi.files_dict[filename]['WindowsFileName'])
        original_subject_for_xmp = format_subject_for_xmp(original_subject)
        correct_subject_for_xmp = format_subject_for_xmp(correct_subject)
        wrote = False
        # Step 1: Read current XMP:Subject using exiftool. -charset utf8 makes
        # exiftool interpret args/output as UTF-8; encoding='utf-8' makes Python
        # decode stdout as UTF-8 instead of the Windows local code page.
        try:
            result = subprocess.run(
                ['exiftool', '-charset', 'filename=utf8', '-charset', 'utf8',
                 '-j', '-XMP:Subject', str(filepath)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
                text=True,
                encoding='utf-8'
            )
            metadata = json.loads(result.stdout)[0]
            current_subjects = metadata.get('Subject', [])
            if isinstance(current_subjects, str):
                current_subjects = [current_subjects]

            # Step 3: Replace subject if original_subject is found
            try:
                original_subject_list = ast.literal_eval(original_subject)
                if not isinstance(original_subject_list, list):
                    raise ValueError
            except Exception:
                original_subject_list = [original_subject]
            # An empty original means the picture had no species yet — there is
            # nothing to match against (on-disk Subject is []), so SET the new
            # value unconditionally. The old match-guard (['']==[]) failed here,
            # so a newly-named picture was never written to disk and the name was
            # lost on the next re-read.
            original_is_empty = (not original_subject_list) or all(
                (s is None or str(s).strip() in ("", "[]")) for s in original_subject_list
            )
            if original_is_empty or original_subject_list == current_subjects:
                exiftool_write(filepath, [f'-XMP:Subject={correct_subject_for_xmp}'])
                wrote = True
            else:
                logger.debug(
                    f"Original subject '{original_subject_for_xmp}' not found in {filepath.name}; "
                    f"on-disk value is {current_subjects}."
                )

        except subprocess.CalledProcessError as e:
            logger.error(f"ExifTool error on {filepath.name}: {e.stderr}")

        # Step 2: update the internal data structure
        self.fi.files_dict[filename]['XMP:Subject'] = correct_subject
        return wrote



    # the UPN update is only reflected in the data_pictures file and the internal structures 
    def replaceUPNSubject(self, filename, original_subject, correct_subject, sc_object):
        upn_object = self.upn.UniquePictureName_dict[Path(filename).name]
        if upn_object != None:
            upn_object['subject'] = correct_subject
            upn_object['errormessage'] = ''   # missing species issue fixed, clear error message
            if sc_object is not None:
                upn_object['NLSoortNaam'] = sc_object['DutchName']
                upn_object['EngSoortNaam'] = sc_object['EnglishName']
                upn_object['WetSoortNaam'] = sc_object['ScientificName']
            elif correct_subject == "":
                # Cleared — drop the resolved names too, else the old Dutch/
                # English/scientific names linger on a now species-less picture.
                upn_object['NLSoortNaam'] = ''
                upn_object['EngSoortNaam'] = ''
                upn_object['WetSoortNaam'] = ''
            # write updated data_pictures file
            self.upn.write_project_summary()
        return upn_object

    # function to determine which files must be updated
    # Returns (upn_object, files_written_count). The caller should report success
    # only when files_written_count > 0; the CSV is always updated either way.
    def updateSubject(self, filename, original_subject, corrected_subject):
        if original_subject == corrected_subject:
            logger.debug(f"There are no changes for {filename}, no updates done.")

        try:
            parts = ast.literal_eval(corrected_subject)
            lookup_name = max(parts, key=len) if isinstance(parts, list) and parts else corrected_subject
        except Exception:
            lookup_name = corrected_subject
        scientific_name = self.sc.getSpecies(lookup_name)
        if scientific_name == "":  # still no name found
            logger.warning(f"The species name is not recognized, but the updated keywords are stored in the files.")
            sc_object = None
        else:
            sc_object = self.sc.sc_dict[scientific_name]

        upn_name = self.fi.files_dict[Path(filename).name]["UniquePictureName"]
        files_written = 0
        for f in self.fi.files_dict:
            frow = self.fi.files_dict[f]
            if frow["UniquePictureName"] != upn_name:
                continue
            # Only files that should carry the keyword — skip RAW and .dop.
            if frow.get("WindowsExtension", "").lower() not in _SUBJECT_WRITE_EXT:
                continue
            if frow["XMP:Subject"] == original_subject:
                if self.replaceFileSubject(f, original_subject, corrected_subject):
                    files_written += 1

        # update external CSV file (internal done on a per file basis)
        self.fi.write_project_files()
        # update internal and external data_pictures (just one per definition)
        upn_object = self.replaceUPNSubject(upn_name, original_subject, corrected_subject, sc_object)
        return upn_object, files_written

    def _raw_for_upn(self, upn_name):
        """The RAW file of a picture, as a Path — or None if it has none.

        Used to decide WHERE to author a missing .xmp: a sidecar belongs beside
        its RAW and is named after it. Extensions come from the one table
        (utils.raw_extensions) — never a literal list here.
        """
        for frow in self.fi.files_dict.values():
            if frow["UniquePictureName"] != upn_name:
                continue
            if frow.get("WindowsExtension", "").lower() in _RAW_EXT:
                return Path(frow["WindowsFileName"])
        return None

    def updateSubjects(self, filenames, corrected_subject):
        """Batched multi-picture version of updateSubject: write the SAME
        `corrected_subject` to many pictures with just TWO exiftool calls (one
        batched read for the on-disk guard, one batched write) instead of a
        read+write PER file, and rewrite data_files.csv / data_pictures.csv ONCE
        at the end instead of once per picture. Semantics match updateSubject +
        replaceFileSubject exactly — same species resolution, same write-target
        filter (UPN + writable ext + CSV-subject == original), same on-disk
        match guard — only the I/O is coalesced. Returns (updated_upns,
        files_written). Used by the Set-species multi-selection commit.
        """
        # Resolve the species ONCE — every picture gets the identical subject.
        try:
            parts = ast.literal_eval(corrected_subject)
            lookup_name = (max(parts, key=len) if isinstance(parts, list) and parts
                           else corrected_subject)
        except Exception:
            lookup_name = corrected_subject
        scientific_name = self.sc.getSpecies(lookup_name) if corrected_subject != "" else ""
        sc_object = self.sc.sc_dict[scientific_name] if scientific_name else None
        if corrected_subject != "" and scientific_name == "":
            logger.warning("The species name is not recognized, but the updated "
                           "keywords are stored in the files.")
        correct_for_xmp = (format_subject_for_xmp(corrected_subject)
                           if corrected_subject != "" else "")

        # 1) Collect every write target across the selection, pre-filtered exactly
        #    like updateSubject's loop (same UPN, writable type, on-CSV subject
        #    equals this picture's original). Dedup by path.
        targets = []            # (file_key, Path, original_subject)
        seen = set()
        updated_upns = set()
        sidecarless = []        # pictures with no file the keyword can go in
        for fn in filenames:
            name = Path(fn).name
            if name not in self.fi.files_dict:
                continue
            upn_name = self.fi.files_dict[name]["UniquePictureName"]
            orig = self.getSubject(name)
            updated_upns.add(upn_name)
            n_before = len(targets)
            for f, frow in self.fi.files_dict.items():
                if frow["UniquePictureName"] != upn_name:
                    continue
                if frow.get("WindowsExtension", "").lower() not in _SUBJECT_WRITE_EXT:
                    continue
                if frow["XMP:Subject"] != orig:
                    continue
                fp = Path(frow["WindowsFileName"])
                key = os.path.normcase(os.path.normpath(str(fp)))
                if key in seen:
                    continue
                seen.add(key)
                targets.append((f, fp, orig))
            if len(targets) == n_before:
                sidecarless.append(upn_name)

        # 1b) Pictures with NO file the keyword can go in: a RAW straight off the
        #     card that has never been culled, so no sidecar was ever written
        #     beside it. Every filter above passes over it (a RAW is deliberately
        #     never written into), the commit updated only the CSV, and the next
        #     Read & Summarize — which re-reads the files on disk — dropped the
        #     name again. Silently: 7 of 8 pictures in one selection, and the day
        #     had 678 such RAWs (2026-09-22). Author the sidecar here, exactly the
        #     way ingest does, so the name lands where every later step reads it.
        #     Not for a CLEAR: creating a sidecar to record the absence of a
        #     species would invent a file to hold nothing.
        created_paths = []
        if sidecarless and corrected_subject != "":
            raws = [r for r in (self._raw_for_upn(u) for u in sidecarless) if r]
            if raws:
                from engine.sidecarlib import ensure_xmps
                created = ensure_xmps(raws)
                created_paths = [Path(x) for x in created.values()]
                if len(created) < len(raws):
                    logger.warning(f"Species commit: {len(raws) - len(created)} "
                                   f"picture(s) have no sidecar and one could not "
                                   f"be created — the name was NOT written to disk.")
                else:
                    logger.info(f"Species commit: {len(created)} picture(s) had no "
                                f"file to hold the name; wrote their .xmp sidecar.")

        files_written = 0
        write_paths = list(created_paths)
        if targets:
            # 2) ONE batched read of the on-disk XMP:Subject for the guard.
            current_by_path = {}
            try:
                out = run_exiftool_files(
                    ["-j", "-XMP:Subject"], [fp for _, fp, _ in targets]).stdout
                for entry in (json.loads(out) if out else []):
                    subj = entry.get("Subject", [])
                    if isinstance(subj, str):
                        subj = [subj]
                    key = os.path.normcase(os.path.normpath(entry.get("SourceFile", "")))
                    current_by_path[key] = subj
            except Exception as e:
                logger.warning(f"Batched subject read failed ({e}); on-disk guard "
                               f"will skip unverified files, as the per-file path does.")

            # 3) Apply the same match guard as replaceFileSubject, collecting the
            #    files to actually write. The CSV cache is updated for every
            #    pre-filtered target regardless (mirrors replaceFileSubject, which
            #    sets files_dict[...] even when the on-disk write is skipped).
            for f, fp, orig in targets:
                current = current_by_path.get(
                    os.path.normcase(os.path.normpath(str(fp))), [])
                try:
                    orig_list = ast.literal_eval(orig)
                    if not isinstance(orig_list, list):
                        raise ValueError
                except Exception:
                    orig_list = [orig]
                original_is_empty = (not orig_list) or all(
                    (s is None or str(s).strip() in ("", "[]")) for s in orig_list)
                if original_is_empty or orig_list == current:
                    write_paths.append(fp)
                self.fi.files_dict[f]['XMP:Subject'] = corrected_subject

        # 4) ONE batched write of the identical new subject to the whole set —
        #    the files already carrying the old value, plus any sidecar just
        #    created for a RAW that had none (nothing to guard there: the file
        #    did not exist a moment ago).
        if write_paths:
            run_exiftool_files(
                [f"-XMP:Subject={correct_for_xmp}", "-overwrite_original"],
                write_paths)
            files_written = len(write_paths)

        # 5) Update each UPN object once (same fields replaceUPNSubject sets), then
        #    persist the CSV + summary ONCE for the whole batch.
        for upn_name in updated_upns:
            upn_object = self.upn.UniquePictureName_dict.get(upn_name)
            if upn_object is None:
                continue
            upn_object['subject'] = corrected_subject
            upn_object['errormessage'] = ''
            if sc_object is not None:
                upn_object['NLSoortNaam'] = sc_object['DutchName']
                upn_object['EngSoortNaam'] = sc_object['EnglishName']
                upn_object['WetSoortNaam'] = sc_object['ScientificName']
            elif corrected_subject == "":
                upn_object['NLSoortNaam'] = ''
                upn_object['EngSoortNaam'] = ''
                upn_object['WetSoortNaam'] = ''
        self.fi.write_project_files()
        self.upn.write_project_summary()
        return updated_upns, files_written


    # read all species
    def readMaster(self, trippath, projectdirectory, getEarlierDays = False, silent=False):
        metadatadir = os.path.join(trippath,  "metadata")
        starttime = time.time()
        self.sp = Species(None)
        speciesfilename = os.path.join(metadatadir, "active", "species.csv")
        self.rs = self.sp.readSpeciesInfo(speciesfilename, False)

        # read all knowlegde about species, including species we do not yet have.  
        # Maintain English and Dutch species list
        self.sc = SpeciesClassification(None)
        # E:\Dropbox\trips\2017 oostzeedb\metadata/active/speciescollection.csv.        
        self.sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)
        # Read EXIF data file, one record for each file (ORF, jpg, XMP, etc)        
        # Read EXIF data for 94 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_files.csv.
        self.fi = Files(None)
        filecount = self.fi.read_file(os.path.join(metadatadir, "active", "data_files.csv"))

        # Read data for 42 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_pictures.csv.
        self.upn = Pictures(None)
        picturecount = self.upn.read_pictures(os.path.join(metadatadir, "active", "data_pictures.csv"))

         # # see what files for non-commtted projected
        # if getEarlierDays and self.Trip != None:
        #     # Collect states
        #     directory_to_scan = os.path.join(self.getMetaDir(), 'review') 
        #     days_dict = getAllProjectStatus(self, directory_to_scan, silent=True)
        #     # read csv for files and pictures that are not yet committed
        #     for statemachine in days_dict:                      # projects
        #         if statemachine >= self.ProjectName(): 
        #             break
        #         if not days_dict[statemachine]['WriteCommit'][0]:  # this project is not committed
        #             logger.info(f"Reading data from project {statemachine} that has not yet been committed.")
        #             read_file(self, os.path.join(metadatadir, "review", statemachine, "data_files.csv"), silent=True)
        #             read_pictures(self, os.path.join(metadatadir, "review", statemachine, "data_pictures.csv"))

        thissteptime = time.time()
        logger.info(f"Read all committed data from the main repository in {thissteptime-starttime:.1f} seconds.")    
    
        # read other files and pictures
        RepositoryFileCount = filecount
        RepositoryPictureCount = picturecount
        
        return [RepositoryFileCount[0], RepositoryPictureCount, RepositoryFileCount[1], RepositoryFileCount[2]]
    
    def readProject(self, trippath, projectdirectory, getEarlierDays = False, silent=False):
        metadatadir = os.path.join(trippath,  "metadata")
        starttime = time.time()
        self.sp = Species(None)
        speciesfilename = os.path.join(metadatadir, "review", projectdirectory, "species.csv")
        self.rs = self.sp.readSpeciesInfo(speciesfilename, False)

        # read all knowlegde about species, including species we do not yet have.  
        # Maintain English and Dutch species list
        self.sc = SpeciesClassification(None)
        # E:\Dropbox\trips\2017 oostzeedb\metadata/active/speciescollection.csv.        
        self.sc.readSpeciesCollection(os.path.join(metadatadir, "active", "speciescollection.csv"), False)
        # Read EXIF data file, one record for each file (ORF, jpg, XMP, etc)        
        # Read EXIF data for 94 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_files.csv.
        self.fi = Files(None)
        filecount = self.fi.read_file(os.path.join(metadatadir, "active", "data_files.csv"))

        # Read data for 42 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/active/data_pictures.csv.
        self.upn = Pictures(None)
        picturecount = self.upn.read_pictures(os.path.join(metadatadir, "active", "data_pictures.csv"))

         # # see what files for non-commtted projected
        # if getEarlierDays and self.Trip != None:
        #     # Collect states
        #     directory_to_scan = os.path.join(self.getMetaDir(), 'review') 
        #     days_dict = getAllProjectStatus(self, directory_to_scan, silent=True)
        #     # read csv for files and pictures that are not yet committed
        #     for statemachine in days_dict:                      # projects
        #         if statemachine >= self.ProjectName(): 
        #             break
        #         if not days_dict[statemachine]['WriteCommit'][0]:  # this project is not committed
        #             logger.info(f"Reading data from project {statemachine} that has not yet been committed.")
        #             read_file(self, os.path.join(metadatadir, "review", statemachine, "data_files.csv"), silent=True)
        #             read_pictures(self, os.path.join(metadatadir, "review", statemachine, "data_pictures.csv"))

        thissteptime = time.time()
        logger.info(f"Read all committed data from the main repository in {thissteptime-starttime:.1f} seconds.")    
    
        # read other files and pictures
        RepositoryFileCount = filecount
        RepositoryPictureCount = picturecount
        metadatadirreview = os.path.join(metadatadir, 'review') 
        
        # Read EXIF data for 3 files from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\data_files.csv.
        # Read data for 0 pictures from CSV file E:\Dropbox\trips\2017 oostzeedb\metadata/review/2017-08-09\data_pictures.csv.
        count, from_time, last_time = self.fi.read_file(os.path.join(metadatadirreview, '2017-08-09', 'data_files.csv'), silent=True)
        self.upn.read_pictures(os.path.join(metadatadirreview, '2017-08-09', 'data_pictures.csv'))      

        logger.info(f"Read all new data from project in {time.time()-thissteptime:.1f} seconds.")
        return [RepositoryFileCount[0], RepositoryPictureCount, RepositoryFileCount[1], RepositoryFileCount[2]]

