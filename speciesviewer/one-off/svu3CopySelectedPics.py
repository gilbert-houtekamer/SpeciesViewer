# svu3CopySelectedPics.py: Copy Green/Yellow/Red-labeled RAW+XMP files into a dated trip directory.
# Scans one or more source folders for .xmp sidecars whose color label matches the target set,
# extracts CreateDate, creates a per-day subdirectory under destination_root, writes a blog.ini
# stub, and copies the matching RAW + .xmp + .dop files alongside it.
# Hardcoded to a Baltic Sea 2017 trip (source_dirs, destination_root, trip_naam) —
# update those variables at the top of the file before running for a different trip.
# Note: no sys.path bootstrap — run from the speciesviewer/ directory so 'engine' is importable.
# code originally co-written with GitHub Copilot.

import os
import shutil
import glob
from xml.etree import ElementTree as ET
from datetime import datetime
import engine.translatelib 
# Define namespaces from your XML snippet
namespaces = {
    'xmp': 'http://ns.adobe.com/xap/1.0/',
    'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
}
#
# Required input:
destination_root = "D:/CurrentPictures/2017/Oostzee zeilzomer"
trip_naam = os.path.basename(destination_root) 
trip_name_en = "Baltic Sea sailing summer"

# # establish cache for translation -- unfortunately it was not very good :-(
# ct = engine.translatelib.CachedTranslate("c:/temp/translate cache.bin")

# Dutch month names
nl_months = {
    "01": "januari", "02": "februari", "03": "maart", "04": "april",
    "05": "mei", "06": "juni", "07": "juli", "08": "augustus",
    "09": "september", "10": "oktober", "11": "november", "12": "december"
}

def process_source_dir(source_dir, dest_root):
    color_labels = ['Label="Green"','>Green<', 'Label="Yellow"','Label="Red"']
    # base logic on XMP files, which means JPGs will not be found!
    for xmp_path in glob.glob(os.path.join(source_dir, "*.xmp")):
        try:
            tree = ET.parse(xmp_path)
            root = tree.getroot()
            xml_text = ET.tostring(root, encoding='unicode')

            # Check if Green label is present
            if any(color in xml_text for color in color_labels):
                # Extract CreateDate.  Pretty ugly, I agree with CoPilot:
                #   You're absolutely right—real-world XMP files are a namespace jungle 🐒. 
                #   Some tools like DxO write simple XMP structures without namespaces, 
                #   while Adobe apps lean hard into RDF/XML with multiple prefixes. 
                #   Reinstating the string-based fallback is actually a very pragmatic solution.
                create_date_element = root.find(".//rdf:Description/xmp:CreateDate", namespaces)
                try:
                    if create_date_element is None: # did not work, try simple version
                        start = xml_text.find('CreateDate="') + len('CreateDate="')
                        end = xml_text.find('"', start)
                        date_time = xml_text[start:end]  # Format: 2017-08-09T17:40:43.89\
                    else:
                        date_time = create_date_element.text  
                except Exception as e:
                    print(f"Error date processing {xmp_path}: {e}")

                date_only = date_time.split('T')[0]

                # Format Dutch and English versions
                dt = datetime.strptime(date_only, "%Y-%m-%d")
                nl_date = f"{dt.day} {nl_months[date_only[5:7]]} {dt.year}"
                en_date = dt.strftime("%B %-d, %Y") if os.name != 'nt' else dt.strftime("%B %#d, %Y")  # Windows-friendly

                # Create destination subdirectory
                dated_dir = os.path.join(dest_root, date_only)
                os.makedirs(dated_dir, exist_ok=True)

                # Create blog.ini if not present
                ini_path = os.path.join(dated_dir, "blog.ini")
                if not os.path.exists(ini_path):
                    # translate holiday name
                    #trip_name_en = ct.translator.translate(trip_naam).text
                    with open(ini_path, "w", encoding="utf-8") as f:
                        f.write(f"""[blog_config]
only_new=False
minimum_score=25
column_width=300
[blog_selection]
species=biota
[blog_text]
titel_nl={trip_naam} foto's van {nl_date}
message_nl=Deze post is gemaakt op basis van oudere foto's tijdens de {trip_naam} vakantie in {dt.year}.
titel_en={trip_name_en} pictures from {en_date}
message_en=This post is based on older photos taken during the {trip_name_en} holiday in {dt.year}.
""")
                                                
                # Match base name of XMP file
                base = os.path.splitext(os.path.basename(xmp_path))[0]
                for ext in [".jpg", ".jpeg", ".nef", "orf", ".cr2", ".arw"]:
                    image_path = os.path.join(source_dir, base + ext)
                    if os.path.exists(image_path):
                        shutil.copy2(image_path, dated_dir)
                        shutil.copy2(xmp_path, dated_dir)
                        if os.path.exists(image_path+'.dop'): # also copy metadata from DxO when present:
                            shutil.copy2(image_path+'.dop', dated_dir)
                        print(f"Copied {base}{ext} + .xmp → {dated_dir}")
                        break
        except Exception as e:
                print(f"Error processing {xmp_path}: {e}")       
    return

# list of directories with picture for this vacation
source_dirs = [
    "E:/Dropbox/My Pictures/2017/2017 augustus noordzee",
    "E:/Dropbox/My Pictures/2017/2017 augustus zeeland"
]

for folder in source_dirs:
    process_source_dir(folder, destination_root)