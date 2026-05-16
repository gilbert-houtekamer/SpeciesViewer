import csv

input_file = "E:/Dropbox/trips/extramaduradb/ascii lijst.txt"  
output_file =  "E:/Dropbox/trips/extramaduradb/soorten.csv"

rows = []

with open(input_file, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue

        parts = line.split()

        # 1) Nummer is het eerste element
        nummer = parts[0]

        # 2) Wetenschappelijke naam = laatste twee woorden
        wet_naam = " ".join(parts[-2:])

        # 3) Nederlandse naam = alles tussen nummer en wetenschappelijke naam
        nl_naam = " ".join(parts[1:-2])

        rows.append([nummer, nl_naam, wet_naam])

# Schrijf CSV
with open(output_file, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f, delimiter=";")
    writer.writerows(rows)

print("Klaar. Bestand geschreven naar", output_file)
