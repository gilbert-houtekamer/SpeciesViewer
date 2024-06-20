from pathlib import Path
import configparser
from sview.model import Repository


class SVConfig:
    
    def __init__(self, path: Path):
        self.path = path
        self.current_repo = None
        self.repo_paths = []
        self.repositories = {}
        cfg = configparser.ConfigParser()
        config_path = path / "config.ini"
        if config_path.exists():
            cfg.read(config_path)
        else:
            cfg.add_section('repositories')
            with open(config_path, 'w') as configfile:
                cfg.write(configfile)
        if cfg.has_section('repositories'):
            for repo_name in cfg['repositories']:
                repo_path = Path(cfg['repositories'][repo_name])
                repo = Repository(repo_path, repo_name)
                self.add_repository(repo)

    def repositories_names(self):
        return list(self.repositories.keys())

    def get_repository(self, repo_name):
        return self.repositories[repo_name]

    def has_repository(self, path: Path):
        return path in self.repositories

    def set_current_repository(self, repo):
        self.current_repo = repo

    def get_current_repository(self):
        return self.current_repo

    def add_repository(self, repo: Repository):
        if repo.name not in self.repositories:
            self.repo_paths.append(repo.path)
            self.repositories[repo.name] = repo

    def delete_repository(self, repo: Repository):
        if repo.name in self.repositories:
            del self.repositories[repo.name]
            self.repo_paths.remove(repo.path)

    def write_config(self):
        config = configparser.ConfigParser()
        config.add_section('repositories')
        for repo_name, repo in self.repositories.items():
            config['repositories'][repo.name] = str(repo.path.resolve())
        config_path = self.path / "config.ini"
        with open(config_path, 'w') as configfile:
            config.write(configfile)

            