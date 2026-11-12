"""One translation memory per computer + pacing (engine/translatelib.py).

No network: every live call is stubbed. Each test gets its own shared memory
file in a temporary folder, so nothing touches the real app-data."""
import threading

import pytest

from engine import translatelib
from engine.translatelib import CachedTranslate, _SharedTranslations, _read_cache_file


@pytest.fixture
def shared(tmp_path, monkeypatch):
    mem = _SharedTranslations(path=str(tmp_path / "shared" / "TransCache.csv"))
    monkeypatch.setattr(translatelib, "_shared", mem)
    monkeypatch.setattr(CachedTranslate, "_official_key", staticmethod(lambda: ""))
    monkeypatch.setattr(CachedTranslate, "_RETRY_WAITS_S", (0, 0))
    monkeypatch.setattr(CachedTranslate, "_GAP_FREE_S", 0)
    monkeypatch.setattr(CachedTranslate, "_mymemory_quota_until", 0.0)
    return mem


def _stub_live(monkeypatch, answers):
    """_live_translate returns the next answer ('' = refused); counts calls."""
    calls = []

    def fake(self, text, from_lang, to_lang):
        calls.append(text)
        out = answers.pop(0) if answers else f"<{text}>"
        if not out:
            self.last_error = "refused"
        return out
    monkeypatch.setattr(CachedTranslate, "_live_translate", fake)
    return calls


def _repo(tmp_path, name):
    root = tmp_path / name
    (root / "admin").mkdir(parents=True)
    return root


def test_text_translated_for_a_trip_is_not_asked_again_for_the_master(tmp_path, shared, monkeypatch):
    calls = _stub_live(monkeypatch, ["Een zeearend"])
    trip = CachedTranslate(str(_repo(tmp_path, "trip")))
    assert trip.cachedTranslate("A sea eagle", language="en") == "Een zeearend"
    trip.writeTranslationCache()

    master = CachedTranslate(str(_repo(tmp_path, "master")))
    assert master.cachedTranslate("A sea eagle", language="en") == "Een zeearend"
    assert calls == ["A sea eagle"]                     # asked once, for both
    master.writeTranslationCache()                      # master's own file gets it too
    assert len(_read_cache_file(str(tmp_path / "master" / "admin" / "TransCache.csv"))) == 1


def test_hand_verified_wins_in_both_directions(tmp_path, shared, monkeypatch):
    _stub_live(monkeypatch, ["machine"])
    a = CachedTranslate(str(_repo(tmp_path, "a")))
    a.cachedTranslate("tekst", language="nl")           # unverified everywhere
    b = CachedTranslate(str(_repo(tmp_path, "b")))
    b.mark_verified("tekst", corrected="checked", source_lang="nl")
    # a's own unverified entry must give way to the verified one
    assert a.cachedTranslate("tekst", language="nl") == "checked"
    assert a.is_verified("tekst")
    # a repository folded in later with an unverified entry does not downgrade it
    c_root = _repo(tmp_path, "c")
    translatelib._write_cache_file(str(c_root / "admin" / "TransCache.csv"), {
        a.getCRC32("tekst"): {'translation': 'old', 'source_lang': 'nl', 'verified': False}})
    CachedTranslate(str(c_root))
    assert shared.get(a.getCRC32("tekst"))['translation'] == "checked"


def test_save_merges_with_what_another_process_wrote(tmp_path, shared, monkeypatch):
    _stub_live(monkeypatch, ["one"])
    tr = CachedTranslate(str(_repo(tmp_path, "r")))
    tr.cachedTranslate("een", language="nl")
    shared.save()
    # another SpeciesViewer process adds a row behind our back
    rows = _read_cache_file(shared._path)
    rows[12345] = {'translation': 'theirs', 'source_lang': 'en', 'verified': True}
    translatelib._write_cache_file(shared._path, rows)
    _stub_live(monkeypatch, ["two"])
    tr.cachedTranslate("twee", language="nl")
    shared.save()
    saved = _read_cache_file(shared._path)
    assert 12345 in saved and len(saved) == 3


def test_patient_translator_retries_a_refusal(tmp_path, shared, monkeypatch):
    calls = _stub_live(monkeypatch, ["", "", "Hallo"])
    tr = CachedTranslate(str(_repo(tmp_path, "r")), patient=True)
    assert tr.cachedTranslate("Hello", language="en") == "Hallo"
    assert len(calls) == 3


def test_gui_translator_does_not_wait(tmp_path, shared, monkeypatch):
    calls = _stub_live(monkeypatch, ["", "Hallo"])
    tr = CachedTranslate(str(_repo(tmp_path, "r")))
    assert tr.cachedTranslate("Hello", language="en") == "Hello"   # original back
    assert len(calls) == 1
    assert tr.translation_cache == {}                               # nothing cached


def test_mymemory_quota_warning_is_never_cached(tmp_path, shared, monkeypatch):
    import deep_translator

    def google_down(*_a, **_k):
        raise RuntimeError("no google")
    monkeypatch.setattr(translatelib, "GoogleTranslator", google_down)
    monkeypatch.setattr(CachedTranslate, "_google_blocked_until", 0.0)
    monkeypatch.setattr(deep_translator.MyMemoryTranslator, "translate",
                        lambda self, t, **k: "MYMEMORY WARNING: YOU USED ALL AVAILABLE "
                                             "FREE TRANSLATIONS FOR TODAY")
    tr = CachedTranslate(str(_repo(tmp_path, "r")), patient=True)
    assert tr.cachedTranslate("Hello", language="en") == "Hello"
    assert tr.translation_cache == {}
    assert "daily limit" in tr.last_error


def test_a_finished_step_saves_its_translators_only(tmp_path, shared, monkeypatch):
    _stub_live(monkeypatch, [])
    gui_side = CachedTranslate(str(_repo(tmp_path, "gui")))
    gui_side.cachedTranslate("gui text", language="en")

    def step():                     # a website build: translates, never saves
        CachedTranslate(str(_repo(tmp_path, "step"))).cachedTranslate("page text", language="en")
        translatelib.save_translators()
    t = threading.Thread(target=step)
    t.start()
    t.join()
    assert (tmp_path / "step" / "admin" / "TransCache.csv").exists()
    assert not (tmp_path / "gui" / "admin" / "TransCache.csv").exists()
    assert len(_read_cache_file(shared._path)) == 2


def test_long_run_saves_along_the_way(tmp_path, shared, monkeypatch):
    _stub_live(monkeypatch, [])
    monkeypatch.setattr(CachedTranslate, "_AUTOSAVE_EVERY", 3)
    tr = CachedTranslate(str(_repo(tmp_path, "r")), patient=True)
    for i in range(4):
        tr.cachedTranslate(f"text {i}", language="en")
    assert len(_read_cache_file(str(tmp_path / "r" / "admin" / "TransCache.csv"))) == 3
