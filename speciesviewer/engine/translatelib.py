import csv
import os
import time
from collections import namedtuple
from deep_translator import GoogleTranslator
from deep_translator.exceptions import TooManyRequests
from langdetect import detect as _langdetect_detect, DetectorFactory
from fastcrc import crc32
import logging
logger = logging.getLogger("speciesviewer")

# Translation backend: deep-translator (GoogleTranslator) — actively maintained.
# Language detection: langdetect (offline, deterministic). This replaced
# googletrans 4.0.0-rc1, whose translate()/detect() blow up whenever Google
# throttles the unofficial endpoint (a raise_Exception typo in its client.py).
# pip install deep-translator langdetect
DetectorFactory.seed = 0  # make langdetect deterministic across runs

# googletrans.detect() returned an object with a .lang attribute; keep that shape
# so existing callers (detected.lang) don't change.
_Detected = namedtuple("_Detected", "lang")

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
        # deep-translator is stateless/per-call (GoogleTranslator(source, target)),
        # so there is no persistent translator object to hold here.
        self.admindir = os.path.join(treedir, 'admin')
        self._dirty = False
        # Set to str by cachedTranslate whenever a LIVE translation call failed
        # (cache hits never touch it). A caller that wants to report accurately
        # resets it to None before its batch, then checks it after — the network
        # failure is otherwise swallowed and the original text returned.
        self.last_error = None
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

    def writeTranslationCache(self, on_locked=None) -> bool:
        """Persist the cache whenever anything changed. Returns True on success (or when
        there is nothing to save).

        TransCache.csv lives in the master admin, which can be open in Excel or mid
        Dropbox-sync — a plain write then dies with PermissionError. We never crash on
        that: the cache is kept in memory (still marked dirty, so it saves next time) and
        this returns False. An interactive caller may pass `on_locked(err) -> "retry" |
        "skip"` to let the user free the file and try again."""
        if not self._dirty:
            return True
        cachefile = os.path.join(self.admindir, 'TransCache.csv')
        os.makedirs(self.admindir, exist_ok=True)
        while True:
            try:
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
                return True
            except OSError as e:
                logger.warning(
                    f'Could not write "{cachefile}": {e}. It is probably open in Excel or '
                    f'syncing in Dropbox — the translations are kept and will be saved next '
                    f'time.')
                if on_locked is None or on_locked(e) != "retry":
                    return False
                # else loop and try again — the user has (hopefully) freed the file.

    def detectLanguage(self, text):
        # Offline detection via langdetect. Returns a _Detected(lang=...) shim so
        # callers keep using .lang. langdetect yields ISO codes ('nl', 'en', …);
        # our callers only act on 'nl'/'en' and ignore anything else.
        if not text or not text.strip():
            return None
        try:
            return _Detected(_langdetect_detect(text))
        except Exception as e:
            logger.warning(f"Language detection failed, leaving source unknown: {e}")
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
        translated = self._live_translate(text, from_lang, to_lang)
        if not translated:
            logger.warning(f"Translation failed, returning original text: {self.last_error}")
            return text
        translatedText = translated.replace('.', '. ').replace('.  ', '. ')
        self.translation_cache[int(crctext)] = {
            'translation': translatedText, 'source_lang': from_lang, 'verified': False}
        self._dirty = True
        return translatedText

    # When Google refuses this internet connection (its "unusual traffic" page,
    # which deep_translator reports as "too many requests" whatever the real
    # reason), stop asking it for a while: every further try costs ~1.5 s and
    # one more identical log line. Class-level, so it holds across the several
    # CachedTranslate instances one session creates; time-limited, because the
    # block lifts on its own (hours, or a different network).
    _GOOGLE_PAUSE_S = 30 * 60
    _google_blocked_until = 0.0
    _GOOGLE_BLOCKED_MSG = ("Google is blocking automatic translation from this "
                           "internet connection")

    # MyMemory wants region-tagged codes and caps a request at 500 characters.
    _MYMEM_REGION = {'nl': 'nl-NL', 'en': 'en-GB'}
    _MYMEM_LIMIT = 480

    def _live_translate(self, text, from_lang, to_lang):
        """One live translation. Google first (best quality); if its unofficial
        endpoint is down — which it is, periodically, by design — fall back to
        MyMemory, a real documented API. Returns '' when both fail, with the
        reason in self.last_error; '' is never cached, so it retries next run."""
        cls = CachedTranslate
        if time.time() < cls._google_blocked_until:
            self.last_error = cls._GOOGLE_BLOCKED_MSG
        else:
            try:
                out = GoogleTranslator(source=from_lang, target=to_lang).translate(text)
                if out:
                    return out
                self.last_error = "Google Translate returned nothing"
            except TooManyRequests:
                cls._google_blocked_until = time.time() + cls._GOOGLE_PAUSE_S
                self.last_error = cls._GOOGLE_BLOCKED_MSG
                logger.info(f"Translation: {cls._GOOGLE_BLOCKED_MSG}; using MyMemory "
                            f"for the next {cls._GOOGLE_PAUSE_S // 60} minutes.")
            except Exception as e:
                self.last_error = f"Google Translate: {e}"
        try:
            from deep_translator import MyMemoryTranslator
            mm = MyMemoryTranslator(source=self._MYMEM_REGION.get(from_lang, from_lang),
                                    target=self._MYMEM_REGION.get(to_lang, to_lang))
            parts = self._split_for_limit(text, self._MYMEM_LIMIT)
            out = "".join(mm.translate(p) for p in parts)
            if out:
                if self.last_error != cls._GOOGLE_BLOCKED_MSG:
                    logger.info(f"Translation: Google unavailable ({self.last_error}); "
                                f"used MyMemory instead.")
                self.last_error = None
                return out
        except Exception as e:
            self.last_error = f"{self.last_error}; MyMemory: {e}"
        return ""

    @staticmethod
    def _split_for_limit(text, limit):
        """Break `text` into <=limit-char pieces at sentence, then word,
        boundaries — for translators that cap a single request."""
        if len(text) <= limit:
            return [text]
        import re
        out, buf = [], ""
        for piece in re.split(r'(?<=\. )', text):
            if len(buf) + len(piece) <= limit:
                buf += piece
                continue
            if buf:
                out.append(buf)
                buf = ""
            while len(piece) > limit:
                cut = piece.rfind(' ', 0, limit)
                cut = cut if cut > 0 else limit
                out.append(piece[:cut])
                piece = piece[cut:].lstrip()
            buf = piece
        if buf:
            out.append(buf)
        return out

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
