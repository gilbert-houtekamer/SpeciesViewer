import pysftp
from urllib.parse import urlparse
import os
import logging
import warnings
warnings.filterwarnings('ignore','.*Failed to load HostKeys.*')
logger = logging.getLogger(__name__)


class Sftp:
    remote_path = "/data/sites/web/houtekamercom/www"

    def __init__(self, hostname, username, password, port=22):
        """Constructor Method"""
        # Set connection object to None (initial value)
        self.connection = None
        self.hostname = hostname
        self.username = username
        self.password = password
        self.port = port
    
    def remotePrefix(self):
        return self.remote_path

    def connect(self):
        """Connects to the sftp server and returns the sftp connection object"""

        try:
            cnopts = pysftp.CnOpts()
            cnopts.hostkeys = None
            # Get the sftp connection object
            self.connection = pysftp.Connection(
                host=self.hostname,
                username=self.username,
                password=self.password,
                port=self.port,
                cnopts=cnopts
            )
        except Exception as err:
            raise Exception(err)
        finally:
            logger.info(f"Connected to {self.hostname} as {self.username}.")

    def disconnect(self):
        """Closes the sftp connection"""
        self.connection.close()
        logger.info(f"Disconnected from host {self.hostname}")

    def listdir(self, remote_path):
        """lists all the files and directories in the specified path and returns them"""
        for obj in self.connection.listdir(remote_path):
            yield obj

    def listdir_attr(self, remote_path):
        """lists all the files and directories (with their attributes) in the specified path and returns them"""
        for attr in self.connection.listdir_attr(remote_path):
            yield attr

    # Downloads the file from remote sftp server to local.
    # Also, by default extracts the file to the specified target_local_path
    def download(self, remote_path, target_local_path):
        try:
            logger.info(
                 f"downloading remote path : {remote_path} to local path: {target_local_path}"
            )

            # Create the target directory if it does not exist
            path, _ = os.path.split(target_local_path)
            if not os.path.isdir(path):
                try:
                    os.makedirs(path)
                except Exception as err:
                    raise Exception(err)

            # Download from remote sftp server to local
            self.connection.get(remote_path, target_local_path)
            logger.info("download completed")

        except Exception as err:
            raise Exception(err)

    # Uploads the source files from local to the sftp server.
    def upload(self, source_local_path, remote_path, preserve_mtime=True):
        try:
            # logger.info(
            #     f"uploading to {self.hostname} as {self.username} [(remote path: {remote_path});(source local path: {source_local_path})]"
            # )
            # Download file from SFTP
            self.connection.put(source_local_path, remote_path, preserve_mtime=True)
            # logger.info("upload completed")

        except Exception as err:
            raise Exception(err)
        
    def put_r_portable(self, localdir, remotedir, preserve_mtime=True):
        for entry in os.listdir(localdir):
            remotepath = remotedir + "/" + entry
            localpath = os.path.join(localdir, entry)
            if not os.path.isfile(localpath):
                try:
                    self.connection.mkdir(remotepath)
                except OSError:     
                    pass
                self.put_r_portable(localpath, remotepath, preserve_mtime)
                logger.info(f'Status:  Processing directory {remotepath.replace(self.remote_path, "[WebServer]")}')
            else:
                self.connection.put(localpath, remotepath, preserve_mtime=preserve_mtime)
                logger.info(f'Status:  Copied {remotepath.replace(self.remote_path, "[WebServer]")}')
    
    def safeMkdir(self, remotepath):
        try:
            self.connection.mkdir(remotepath)
        except OSError:     
            pass