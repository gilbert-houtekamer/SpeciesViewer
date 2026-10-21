
import os
import csv
import logging
from pathlib import Path
from engine.csv_columns import COLUMN_NAMES

logger = logging.getLogger("speciesviewer")

# strip all suffixes for files from Lightroom, DxO PureRaw, DxO Photolab, Capture etc
# One copy, in engine.handling — two identical ones had already been edited apart once.
from engine.handling import deriveUniquePictureName  # noqa: F401 (re-exported)


# read files without MyProject
class Files:
    def __init__(self, MyProject):
        self.files_dict = {}      
        self.filecount = 0
        self.filename = ""
        # File:FileName -> the root its stored RELATIVE path was resolved against
        # in read_file, so write_project_files can store it relative again.
        self._stored_relative = {}

        if MyProject != None:
            MyProject.registerSpecies(self)
            self.mp = MyProject
        return 
    
    # Read EXIF data file, one record for each file (ORF, jpg, XMP, etc)
    # For second call, additional data is appended
    # Check if is does not create a duplicaete
    def read_file(self, filename, silent=False):
        from_time = ""
        last_time = ""
        self.filename = filename
        runningcounter = len(self.files_dict)
        pictures_root = None
        try:
            from utils.config import _config
            from engine.handling import resolve_path
            pic_dir = _config.get("project", "picturedirectory", fallback="")
            if pic_dir:
                pictures_root = Path(pic_dir).parent
        except Exception:
            pass
        with open(filename, "r", newline="", encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile, delimiter=';')
            for row in reader:
                if pictures_root:
                    stored = row.get("File:Directory", "")
                    row["File:Directory"] = resolve_path(stored, pictures_root)
                    row["WindowsFileName"] = resolve_path(row.get("WindowsFileName", ""), pictures_root)
                    if row["File:Directory"] != stored:
                        self._stored_relative[row.get("File:FileName", "")] = pictures_root
                if row['File:FileName'] not in self.files_dict:
                    self.files_dict[row['File:FileName']] = row
                    if row['EXIF:DateTimeOriginal'] != "":
                        if from_time == "" or from_time > row['EXIF:DateTimeOriginal']: 
                            from_time = row['EXIF:DateTimeOriginal']
                        if last_time < row['EXIF:DateTimeOriginal']:
                            last_time = row['EXIF:DateTimeOriginal']
                elif not silent:
                    logger.warning(f"Unexpected duplicate file {row['WindowsFileName']} not added, ")
                    logger.warning(f"File was already found here {self.files_dict[row['File:FileName']]['WindowsFileName']}.")
        logger.debug(f'Read EXIF data for {len(self.files_dict)-runningcounter} files from CSV file {filename}.')
        return len(self.files_dict), from_time, last_time
    
    def write_project_files(self):
        # No output path → nothing to persist here. This happens for the merged,
        # in-memory Files object the Trip-scope viewers build (Files(None)): its
        # per-day CSVs are written via their own day objects instead. Skip rather
        # than open('') and crash. (write_project_summary already guards this.)
        if not self.filename:
            logger.debug("write_project_files: no filename set — skipping "
                         "(merged in-memory Files object; persist per-day instead).")
            return
        with open(self.filename, "w", newline="", encoding='utf-8') as csvfile:
            writer = csv.DictWriter(
                csvfile, fieldnames=COLUMN_NAMES.values(), quoting=csv.QUOTE_MINIMAL, delimiter=';'
            )
            writer.writeheader()
            from engine.handling import relativize_path
            for file in self.files_dict:
                row = self.files_dict[file]
                # Write a path back in the form it was READ in. read_file resolves
                # the relative paths sv2 stores (portable between machines, since
                # 025a682) to absolute; writing them out unchanged quietly turned a
                # whole day absolute on any colour/rating save (2026-09-07, found
                # 2026-09-29). Rows that were absolute on disk stay absolute.
                root = self._stored_relative.get(file)
                if root is not None:
                    row = dict(row)
                    row["File:Directory"] = relativize_path(row.get("File:Directory", ""), root)
                    row["WindowsFileName"] = relativize_path(row.get("WindowsFileName", ""), root)
                writer.writerow(row)
