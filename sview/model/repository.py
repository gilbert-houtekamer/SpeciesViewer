import configparser
import logging
import os
import shutil
from pathlib import Path

import ghexif.sftplib
import sview
from sview.model import SVModelException

logger = logging.getLogger(__name__)


class Repository:

    def __init__(self, path: Path, name: str = None):
        if not path.exists():
            raise SVModelException(f"repository does not exist: {path}")
        self.path: Path = path
        if name is None:
            self.name: str = path.stem
        else:
            self.name = name
        self.description = ""
        self.picture = None
        self.config = configparser.ConfigParser()
        self.config_path = self.path / "config.ini"
        if self.config_path.exists():
            self.config.read(path / 'config.ini')
            self.biota_database_path = Path(self.config.get('repository', 'BiotaDatebase'))
            self.biota_webroot_path = self.config.get('repository', 'BiotaWebRoot')
        else:
            self.config.add_section('repository')
            self.biota_database_path = self.path
            os.makedirs(self.biota_database_path, exist_ok=True)
            self.config.set('repository', 'BiotaDatebase', str(self.biota_database_path))
            self.biota_webroot_path = '/web'
            self.config.set('repository', 'BiotaWebRoot', str(self.biota_webroot_path))
            self.config.add_section('projects')
            self.config.add_section('project')
            self.config.add_section('debug')
            self.config.set('debug', 'SpeciesFound', str(0))
            with open(self.config_path, 'w') as configfile:
                self.config.write(configfile)

        # root for data repository with all pictures (will be very big)
        self.biota_path = self.path / 'biota'
        self.admin_path = self.path / 'admin'
        # the metadata describes the data in the repository, and has review subdirectories as specified below
        self.metadata_path = self.path / 'metadata'
        self.web_path = self.metadata_path / 'web'
        self.active_path = self.metadata_path / 'active'
        self.review_path = self.metadata_path / 'review'
        self.initspecies_path = self.metadata_path / 'initialize'
        logger.info(f"Repository {self.name} started")

    def set_last_project(self, project_path):
        self.config.set('projects', 'last', str(project_path))
        with open(self.config_path, 'w') as configfile:
            self.config.write(configfile)

    def get_last_project(self) -> Path | None:
        if self.config.has_option('projects', 'last'):
            p = self.config.get('projects', 'last')
            path = Path(p)
            if path.exists():
                return path
        return None

    def populate(self):
        # check we can access the installation files
        installation_files = sview.svconfig.path / 'installation files'
        if not installation_files.exists():
            raise Exception("missing installation files")
        installation_web_files = sview.svconfig.path / 'includes web'
        if not installation_files.exists():
            raise Exception("missing installation web files")

        # create the required paths
        os.makedirs(self.biota_path, exist_ok=True)
        os.makedirs(self.admin_path, exist_ok=True)
        os.makedirs(self.metadata_path, exist_ok=True)
        os.makedirs(self.active_path, exist_ok=True)
        os.makedirs(self.review_path, exist_ok=True)
        os.makedirs(self.initspecies_path, exist_ok=True)
        os.makedirs(self.web_path, exist_ok=True)
        os.makedirs(self.web_path / "biota", exist_ok=True)
        os.makedirs(self.web_path / "biotablog/", exist_ok=True)  # mostly staging
        os.makedirs(self.web_path / "admin", exist_ok=True)  # mostly staging
        os.makedirs(self.web_path / "summaries", exist_ok=True)
        os.makedirs(self.web_path / "includes web", exist_ok=True)

        # copy the initialization files
        shutil.copy(installation_files / 'empty_species.csv', self.initspecies_path / 'empty_species.csv')
        shutil.copy(installation_files / 'empty_species.csv', self.active_path / 'species.csv')
        shutil.copy(installation_files / 'initial_speciescollection.csv',
                    self.initspecies_path / 'initial_speciescollection.csv')
        shutil.copy(installation_files / 'initial_speciescollection.csv', self.active_path / 'speciescollection.csv')
        shutil.copy(installation_files / 'empty_data_files.csv', self.initspecies_path / 'empty_data_files.csv')
        shutil.copy(installation_files / 'empty_data_files.csv', self.active_path / 'data_files.csv')
        shutil.copy(installation_files / 'empty_data_pictures.csv', self.initspecies_path / 'empty_data_pictures.csv')
        shutil.copy(installation_files / 'empty_data_pictures.csv', self.active_path / 'data_pictures.csv')

        # copy the initialization web files
        initwebfiles_source = installation_web_files
        initwebfiles_target = self.web_path / "includes web"
        for filename in os.listdir(initwebfiles_source):
            source = os.path.join(initwebfiles_source, filename)
            target = os.path.join(initwebfiles_target, filename)
            shutil.copy(source, target)

    def validate(self):
        must_exist = [
            self.path,
            self.biota_database_path,
            self.biota_path,
            self.admin_path,
            self.metadata_path,
            self.web_path,
            self.active_path,
            self.review_path,
            self.initspecies_path,
        ]
        for p in must_exist:
            if not p.exists():
                return False
        return True

    def upload(self):
        sftp = ghexif.sftplib.Sftp(
            hostname='houtfp.ssh.transip.me',
            username='houtekamercom',
            password='cWHkf68TM_q^',
        )
        # Connect to SFTP
        sftp.connect()
        #
        sftp.safeMkdir(sftp.remotePrefix() + self.biota_webroot_path)
        sftp.safeMkdir(sftp.remotePrefix() + self.biota_webroot_path + '/biota')
        sftp.safeMkdir(sftp.remotePrefix() + self.biota_webroot_path + '/summaries')
        sftp.safeMkdir(sftp.remotePrefix() + self.biota_webroot_path + '/biotablog')
        sftp.safeMkdir(sftp.remotePrefix() + self.biota_webroot_path + '/admin')
        sftp.safeMkdir(sftp.remotePrefix() + self.biota_webroot_path + '/includes web')
        fromdir = self.web_path / "includes web"
        towebdir = sftp.remotePrefix() + self.biota_webroot_path + '/includes web/'
        sftp.put_r_portable(str(fromdir), towebdir, preserve_mtime=True)
        sftp.disconnect()
