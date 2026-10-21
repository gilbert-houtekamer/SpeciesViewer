"""The upload cache knows a JPG by its size, whatever the case of ".jpg"
(engine.sftplib cachedUpload, 2026-10-21). An old entry for a ".JPG" holds a
CRC: it is checked once, rewritten as a size, and never sent again."""

import binascii

from engine import sftplib


class _Conn:
    def __init__(self):
        self.sent = []

    def put(self, local, remote, preserve_mtime=True):
        self.sent.append(remote)


def _site():
    s = sftplib.Sftp.__new__(sftplib.Sftp)
    s.connection = _Conn()
    s.openFTPCache = {}
    s.FTPhits = s.FTPmisses = 0
    return s


def test_upper_case_jpg_is_known_by_size(tmp_path):
    f = tmp_path / "2013 DSC_3106.JPG"
    f.write_bytes(b"x" * 1000)
    s = _site()
    assert s.cachedUpload(str(f), "/r/a.JPG") is False          # first time: sent
    assert s.openFTPCache["/r/a.JPG"][1] == 1000                # stored as a size
    assert s.cachedUpload(str(f), "/r/a.JPG") is True           # then skipped
    assert s.connection.sent == ["/r/a.JPG"]


def test_an_old_crc_entry_is_converted_once_not_resent(tmp_path):
    f = tmp_path / "2013 DSC_3106.JPG"
    f.write_bytes(b"y" * 777)
    s = _site()
    crc = binascii.crc32(f.read_bytes()) & 0xFFFFFFFF
    s.openFTPCache["/r/a.JPG"] = (str(f), crc)                  # as written before
    assert s.cachedUpload(str(f), "/r/a.JPG") is True
    assert s.openFTPCache["/r/a.JPG"][1] == 777
    assert s.connection.sent == []


def test_a_changed_jpg_is_sent(tmp_path):
    f = tmp_path / "pic_DxO.jpg"
    f.write_bytes(b"z" * 10)
    s = _site()
    s.openFTPCache["/r/b.jpg"] = (str(f), 9)                    # an older render
    assert s.cachedUpload(str(f), "/r/b.jpg") is False
    assert s.connection.sent == ["/r/b.jpg"]
