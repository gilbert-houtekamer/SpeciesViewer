import atexit
import csv
import re
import os
import threading
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

# An opening link tag as written (<a href="…" target="_blank">), and the same
# thing after a translator has been at it: spaces inserted anywhere, "< a",
# "a href = ' … '" — anything from "<", optional spaces, "a" to the next ">".
_OPEN_TAG = re.compile(r'<a\b[^>]*>', re.IGNORECASE)
_OPEN_TAG_MANGLED = re.compile(r'<\s*a\s[^>]*>', re.IGNORECASE)
_CLOSE_TAG_MANGLED = re.compile(r'<\s*/\s*a\s*>', re.IGNORECASE)


# A full stop glued to the next sentence: "nacht.De" — a capital (any accent)
# right after it. Not "a.m.", "e.g.", "3.5" or "hotelmt.com" (no capital).
_GLUED_SENTENCE = re.compile(r'\.(?=[A-ZÀ-ÖØ-Þ])')
_TAG = re.compile(r'(<[^>]*>)')


def space_after_sentences(text: str) -> str:
    """Put a space between sentences a translator glued together ("nacht.De").
    Replaces the old blanket '.' → '. ', which also split abbreviations
    ("a.m." → "a. m. ,") and link addresses. HTML tags are left untouched."""
    parts = _TAG.split(text)          # odd indexes are the tags themselves
    return "".join(p if i % 2 else _GLUED_SENTENCE.sub(". ", p)
                   for i, p in enumerate(parts))


def restore_links(source: str, translated: str) -> str:
    """Put the source's <a …> opening tags back into its translation, in order.

    Only a link's WORDS should be translated; its address and attributes must
    come through byte-for-byte, and they don't: our '. ' sentence spacing and
    the translators themselves insert spaces ("hotelmt. com. br" → %20 in the
    browser). When the counts don't match the translator dropped or merged a
    link — leave the text alone and say so rather than guess."""
    tags = _OPEN_TAG.findall(source)
    if not tags:
        return translated
    found = _OPEN_TAG_MANGLED.findall(translated)
    if len(found) != len(tags):
        logger.warning(f"Translation: {len(tags)} link(s) in the source but {len(found)} "
                       f"in the translation — links left as translated; check the text.")
        return translated
    it = iter(tags)
    out = _OPEN_TAG_MANGLED.sub(lambda _m: next(it), translated)
    return _CLOSE_TAG_MANGLED.sub("</a>", out)


# ── One translation memory per computer ─────────────────────────────────────
# Every trip and the master keep their own admin/TransCache.csv; that stays as
# it is (trip export carries it, the trip overview counts its verified ticks).
# Next to it sits ONE shared memory in this computer's app-data, so a text
# translated for a trip is not asked again for the master, an album or the
# local website. Per computer on purpose — app-data, never Dropbox.

_FIELDS = ['crctext', 'translatedtext', 'source_lang', 'verified']


def _read_cache_file(path):
    """{crc: entry} from a TransCache.csv; {} when there is none.
    Backward compatible: old caches have only crctext^translatedtext."""
    out = {}
    if not os.path.exists(path):
        return out
    # csv.reader (matching the DictWriter) handles CRLF and quoted /
    # multi-line translations correctly — a manual split('^') did not.
    with open(path, mode="r", newline="", encoding='utf-8') as csvfile:
        reader = csv.reader(csvfile, delimiter='^')
        next(reader, None)  # skip header
        for values in reader:
            if values and values[0] != '':
                out[int(values[0])] = {
                    'translation': values[1] if len(values) > 1 else '',
                    'source_lang': values[2] if len(values) > 2 else '',
                    'verified': (len(values) > 3 and values[3] == '1'),
                }
    return out


def _write_cache_file(path, entries):
    with open(path, "w", newline="", encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=_FIELDS,
                                quoting=csv.QUOTE_MINIMAL, delimiter='^')
        writer.writeheader()
        for crc, entry in entries.items():
            writer.writerow({
                'crctext': int(crc),
                'translatedtext': entry['translation'],
                'source_lang': entry.get('source_lang', ''),
                'verified': '1' if entry.get('verified') else '0',
            })


def _better(first, second):
    """Of two entries for one text, the one to keep: a hand-verified one beats
    an unverified one (losing a verification is losing work a person did);
    otherwise `first`."""
    if first is None:
        return second
    if second is not None and second.get('verified') and not first.get('verified'):
        return second
    return first


class _SharedTranslations:
    """The computer-wide memory. One per process, shared by every CachedTranslate
    (the GUI's and a pipeline step's on its worker thread — hence the lock).
    Saving re-reads the file and merges, so a second SpeciesViewer process
    writing the same file loses nothing either."""

    def __init__(self, path=None):
        self._lock = threading.RLock()
        self._path = path
        self._entries = None        # loaded on first use
        self._changed = set()

    def _ensure(self):
        if self._entries is None:
            if self._path is None:
                from utils.cache_paths import app_data_dir
                self._path = str(app_data_dir() / "translations" / "TransCache.csv")
            try:
                self._entries = _read_cache_file(self._path)
            except (OSError, ValueError, csv.Error) as e:
                logger.warning(f'Could not read the shared translations "{self._path}": {e}')
                self._entries = {}

    def get(self, crc):
        with self._lock:
            self._ensure()
            return self._entries.get(crc)

    def put(self, crc, entry, keep_existing=False):
        """Store `entry` unless the one already there is better. keep_existing:
        on a tie the stored one stays (folding a repository in); otherwise the
        new one wins (a fresh or corrected translation)."""
        if not entry or not entry.get('translation'):
            return
        with self._lock:
            self._ensure()
            current = self._entries.get(crc)
            best = _better(current, entry) if keep_existing else _better(entry, current)
            if best is entry and entry != current:
                self._entries[crc] = dict(entry)
                self._changed.add(crc)

    def save(self) -> bool:
        with self._lock:
            if not self._changed:
                return True
            try:
                merged = _read_cache_file(self._path)
                for crc in self._changed:
                    merged[crc] = _better(self._entries[crc], merged.get(crc))
                for crc, entry in self._entries.items():   # never drop one we hold
                    merged.setdefault(crc, entry)
                os.makedirs(os.path.dirname(self._path), exist_ok=True)
                tmp = self._path + ".tmp"
                _write_cache_file(tmp, merged)
                os.replace(tmp, self._path)
            except (OSError, ValueError, csv.Error) as e:
                logger.warning(f'Could not save the shared translations "{self._path}": {e} '
                               f'— kept in memory, saved next time.')
                return False
            self._entries = merged
            self._changed.clear()
            return True


_shared = _SharedTranslations()

# Every CachedTranslate holding unsaved translations, with the thread that made
# the change, so a pipeline step's are saved when the step ends
# (save_translators) — the website builds never called writeTranslationCache,
# so every rebuild asked again. A strong reference on purpose: by the time the
# step has returned, its Project (and translator) is otherwise gone.
_unsaved = {}
_unsaved_lock = threading.Lock()


def save_translators(this_thread_only=True):
    """Save the translators changed on this thread (a finished pipeline step),
    or all of them (program exit). Never raises."""
    me = threading.get_ident()
    with _unsaved_lock:
        pending = list(_unsaved.items())
    for tr, owner in pending:
        if this_thread_only and owner != me:
            continue
        try:
            tr.writeTranslationCache()
        except Exception as e:
            logger.warning(f"Saving translations failed: {e}")
    _shared.save()


atexit.register(save_translators, this_thread_only=False)


class CachedTranslate:
    # crc32(source_text) -> {'translation': str, 'source_lang': 'nl'|'en'|'', 'verified': bool}
    # The translation is the OTHER-language version of the source. source_lang
    # records which language the source is, so a later render is deterministic
    # (no re-detection / mis-detection). verified marks a human-confirmed entry.

    # Save every this many new live translations: a paced bulk run takes long,
    # and a cancel or crash must not throw the finished part away.
    _AUTOSAVE_EVERY = 50

    def __init__(self, treedir, patient=False):
        # Instance-level cache: separate CachedTranslate instances (e.g. the
        # GUI's master translator vs a per-trip one) must NOT share state, or
        # one would write the other's entries into the wrong TransCache.csv.
        # What they DO share is the computer-wide memory (_shared).
        self.translation_cache = {}
        # deep-translator is stateless/per-call (GoogleTranslator(source, target)),
        # so there is no persistent translator object to hold here.
        self.admindir = os.path.join(treedir, 'admin')
        self._dirty = False
        # patient: a bulk run (pipeline step) — when a service refuses, wait and
        # try again (see _paced_translate). A button click in the GUI is not.
        self.patient = patient
        self._new_since_save = 0
        # Set to str by cachedTranslate whenever a LIVE translation call failed
        # (cache hits never touch it). A caller that wants to report accurately
        # resets it to None before its batch, then checks it after — the network
        # failure is otherwise swallowed and the original text returned.
        self.last_error = None
        self.readTranslationache()
        # Fold this repository into the shared memory; nothing is lost on a
        # tie, and a hand-verified entry wins either way.
        for crc, entry in self.translation_cache.items():
            _shared.put(crc, entry, keep_existing=True)

    @property
    def _dirty(self):
        return self.__dict__.get('_dirty_flag', False)

    @_dirty.setter
    def _dirty(self, value):
        # Unsaved work registers itself for save_translators; saved work leaves.
        self.__dict__['_dirty_flag'] = value
        with _unsaved_lock:
            if value:
                _unsaved.setdefault(self, threading.get_ident())
            else:
                _unsaved.pop(self, None)

    def readTranslationache(self):
        # Backward compatible: old caches have only crctext^translatedtext;
        # source_lang/verified default to ''/False and get filled over time.
        self.translation_cache = _read_cache_file(os.path.join(self.admindir, 'TransCache.csv'))
        self.cachedtranslations = len(self.translation_cache)
        self._dirty = False

    def _lookup(self, crc):
        """This repository's entry for a text, or the shared memory's when that
        one is better (or the only one). It is copied in, so the repository's
        own file stays complete: trip export and the trip overview's verified
        count read that file."""
        own = self.translation_cache.get(crc)
        best = _better(own, _shared.get(crc))
        if best is not own:
            best = dict(best)
            self.translation_cache[crc] = best
            self._dirty = True
        return best

    def writeTranslationCache(self, on_locked=None) -> bool:
        """Persist the cache whenever anything changed. Returns True on success (or when
        there is nothing to save).

        TransCache.csv lives in the master admin, which can be open in Excel or mid
        Dropbox-sync — a plain write then dies with PermissionError. We never crash on
        that: the cache is kept in memory (still marked dirty, so it saves next time) and
        this returns False. An interactive caller may pass `on_locked(err) -> "retry" |
        "skip"` to let the user free the file and try again.

        The computer-wide memory is saved too; a failure there is only logged."""
        _shared.save()
        self._new_since_save = 0
        if not self._dirty:
            return True
        cachefile = os.path.join(self.admindir, 'TransCache.csv')
        os.makedirs(self.admindir, exist_ok=True)
        while True:
            try:
                logger.debug(f'Writing {len(self.translation_cache)} cached translations to CSV file "{cachefile}".')
                _write_cache_file(cachefile, self.translation_cache)
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
        entry = self._lookup(crctext)
        if entry is not None:
            # Repair on read too: entries cached before the link fix hold
            # mangled hrefs, and this mends them without re-translating.
            return restore_links(text, entry['translation'])
        if language == "":
            detected_language = self.detectLanguage(text)
            if detected_language is None:
                return text
            language = detected_language.lang
        if language == 'nl':
            from_lang, to_lang = 'nl', 'en'
        else:
            from_lang, to_lang = 'en', 'nl'
        translated = self._paced_translate(text, from_lang, to_lang)
        if not translated:
            logger.warning(f"Translation failed, returning original text: {self.last_error}")
            return text
        translatedText = space_after_sentences(translated)
        # That '. ' spacing (and the translator itself) breaks the address inside
        # a link: "hotelmt.com.br" → "hotelmt. com. br". Put the original tags back.
        translatedText = restore_links(text, translatedText)
        entry = {'translation': translatedText, 'source_lang': from_lang, 'verified': False}
        self.translation_cache[int(crctext)] = entry
        _shared.put(int(crctext), entry)
        self._dirty = True
        self._new_since_save += 1
        if self._new_since_save >= self._AUTOSAVE_EVERY:
            self.writeTranslationCache()
        return translatedText

    # Pacing for a bulk run (a website rebuild, a re-published trip, a merge):
    # thousands of new texts in a row make the free services refuse, and every
    # refused text went onto the page untranslated. Slower is accepted.
    _GAP_FREE_S = 1.5           # between live calls to the free services
    _GAP_OFFICIAL_S = 0.1       # Google's paid API takes far more
    _RETRY_WAITS_S = (15, 60, 180)   # patient only: wait, then ask again
    _gap_lock = threading.Lock()
    _last_live_call = 0.0

    def _keep_gap(self):
        cls = CachedTranslate
        gap = (cls._GAP_OFFICIAL_S if self._official_key() and not cls._official_off_reason
               else cls._GAP_FREE_S)
        with cls._gap_lock:
            wait = cls._last_live_call + gap - time.time()
            if wait > 0:
                time.sleep(wait)
            cls._last_live_call = time.time()

    def _paced_translate(self, text, from_lang, to_lang):
        """_live_translate; for a patient (bulk) translator with a gap between
        calls and a few waits-and-retries when every service refused. A GUI
        button asks two or three texts — no pacing there.
        A refusal that waiting cannot cure (MyMemory's daily quota) stops the
        retries at once."""
        waits = self._RETRY_WAITS_S if self.patient else ()
        for attempt, wait in enumerate((*waits, None)):
            if self.patient:
                self._keep_gap()
            out = self._live_translate(text, from_lang, to_lang)
            if out or wait is None or CachedTranslate._mymemory_quota_until > time.time():
                return out
            logger.info(f"Translation refused ({self.last_error}); trying again in "
                        f"{wait} s (attempt {attempt + 2} of {len(waits) + 1}).")
            time.sleep(wait)
        return ""

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
    # MyMemory's daily quota: when it runs out it answers "OK" with the text
    # "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY…",
    # which would otherwise be cached and published as the translation.
    # Waiting minutes does not cure it; stop asking it for a few hours.
    _MYMEM_QUOTA_PAUSE_S = 6 * 3600
    _mymemory_quota_until = 0.0

    # Google's OFFICIAL Cloud Translation API (basic, v2), used first when this
    # machine's config has [translate] google_api_key. Unlike the free web page
    # below it is not refused on VPNs or shared networks. The key lives only in
    # sv_config_<host>.ini (gitignored, never copied between machines).
    _OFFICIAL_URL = "https://translation.googleapis.com/language/translate/v2"
    _official_off_reason = None     # set once the key is refused: stop asking

    @staticmethod
    def _official_key():
        try:
            from utils.config import get_setting
            return (get_setting("translate", "google_api_key", fallback="") or "").strip()
        except Exception:
            return ""

    def _official_translate(self, text, from_lang, to_lang):
        """Translate via the Cloud Translation API. Returns '' when there is no
        key or the call fails (reason in self.last_error)."""
        cls = CachedTranslate
        key = self._official_key()
        if not key or cls._official_off_reason:
            return ""
        import requests
        try:
            r = requests.post(cls._OFFICIAL_URL, params={"key": key},
                              data={"q": text, "source": from_lang, "target": to_lang,
                                    "format": "text"}, timeout=15)
            if r.status_code == 200:
                return r.json()["data"]["translations"][0]["translatedText"]
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                msg = r.text[:200]
            self.last_error = f"Google Cloud Translation: HTTP {r.status_code}: {msg}"
            if r.status_code in (400, 401, 403):
                # A key problem (wrong key, API not enabled, key restricted) —
                # it will not fix itself mid-session, so say so ONCE and stop.
                cls._official_off_reason = self.last_error
                logger.warning(f"Translation: {self.last_error} — check [translate] "
                               f"google_api_key; using the free services this session.")
        except Exception as e:
            self.last_error = f"Google Cloud Translation: {e}"
        return ""

    def _live_translate(self, text, from_lang, to_lang):
        """One live translation. Google's official API first when a key is
        configured; then Google's free web page (best quality, but refused on
        some connections); then MyMemory, a real documented API. Returns '' when
        all fail, with the reason in self.last_error; '' is never cached, so it
        retries next run."""
        cls = CachedTranslate
        out = self._official_translate(text, from_lang, to_lang)
        if out:
            self.last_error = None
            return out
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
        if time.time() < cls._mymemory_quota_until:
            self.last_error = f"{self.last_error}; MyMemory: daily limit reached"
            return ""
        try:
            from deep_translator import MyMemoryTranslator
            mm = MyMemoryTranslator(source=self._MYMEM_REGION.get(from_lang, from_lang),
                                    target=self._MYMEM_REGION.get(to_lang, to_lang))
            parts = self._split_for_limit(text, self._MYMEM_LIMIT)
            out = "".join(mm.translate(p) for p in parts)
            if "MYMEMORY WARNING" in out.upper():
                cls._mymemory_quota_until = time.time() + cls._MYMEM_QUOTA_PAUSE_S
                self.last_error = f"{self.last_error}; MyMemory: daily limit reached"
                logger.warning("Translation: MyMemory's free daily limit is used up; "
                               "texts stay untranslated until a later run.")
                return ""
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
        entry = self._lookup(crc)
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
            _shared.put(crc, entry)
        if src == languagein:
            return text                              # already in the target language
        if src in ('nl', 'en'):
            return self.cachedTranslate(text, language=src)
        return text                                  # unknown source, leave as-is

    def is_verified(self, text):
        # Empty text needs no verification.
        if text == "":
            return True
        entry = self._lookup(self.getCRC32(text))
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
        _shared.put(int(crc), entry)
