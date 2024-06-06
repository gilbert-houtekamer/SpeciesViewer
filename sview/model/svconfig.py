from pathlib import Path
import configparser
from sview.model import Repository


class SVConfig:
    
    def __init__(self, path: Path):
        self.path = path
        self.repo_paths = []
        self.repositories = {}
        cfg = configparser.ConfigParser()
        cfg.read(path / "config.ini")
        if cfg.has_section('repositories'):
            for repo_name in cfg['repositories']:
                repo_path = Path(cfg['repositories'][repo_name])
                self.add_repository(repo_path)

    def repositories_names(self):
        return self.repositories.keys()

    def get_repository(self, repo_name):
        return self.repositories[repo_name]

    def add_repository(self, path: Path):
        repo = Repository(path)
        if repo.name not in self.repositories:
            self.repo_paths.append(path)
            self.repositories[path] = repo

    def write_config(self):
        config = configparser.ConfigParser()
        config.add_section('repositories')
        for repo_path in self.repo_paths:
            repo = self.repositories[repo_path]
            config['repositories'][repo.name] = str(repo_path.resolve())
        config_path = self.path / "config.ini"
        with open(config_path, 'w') as configfile:
            config.write(configfile)

            