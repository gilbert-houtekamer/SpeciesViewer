import csv
import os
import googletrans
from googletrans import Translator
from fastcrc import crc32
import logging
logger = logging.getLogger("speciesviewer")


#pip install googletrans==4.0.0-rc1

class CachedTranslate:
    # crc32(source_text) -> {'translation': str, 'source_lang': 'nl'|'en'|'', 'verified': bool}
    # The translation is the OTHER-language version of the source. source_lang
    # records which language the source is, so a later render is deterministic
    # (no re-detection / mis-detection). verified marks a human-confirmed entry.

    def __init__(self, treedir):
        # Instance-level cache: separate CachedTranslate instances (e.g. the
        # GUI's master translator vs a per-trip one) must NOT share state, or
        # one would write the other's entries into the wrong TransCache.csv.
        self.translation_cache = {}
        self.translator = googletrans.Translator()
        self.admindir = os.path.join(treedir, 'admin')
        self._dirty = False
        self.readTranslationache()

    def readTranslationache(self):
        # Backward compatible: old caches have only crctext^translatedtext;
        # source_lang/verified default to ''/False and get filled over time.
        cachefile = os.path.join(self.admindir, 'TransCache.csv')
        if os.path.exists(cachefile):
            # csv.reader (matching the DictWriter) handles CRLF and quoted /
            # multi-line translations correctly — a manual split('^') did not.
            with open(cachefile, mode="r", newline="", encoding='utf-8') as csvfile:
                reader = csv.reader(csvfile, delimiter='^')
                next(reader, None)  # skip header
                for values in reader:
                    if values and values[0] != '':
                        self.translation_cache[int(values[0])] = {
                            'translation': values[1] if len(values) > 1 else '',
                            'source_lang': values[2] if len(values) > 2 else '',
                            'verified': (len(values) > 3 and values[3] == '1'),
                        }
            self.cachedtranslations = len(self.translation_cache)
        else:
            self.cachedtranslations = 0
        self._dirty = False

    def writeTranslationCache(self):
        # Persist whenever anything changed (a new translation, a backfilled
        # source_lang, or a human verification/correction) — not just on growth.
        if not self._dirty:
            return
        cachefile = os.path.join(self.admindir, 'TransCache.csv')
        os.makedirs(self.admindir, exist_ok=True)
        logger.info(f'Writing {len(self.translation_cache)} cached translations to CSV file "{cachefile}".')
        with open(cachefile, "w", newline="", encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile,
                                    fieldnames=['crctext', 'translatedtext', 'source_lang', 'verified'],
                                    quoting=csv.QUOTE_MINIMAL, delimiter='^')
            writer.writeheader()
            for crc, entry in self.translation_cache.items():
                writer.writerow({
                    'crctext': int(crc),
                    'translatedtext': entry['translation'],
                    'source_lang': entry.get('source_lang', ''),
                    'verified': '1' if entry.get('verified') else '0',
                })
        self.cachedtranslations = len(self.translation_cache)
        self._dirty = False

    def detectLanguage(self, text):
        try:
            return self.translator.detect(text)
        except Exception as e:
            logger.warning(f"Translation service unavailable: {e}")
            return None

    # convert text to crc32
    def getCRC32(self, text):
        # Text is identified with crc32 (int), used as the key into the cache
        return crc32.aixm(text.encode('utf-8'))

    def cachedTranslate(self, text, language=''):
        # All translation requests go through the cache. `language` is the SOURCE
        # language; '' means auto-detect. Only two languages exist (nl/en), so the
        # result is always the other one. Stores source_lang + verified=False.
        crctext = self.getCRC32(text)
        if crctext in self.translation_cache:
            return self.translation_cache[crctext]['translation']
        if language == "":
            detected_language = self.detectLanguage(text)
            if detected_language is None:
                return text
            language = detected_language.lang
        if language == 'nl':
            from_lang, to_lang = 'nl', 'en'
        else:
            from_lang, to_lang = 'en', 'nl'
        try:
            translated_text_stuct = self.translator.translate(text, src=from_lang, dest=to_lang)
            translatedText = translated_text_stuct.text.replace('.', '. ').replace('.  ', '. ')
        except Exception as e:
            logger.warning(f"Translation failed, returning original text: {e}")
            return text
        self.translation_cache[int(crctext)] = {
            'translation': translatedText, 'source_lang': from_lang, 'verified': False}
        self._dirty = True
        return translatedText

    def getBothLanguages(self, nederlands, english):
        # two languages => pass
        # one language  => translate to the other language
        # no text       => pass (two "")
        if nederlands != "" and english == "":
            return nederlands, self.cachedTranslate(nederlands, language='nl')
        if nederlands == "" and english != "":
            return self.cachedTranslate(english, language='en'), english
        return nederlands, english

    def getOneLanguage(self, text, languagein, source_lang=None):
        # languagein:  the language I need to print (the page/target language)
        # source_lang: optional known source language (e.g. description-anchored)
        #
        # Cache-first + a known source language make this deterministic: no
        # re-detection, so short proper-noun text can't be mis-detected once we
        # know its language. Source language is taken from, in order: the cached
        # entry, the explicit hint, then detection.
        if text == "":
            return text
        crc = self.getCRC32(text)
        entry = self.translation_cache.get(crc)
        if entry and entry.get('source_lang'):
            src = entry['source_lang']
        elif source_lang in ('nl', 'en'):
            src = source_lang
        else:
            detected = self.detectLanguage(text)
            src = detected.lang if detected else ''
        # Backfill a known source language onto an older (schema-less) entry.
        if entry and not entry.get('source_lang') and src in ('nl', 'en'):
            entry['source_lang'] = src
            self._dirty = True
        if src == languagein:
            return text                              # already in the target language
        if src in ('nl', 'en'):
            return self.cachedTranslate(text, language=src)
        return text                                  # unknown source, leave as-is

    def is_verified(self, text):
        # Empty text needs no verification.
        if text == "":
            return True
        entry = self.translation_cache.get(self.getCRC32(text))
        return bool(entry and entry.get('verified'))

    def mark_verified(self, text, corrected=None, source_lang=None):
        # Confirm (and optionally correct) the translation of `text`. Used by the
        # validation panel. `corrected` replaces the stored translation; verified
        # is set True and never overwritten by machine translation afterwards.
        if text == "":
            return
        crc = self.getCRC32(text)
        entry = self.translation_cache.get(crc)
        if entry is None:
            entry = {'translation': corrected or '', 'source_lang': source_lang or '', 'verified': True}
            self.translation_cache[int(crc)] = entry
        else:
            if corrected is not None:
                entry['translation'] = corrected
            if source_lang in ('nl', 'en'):
                entry['source_lang'] = source_lang
            entry['verified'] = True
        self._dirty = True
