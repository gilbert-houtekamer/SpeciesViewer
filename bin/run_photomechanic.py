import os
import sys

def makeXML(input_file, output_file):
    with open(input_file, 'r') as infile, open(output_file, 'w') as outfile:
        outfile.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        outfile.write('<KeywordsSettings>\n')
        outfile.write('  <KeywordsArray>\n')
        i = 0
        for current_line, line in enumerate(infile, start=1):
            myline = line.replace("\n","")
            outfile.write(f'    <item_00000{i:03x}>{myline}</item_0000{i:03x}>\n')
            i += 1
        outfile.write('  </KeywordsArray>\n')            
        outfile.write('</KeywordsSettings>\n')

# Usage
input_file = "C:/CurrentPictures/2025/Gambia/names.txt"
output_file = "C:/Users/gilbe/AppData/Roaming/Camera Bits, Inc/Photo Mechanic/IPTCKeywordsSettings/gambialist.snap"

makeXML(input_file, output_file)