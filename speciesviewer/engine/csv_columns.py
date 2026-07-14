# for species_dict and corresponding CSV
COLUMN_NAMES = {
    "-filename": "File:FileName",
    "-WindowsExtension": "WindowsExtension",
    "-WindowsFileName": "WindowsFileName",
    "-directory": "File:Directory",
    "-UniquePictureName": "UniquePictureName",      
    "-RawFileName": "XMP:RawFileName",
    "-filetype": "File:FileType",
    "-filetypeextension": "File:FileTypeExtension",
    "-MIMEType": "File:MIMEType",
    # "-IFD0:Make": "EXIF:Make",
    # "-XMP-tiff:Make": "XMP:Make",
    "-CameraMake": "CameraMake",    
#    "-IFD0:Model": "EXIF:Model",
#    "-XMP-tiff:Model": "XMP:Model",
    "-CameraModel": "CameraModel",
    "-IFD0:Software": "EXIF:Software",
    "-XMP-xmp:CreatorTool": "XMP:CreatorTool",
    "-Olympus:SerialNumber": "MakerNotes:SerialNumber",
    # "-ImageSize": "Composite:ImageSize",
    "-ImageSize": "ImageSize",
    "-lensid": "Composite:LensID",
    "-lensmodel": "EXIF:LensModel",
    "-lensinfo": "EXIF:LensInfo",
    "-lenstype": "MakerNotes:LensType",
    #"-lensserialnumber": "MakerNotes:LensSerialNumber",
    "-LensSerialNumber": "LensSerialNumber", 
    #"-exifIFD:lensserialnumber": "EXIF:LensSerialNumber",
    "-MinFocalLength": "MakerNotes:MinFocalLength",
    "-MaxFocalLength": "MakerNotes:MaxFocalLength",
    "-FocalLength": "EXIF:FocalLength",
    "-FocalLengthIn35mmFormat": "EXIF:FocalLengthIn35mmFormat",
    "-ExposureTime": "EXIF:ExposureTime",
    "-ISO": "EXIF:ISO",
    "-fnumber": "EXIF:FNumber",
    "-aperture": "Composite:Aperture",
    "-ExposureProgram": "EXIF:ExposureProgram",
    "-SensitivityType": "EXIF:SensitivityType",
    "-Olympus:FocusDistance": "MakerNotes:FocusDistance",
    "-SubjectDistance": "EXIF:SubjectDistance",
    "-dof": "Composite:DOF",
    # "-imagesize": "Composite:ImageSize",
    "-GPSAltitude": "EXIF:GPSAltitude",
    "-gpsposition": "Composite:GPSPosition",
    "-gpslatitude": "EXIF:GPSLatitude",
    "-gpslongitude": "EXIF:GPSLongitude",
    # HOW the fix was obtained. Empty (or anything else) = the camera recorded it,
    # and it is not ours to overwrite. "MANUAL" = a human placed it — dragged onto
    # the map, or derived from a place name — so it may be moved and refined. This
    # is the PROVENANCE the map's move rule reads; the coordinate's precision is a
    # separate, human-facing signal and must never be used to infer permission
    # (1.9% of the master's real fixes already print as whole arcseconds).
    "-gpsprocessingmethod": "EXIF:GPSProcessingMethod",
    "-location": "XMP:Location",
    "-city": "XMP:City",
    "-Municipality": "XMP:Municipality",
    "-state": "XMP:State",
    "-country": "XMP:Country",
    "-HistoryParameters": "XMP:HistoryParameters",
    "-HistorySoftwareAgent": "XMP:HistorySoftwareAgent",
    "-profiledescription": "ICC_Profile:ProfileDescription",
    "-rating": "XMP:Rating",
    "-label": "XMP:Label",
    "-subject": "XMP:Subject",
    "-keywords": "IPTC:Keywords",
    "-DateTimeOriginal": "EXIF:DateTimeOriginal",
    "-description": "XMP:Description",
    "-XMP:Headline": "XMP:Headline",
}

# below for documentation only, can be obtained from 
#    list(COLUMN_NAMES.values())
DATA_FILE_FIELDS = [
    "File:FileName",
    "WindowsExtension",
    "WindowsFileName",
    "File:Directory",
    "UniquePictureName",
    "XMP:RawFileName",
    "File:FileType",
    "File:FileTypeExtension",
    "File:MIMEType",
    "CameraMake",
    "CameraModel",
    "EXIF:Software",
    "XMP:CreatorTool",
    "MakerNotes:SerialNumber",
    "ImageSize",
    "Composite:LensID",
    "EXIF:LensModel",
    "EXIF:LensInfo",
    "MakerNotes:LensType",
    "LensSerialNumber",
    "MakerNotes:MinFocalLength",
    "MakerNotes:MaxFocalLength",
    "EXIF:FocalLength",
    "EXIF:FocalLengthIn35mmFormat",
    "EXIF:ExposureTime",
    "EXIF:ISO",
    "EXIF:FNumber",
    "Composite:Aperture",
    "EXIF:ExposureProgram",
    "EXIF:SensitivityType",
    "MakerNotes:FocusDistance",
    "EXIF:SubjectDistance",
    "Composite:DOF",
    "EXIF:GPSAltitude",
    "Composite:GPSPosition",
    "EXIF:GPSLatitude",
    "EXIF:GPSLongitude",
    "XMP:Location",
    "XMP:City",
    "XMP:Municipality",
    "XMP:State",
    "XMP:Country",
    "XMP:HistoryParameters",
    "XMP:HistorySoftwareAgent",
    "ICC_Profile:ProfileDescription",
    "XMP:Rating",
    "XMP:Label",
    "XMP:Subject",
    "IPTC:Keywords",
    "EXIF:DateTimeOriginal",
    "XMP:Description",
    "XMP:Headline",
    # NEW COLUMNS GO AT THE END (a frozen CSV is re-headed against this list, so an
    # insertion in the middle would shift every existing value one column left).
    "EXIF:GPSProcessingMethod"
]

SPECIES_FIELDS = [
    "Level", 
    "ScientificName",
    "DutchName",
    "EnglishName",
    "Up",
    "Leaves",
    "JPGmap",
    "currentHighestRating",
    "BestJPG",
    "BestUPN",    
    "BestJPGPathName",
    "TargetPath",
    "iSpeciesThisRunCount",
    "iSpeciesCollectionCount",    
    "iPictureThisRunCount",
    "iPictureCollectionCount",
    "iPicturesWithJPGsThisRun",    
    "iPicturesWithJPGsCollection"
]

PICTURE_FIELDS = [
    "UniquePictureName",
    "subject",
    "Rating",
    "Label",
    "Rate",
    "WetSoortNaam",
    "EngSoortNaam",
    "NLSoortNaam",
    "ImageSize",
    "JPGSize",
    "GPSAltitude",
    "GPSPosition",
    "Location",
    "City",
    "State",
    "Country",
    "CameraCreator",
    "JPGCreator",
    "CameraModel",
    "LensShortName",
    "LensLongName",
    "Settings",
    "Kingdom",
    "Phylum",    
    "Class",
    "Order",
    "Family",
    "BestPictureFile",  # obsolete?
    "currentHighestRating", # replacement
    "BestJPG",
    "BestJPGPathName",
    "TargetPath",
    "Description",
    "Files",
    "DateTimeOriginal",
    "malefemale",
    "ageintext",
    "summerwinter",
    "errormessage",
    # Municipality shows between City and State on the website (see websupport
    # getFormattedCityStateCountry); added near the end per the "new columns at
    # end" convention, but before Headline so Headline stays the sacrificial last.
    "Municipality",
    # How this picture's GPS was obtained: "" = the camera recorded it (do not
    # overwrite); "MANUAL" = a human placed it (map drag / place-name lookup), so
    # it may be moved and refined. Placed here for the same reason as Municipality:
    # near the end, but BEFORE the sacrificial Headline.
    "GPSProcessingMethod",
    # The JPG a HUMAN chose as this picture's public face, or "" to let the code
    # decide. Everything that picks a JPG picks the BIGGEST one — but more pixels is
    # not always better: a tighter crop of a warbler in reed can beat a wider frame
    # with more of them, and that is a subjective call no rule can make. Without this
    # there is nowhere for that call to LIVE, so re-deriving would always overrule it.
    # BestJPG stays fully derived — from the pin when set, from area when not — so
    # there is still exactly one rule, and one place a human can lean on it.
    # Same placement reason as Municipality: near the end, but BEFORE Headline.
    "BestJPGPinned",
    # Headline LAST on purpose — so a trailing column drifts off a stale master header harmlessly.
    # Blog extras (the only Headline carriers) are excluded from the master,
    # so a trailing column drifts off the stale master header harmlessly
    # rather than shifting Files / DateTimeOriginal under wrong header names.
    "Headline",
]