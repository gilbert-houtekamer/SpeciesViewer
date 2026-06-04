#
# dictionary with (source, target, size)

import os
import json
# put, only apply to JPG
#
# search by target name, value is path and length on computer
# if target present
#   check if path and length are the same (os.path.getsize(filename))
#   if so, 
#       done
# update/create dictionary
#       copy
#
# save: write dictionary with updates
# read: 

# Example from 
data = {
 'key1': ('string1', 123),
 'key2': ('string2', 456),
 'key3': ('string3', 789)
}

# Convert tuples to lists to make it JSON serializable
data_json = {key: list(value) for key, value in data.items()}

# Write to a file
with open('data.json', 'w') as file:
    json.dump(data_json, file)

### Reading the Dictionary Back from the File

# Read from the file
with open('data.json', 'r') as file:
 data_loaded = json.load(file)

# Convert lists back to tuples
data_loaded = {key: tuple(value) for key, value in data_loaded.items()}

print(data_loaded)
