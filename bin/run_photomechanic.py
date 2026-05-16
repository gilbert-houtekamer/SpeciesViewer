import os
import sys

specieslist = []

def makeXML(output_file):
    with open(output_file, 'w') as outfile:
        outfile.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        outfile.write('<KeywordsSettings>\n')
        outfile.write('  <KeywordsArray>\n')
        i = 0
        for name in specieslist:
            outfile.write(f'    <item_0000{i:03x}>{name}</item_0000{i:03x}>\n')
            i += 1
        outfile.write('  </KeywordsArray>\n')            
        outfile.write('</KeywordsSettings>\n')

def readNames(input_file):
    with open(input_file, 'r') as infile:
         for line in enumerate(infile, start=1):
            myline = line[1].replace("\n","").lower().replace("gray","grey").replace("  "," ")
            if myline not in specieslist:
                specieslist.append(myline)

    # sort them
    specieslist.sort()
    return

# Usage
input_file = "C:/CurrentPictures/2025/Gambia/names.txt"
output_file = "C:/Users/gilbe/AppData/Roaming/Camera Bits, Inc/Photo Mechanic/IPTCKeywordsSettings/gambialist.snap"

readNames(input_file)
makeXML(output_file)