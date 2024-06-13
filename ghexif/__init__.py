from ghexif.selected_fields import COLUMN_NAMES, SELECTED_FIELDS
from ghexif.csv_columns import SPECIES_FIELDS

MyProject = None

# handling
files_dict = {}         # all files
UniquePictureName_dict = {}     # all pictures, with references to files_dict and species_dict
# specieslib
#species_dict = {}       # all species we have the names for, points to Dictonory
#dutch_dict = {}         # all species we have the Dutch name for, points to scientific name
#english_dict = {}       # all species we have the English name for, points to scientific name
# templates
#templates_dict = {}   