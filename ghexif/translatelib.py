import csv
import googletrans
from googletrans import Translator
from fastcrc import crc32

#pip install googletrans==4.0.0-rc1

class CachedTranslate:
    translation_cache = {}
    admindir = ""

    def __init__(self, treedir):
        self.translator = googletrans.Translator()
        self.admindir = treedir + 'admin/'

    def readTranslationache(self):
        cachefile = self.admindir + 'TransCache.csv'
        print(f'Read all cached translations from CSV file "{cachefile}".')
        with open(cachefile, mode="r", newline="", encoding='utf-8') as csvfile:
            header = csvfile.readline().strip().split('^') 
            for line in csvfile:
                values = line.strip().split('^')
                if values[0] != '':
                    self.translation_cache[int(values[0])] = values[1]

        self.cachedtranslations = len(self.translation_cache)
        print(f'Read {self.cachedtranslations} cached translations from CSV file "{cachefile}".')

    def writeTranslationache(self):
        if len(self.translation_cache) > self.cachedtranslations:
            cachefile = self.admindir + 'TransCache.csv'
            print(f'Write {len(self.translation_cache)} cached translations ({
                len(self.translation_cache)-self.cachedtranslations} added) in CSV file "{cachefile}".') 
            with open(cachefile, "w", newline="", encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=['crctext','translatedtext'], 
                                            quoting=csv.QUOTE_MINIMAL, delimiter='^')
                writer.writeheader()
                for transtext in self.translation_cache:
                    transobj = self.translation_cache[transtext]
                    row = {}
                    row['crctext'] = int(transtext)
                    row['translatedtext'] = transobj
                    writer.writerow(row)
                      
    def detectLanguage(self, text):
        detected_language = self.translator.detect(text)
        return detected_language

    # convert text to crc32
    def getCRC32(self, text):
        return crc32.aixm(text.encode('utf-8'))
    
    def cachedTranslate(self, text, language=''):
        crctext = self.getCRC32(text)
        if crctext in self.translation_cache:
            translatedText = self.translation_cache[crctext]
            print(f"in cache, translation is: {translatedText}")
        else:
            if language == "":
                detected_language = self.detectLanguage(text)
                language = detected_language.lang
            if language == 'nl':
                from_lang = 'nl'
                to_lang = 'en'
            else:
                from_lang = 'en'
                to_lang = 'nl'
            translated_text_stuct = self.translator.translate(text, src=from_lang, dest=to_lang)
            translatedText = translated_text_stuct.text.replace('.','. ').replace('.  ','. ')
            self.translation_cache[int(crctext)] = translatedText
            print(f"not in, now translation from {from_lang} to {to_lang}: {translatedText}")
            
        return translatedText


