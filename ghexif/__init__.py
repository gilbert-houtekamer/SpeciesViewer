from ghexif.exceptions import SVException
from ghexif.selected_fields import COLUMN_NAMES, SELECTED_FIELDS
from ghexif.csv_columns import SPECIES_FIELDS

MyProject = None

# handling
files_dict = {}         # all files
UniquePictureName_dict = {}     # all pictures, with references to files_dict and species_dict

