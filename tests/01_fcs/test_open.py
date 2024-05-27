import os
from pathlib import Path
from specview.model import Repository


class TestCreateFCS:
    
    def test_open_repo(self):
        path = Path(os.curdir) / 'data/repositories/repo1'
        assert path.exists()
        cfg = path / 'config.ini'
        assert cfg.exists()
        repo = Repository.from_path(path)
        assert repo.name == 'naam1'
        