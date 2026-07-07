import os
import csv
import logging
from pathlib import Path, PureWindowsPath
import engine
from utils.paths import is_windows_absolute, to_fs_path

logger = logging.getLogger("speciesviewer")


def relativize_path(abs_path, root):
    """Convert a Windows absolute path to a /-prefixed path relative to root.
    Returns abs_path unchanged when it is already relative, empty, or not under
    root. Drive detection and the relative_to use PureWindowsPath so this behaves
    the same on Windows and macOS (a Windows-authored path carries no drive when
    parsed by the host Path on macOS)."""
    if not abs_path or not is_windows_absolute(abs_path):
        return abs_path
    try:
        return "/" + PureWindowsPath(abs_path).relative_to(PureWindowsPath(root)).as_posix()
    except ValueError:
        return abs_path


def resolve_path(stored_path, root):
    """Resolve a stored path back to absolute.
    Absolute Windows paths (drive letter / UNC) are returned as-is; /-prefixed
    relative paths are joined with root. Returns stored_path unchanged when root
    is None/empty. Host-independent: a '\\'-separated or drive-letter value from
    a Windows-authored CSV is classified correctly on macOS too, instead of being
    mis-joined onto root."""
    if not stored_path or not root:
        return stored_path
    if is_windows_absolute(stored_path):
        return stored_path
    return str(to_fs_path(stored_path, root))


# strip all suffixes for files from Lightroom, DxO PureRaw, DxO Photolab, Capture etc
def deriveUniquePictureName(windows_filename):
    # start with filename.extensiom
    # strip extension, make it lower case
    uniquename, extension = os.path.splitext(windows_filename)
    if extension.lower() == ".dop":  # DOP sidecars carry a double extension (e.g. foo.ORF.dop)
        uniquename, extension = os.path.splitext(uniquename)
    uniquename = uniquename.lower()
    # look for common extensions
    uniquename = uniquename.split("-small")[0]  # SpeciesViewer
    uniquename = uniquename.replace(
        "-nef", ""
    )  # Remove "-NEF" from the uniquename (if present)
    # the following should be externalized!  BUGBUG
    uniquename = uniquename.split("-orf")[0]  # Remove "-ORF" from the uniquename (if present)
    # Remove "-DxO" and any characters after it (if present)
    uniquename = uniquename.split("-DxO")[0]  # PureRaw 4
    uniquename = uniquename.split("_DxO")[0]  # PureRaw 3
    uniquename = uniquename.split("_dxo")[0]  # PhotoLab
    # 
    uniquename = uniquename.split("-jpg")[0]  # PhotoLab
    uniquename = uniquename.split("-JPG")[0]  # PhotoLab
    # Windows? - Copy
    uniquename = uniquename.replace(
        " - copy", ""
    )  # Remove "-ORF" from the uniquename (if present)
    return uniquename

# Read EXIF data file, one record for each file (ORF, jpg, XMP, etc)
# For second call, additional data is appended
# Check if is does not create a duplicaete
def read_file(MyProject, filename, silent=False):
    from_time = ""
    last_time = ""
    filecount = len(MyProject.files_dict)
    pictures_root = Path(MyProject.getProjectPath()).parent if MyProject is not None else None
    with open(filename, "r", newline="", encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile, delimiter=';')
        for row in reader:
            if pictures_root:
                row["File:Directory"] = resolve_path(row.get("File:Directory", ""), pictures_root)
                row["WindowsFileName"] = resolve_path(row.get("WindowsFileName", ""), pictures_root)
            if row['File:FileName'] not in MyProject.files_dict:
                MyProject.files_dict[row['File:FileName']] = row
                if row['EXIF:DateTimeOriginal'] != "":
                    if from_time == "" or from_time > row['EXIF:DateTimeOriginal']: 
                        from_time = row['EXIF:DateTimeOriginal']
                    if last_time < row['EXIF:DateTimeOriginal']:
                        last_time = row['EXIF:DateTimeOriginal']
            elif not silent:
                logger.warning(f"Unexpected duplicate file {row['WindowsFileName']} not added, ")
                logger.warning(f"File was already found here {MyProject.files_dict[row['File:FileName']]['WindowsFileName']}.")
    logger.info(f'Read EXIF data for {len(MyProject.files_dict)-filecount} files from CSV file {filename}.')
    return len(MyProject.files_dict), from_time, last_time

# read picture data file, one record for each picture, with links to files (ORF, jpg, XMP, etc)
# For second call, additional data is appended
# Check if is does not create a duplicaete
def read_pictures(MyProject, picturefile):
    picturecount = len(MyProject.UniquePictureName_dict)
    with open(picturefile, "r", newline="", encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile, delimiter=';')
        for row in reader:
            if row['UniquePictureName'] not in MyProject.UniquePictureName_dict:
                MyProject.UniquePictureName_dict[row['UniquePictureName']] = row
                # restore file list
                if row["Files"] == '[]':           # update in place
                    row["Files"] = []
                else:
                    try:
                        # remove additional quoting
                        liststring = row["Files"][1:-1].replace("'","")
                        listvalues = liststring.split(',')
                        # remove possible leading spaces
                        row["Files"] = []                    
                        for value in listvalues:
                            if value.lstrip() not in row["Files"]:
                                row["Files"].append(value.lstrip())
                    except ValueError:
                        pass  # Handle non-numeric values gracefully

    logger.info(f'Read data for {len(MyProject.UniquePictureName_dict)-picturecount} pictures from CSV file {picturefile}.')
    return len(MyProject.UniquePictureName_dict)

def read_Files_CSV():
    return

def parseListFromCSV(rawstring):
    if rawstring == '[]' or rawstring == '':           # update in place
        cleanListString = [] 
    else:
        try:
            # remove additional quoting
            liststring = rawstring[1:-1].replace("'","")
            listvalues = liststring.split(',')
            # remove possible leading spaces
            cleanListString = []                    
            for value in listvalues:
                if value.lstrip() not in cleanListString:
                    cleanListString.append(value.lstrip())
        except ValueError:
            cleanListString = [] 
            pass  # Handle non-numeric values gracefully

    return cleanListString