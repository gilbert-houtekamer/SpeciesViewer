from specview.model import SVModelException
from pathlib import Path
import configparser


class Repository:
    
    def __init__(self, path: Path):
        if not path.exists():
            raise SVModelException(f"repository does not exist: {path}")
        self.path = path
        self.name = path.stem
        self.description = ""
        self.picture = None
        
    
    @staticmethod
    def from_path(path: Path):
        repo = Repository(path)
        cfg = configparser.ConfigParser()
        cfg.read(path / 'config.ini')
        if cfg.has_section('config') and 'name' in cfg['config']:
            repo.name = cfg['config']['name']
        if cfg.has_section('config') and 'description' in cfg['config']:
            repo.description = cfg['config']['description']
        if cfg.has_section('config') and 'picture' in cfg['config']:
            ppath = path / cfg['config']['picture']
            if ppath.exists():
                repo.picture = ppath
        return repo
