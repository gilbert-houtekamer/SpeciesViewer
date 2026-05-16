import os
import re
result_dict = {}
headers = []

def scan_files(directory, search_string):

    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.txt'):
                file_path = os.path.join(root, file)
                parseddir = root.split('\\')
                name = parseddir[1]
                headers.append(name)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if search_string in content:
                        #print(f"'{search_string}' found in {file_path}")
                        lines = content.split('\n')
                        for i, line in enumerate(lines):
                            if search_string in line:
                                for subsequent_line in lines[i+1:]:
                                    if subsequent_line.strip():  # Check if the line is non-empty
                                        tokens = re.split(r'[()/]', subsequent_line)
                                        key = tokens[1].strip()
                                        if key in result_dict:
                                            result_dict[key].append(name)
                                        else:
                                            result_dict[key] = [name]
                    # else:
                    #     print(f"'{search_string}' not found in {file_path}")

    counter = 0
    header_line = ' '.ljust(35)
    for header in headers:
        header_line += header[6:] + ' '
    print(header_line)
    for species in result_dict:
        data_line = str(counter).rjust(3) + ' ' + species.ljust(32)
        counter += 1
        seen = 0
        for header in headers:
            if header in result_dict[species]:
                data_line += 'x  '
                seen += 1
            else:
                data_line += '.  '
        print(data_line, f" {seen:3} ")

# Example usage
directory_to_scan = 'C:/laptop/validate/metadata/review'
search_string = 'Species for which there are no JPGs defined at all:'
scan_files(directory_to_scan, search_string)