import os
from pathlib import Path
import pytest
from specview.model import SVConfig


@pytest.fixture(autouse=True)
def change_test_dir(request, monkeypatch):
    monkeypatch.chdir(request.fspath.dirname)


@pytest.fixture()
def app(request, monkeypatch):
    # create a valid configuration file in the root of our test data
    svcfg = SVConfig()
    monkeypatch.chdir(request.fspath.dirname)
    root = Path(os.curdir) / 'data'
    for repo_dir in (root / 'repositories').iterdir(): 
        if repo_dir.is_dir():
            svcfg.add_repository(repo_dir)
    for proj_dir in (root / 'projects').iterdir(): 
        if proj_dir.is_dir():
            svcfg.add_project(proj_dir)
    svcfg.write(root / 'config.ini')
