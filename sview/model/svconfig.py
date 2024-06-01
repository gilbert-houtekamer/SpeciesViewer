from pathlib import Path
import configparser
from sview.model import Repository, Project


class SVConfig:
    
    def __init__(self):
        self.repo_paths = []
        self.repositories = {}
        self.proj_paths = []
        self.projects = {}
        
    def get_repositories(self):
        return self.repositories
        
    
    @staticmethod
    def from_path(path: Path):
        svcfg = SVConfig()
        cfg = configparser.ConfigParser()
        cfg.read(path)
        if cfg.has_section('repositories'):
            for repo_name in cfg['repositories']:
                repo_path = Path(cfg['repositories'][repo_name])
                svcfg.add_repository(repo_path)
        if cfg.has_section('projects'):
            for proj_name in cfg['projects']:
                proj_path = Path(cfg['projects'][proj_name])
                svcfg.add_project(proj_path)
        return svcfg

    def add_repository(self, path: Path):
        repo = Repository(path)
        if repo.name not in self.repositories:
            self.repo_paths.append(path)
            self.repositories[path] = repo
            
    def add_project(self, path: Path):
        proj = Project(path)
        if proj.name not in self.repositories:
            self.proj_paths.append(path)
            self.projects[path] = proj
    
    def write(self, path):
        config = configparser.ConfigParser()
        config.add_section('repositories')
        for repo_path in self.repo_paths:
            repo = self.repositories[repo_path]
            config['repositories'][repo.name] = str(repo_path.resolve())
        config.add_section('projects')
        for proj_path in self.proj_paths:
            proj = self.projects[proj_path]
            config['projects'][proj.name] = str(proj_path.resolve())
        with open(path, 'w') as configfile:
            config.write(configfile)

            