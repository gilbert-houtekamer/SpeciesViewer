import os
from pathlib import Path
from specview.model import Repository, SVConfig


class TestCreateFCS:
    
    def test_open_repo(self):
        path = Path(os.curdir) / 'data/repositories/repo1'
        assert path.exists()
        cfg = path / 'config.ini'
        assert cfg.exists()
        repo = Repository.from_path(path)
        assert repo.name == 'naam1'
        
    def test_open_proj(self):
        path = Path(os.curdir) / 'data/projects/project1'
        assert path.exists()
        cfg = path / 'config.ini'
        assert cfg.exists()
        repo = Repository.from_path(path)
        assert repo.name == 'project1'
        
    def test_open_cfg(self, app):
        path = Path(os.curdir) / 'data'
        assert path.exists()
        cfg = path / 'config.ini'
        assert cfg.exists()
        svcfg = SVConfig.from_path(path)
        assert len(svcfg.repositories) > 0
        assert len(svcfg.projects) > 0
        
        