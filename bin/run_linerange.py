import os
import sys

def select_lines(input_file, output_file, start_line, end_line):
    with open(input_file, 'r') as infile, open(output_file, 'w') as outfile:
        for current_line, line in enumerate(infile, start=1):
            if start_line <= current_line <= end_line:
                outfile.write(line)
            elif current_line > end_line:
                break

# Usage
input_file = "C:/CurrentPictures/2024/2024 Zuid Afrika/logs/20240624 Elly.log"
output_file = "C:/CurrentPictures/2024/2024 Zuid Afrika/logs/20240624 Elly select 4.log"
start_line = 220000
end_line = 320000

select_lines(input_file, output_file, start_line, end_line)