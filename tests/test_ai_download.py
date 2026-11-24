"""engine.ai_download: resumable, checked, and never a half file under its real name."""
import hashlib
import json

import pytest

from engine import ai_download as dl


class _Resp:
    def __init__(self, data: bytes, start: int, honour_range=True):
        self.data = data[start:] if honour_range else data
        self.status_code = 206 if (start and honour_range) else 200
        self.content = self.data

    def iter_content(self, n):
        for i in range(0, len(self.data), 4):
            yield self.data[i:i + 4]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _site(monkeypatch, files: dict, calls: list, honour_range=True, serve=None):
    serve = serve or files
    m = {"version": "x", "files": [{"name": n, "size": len(d),
                                     "sha256": hashlib.sha256(d).hexdigest()}
                                    for n, d in files.items()]}

    def get(url, start=0, timeout=30):
        name = url.rsplit("/", 1)[-1]
        calls.append((name, start))
        if name == dl.MANIFEST:
            return _Resp(json.dumps(m).encode(), 0)
        return _Resp(serve[name], start, honour_range)
    monkeypatch.setattr(dl, "_get", get)
    return m


FILES = {"a.onnx": b"0123456789abcdef", "b.npy": b"zyxwvutsrq"}


def test_download_writes_checked_files_and_then_has_nothing_to_do(tmp_path, monkeypatch):
    calls = []
    _site(monkeypatch, FILES, calls)
    m = dl.manifest()
    seen = []
    assert dl.download(m, tmp_path, progress=lambda d, t, n: seen.append((d, t))) == \
        ["a.onnx", "b.npy"]
    assert (tmp_path / "a.onnx").read_bytes() == FILES["a.onnx"]
    assert not list(tmp_path.glob("*.part"))
    assert seen[-1] == (26, 26)
    assert dl.to_fetch(m, tmp_path) == []
    assert dl.download(m, tmp_path) == []


def test_a_broken_download_carries_on_where_it_stopped(tmp_path, monkeypatch):
    calls = []
    m = _site(monkeypatch, FILES, calls)
    (tmp_path / "a.onnx.part").write_bytes(FILES["a.onnx"][:6])
    assert dl.still_needed(m, tmp_path) == 26 - 6
    dl.download(m, tmp_path)
    assert ("a.onnx", 6) in calls
    assert (tmp_path / "a.onnx").read_bytes() == FILES["a.onnx"]


def test_a_server_that_ignores_range_starts_the_file_again(tmp_path, monkeypatch):
    m = _site(monkeypatch, FILES, [], honour_range=False)
    (tmp_path / "a.onnx.part").write_bytes(FILES["a.onnx"][:6])
    dl.download(m, tmp_path)
    assert (tmp_path / "a.onnx").read_bytes() == FILES["a.onnx"]


def test_a_damaged_file_never_gets_its_real_name(tmp_path, monkeypatch):
    bad = dict(FILES, **{"b.npy": b"zyxwvutsrQ"})
    m = _site(monkeypatch, FILES, [], serve=bad)
    with pytest.raises(ValueError):
        dl.download(m, tmp_path)
    assert not (tmp_path / "b.npy").exists()
    assert not (tmp_path / "b.npy.part").exists()
    assert (tmp_path / "a.onnx").exists()            # the good one stays


def test_stop_keeps_the_part_for_next_time(tmp_path, monkeypatch):
    m = _site(monkeypatch, FILES, [])
    n = {"i": 0}

    def cancelled():
        n["i"] += 1
        return n["i"] > 2
    with pytest.raises(dl.Cancelled):
        dl.download(m, tmp_path, cancelled=cancelled)
    assert not (tmp_path / "a.onnx").exists()
    assert (tmp_path / "a.onnx.part").stat().st_size == 8


def test_a_changed_local_file_is_fetched_again(tmp_path, monkeypatch):
    m = _site(monkeypatch, FILES, [])
    (tmp_path / "a.onnx").write_bytes(b"0123456789ABCDEF")       # same size, other bytes
    assert [f["name"] for f in dl.to_fetch(m, tmp_path)] == ["a.onnx", "b.npy"]


def test_not_enough_disk_space_is_said_before_anything_is_written(tmp_path, monkeypatch):
    m = _site(monkeypatch, FILES, [])
    monkeypatch.setattr(dl, "SPARE", 10 ** 18)
    with pytest.raises(OSError, match="Not enough free space"):
        dl.download(m, tmp_path)
    assert not list(tmp_path.iterdir())


def test_dialog_and_checklist_row_without_models(tmp_path, monkeypatch):
    from engine import ai_models
    from gui import ai_models_dialog, setup_checklist
    monkeypatch.setattr(ai_models, "ai_dir", lambda: tmp_path)
    mark, text = setup_checklist.ai_models_state()
    assert mark == setup_checklist.OPTIONAL and "Not installed" in text
    d = ai_models_dialog.AiModelsDialog()
    assert not d._go.isHidden() or not ai_models.runtime_available()
    (tmp_path / ai_models.DETECTOR_FILE).write_bytes(b"x")
    assert "Partly" in ai_models_dialog.state()[1]


def test_settings_window_has_the_ai_models_tab(monkeypatch):
    from settings.settings_window import SettingsWindow
    w = SettingsWindow(None, tab="AI models")
    assert w.tabs.tabText(w.tabs.currentIndex()) == "AI models"
    page = next(iter(w._built.values()))
    assert page.__class__.__name__ == "AiModelsDialog"
    w.done(0)                                     # no download running: nothing to stop


def test_a_missing_runtime_says_why_and_how(monkeypatch):
    import sys
    from engine import ai_models
    from gui import ai_models_dialog
    monkeypatch.setattr(ai_models, "runtime_problem",
                        lambda: "ModuleNotFoundError: No module named 'onnxruntime'")
    mark, text = ai_models_dialog.state()
    assert mark == "optional" and "No module named" in text
    assert sys.executable in text and "-m pip install onnxruntime" in text
