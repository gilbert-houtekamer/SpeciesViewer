from specview.model import SVModelException
from pathlib import Path
import configparser


class Project:

    def __init__(self, path: Path):
        if not path.exists():
            raise SVModelException(f"repository does not exist: {path}")
        self.path = path
        self.name = path.stem
        self.description = ""
        self.picture = None


    @staticmethod
    def from_path(path: Path):
        prj = Project(path)
        cfg = configparser.ConfigParser()
        cfg.read(path / 'config.ini')
        if cfg.has_section('config') and 'name' in cfg['config']:
            prj.name = cfg['config']['name']
        if cfg.has_section('config') and 'description' in cfg['config']:
            prj.description = cfg['config']['description']
        if cfg.has_section('config') and 'picture' in cfg['config']:
            ppath = path / cfg['config']['picture']
            if ppath.exists():
                prj.picture = ppath
        return prj
