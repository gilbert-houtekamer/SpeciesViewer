import pysftp
from urllib.parse import urlparse
import os
import json
import logging
import warnings
import binascii

warnings.filterwarnings('ignore','.*Failed to load HostKeys.*')
logger = logging.getLogger(__name__)


class Sftp:
    remote_path = "/data/sites/web/houtekamercom/www"
    FTPCache = {}
    FTPhits = 0
    FTPmisses = 0

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
                cacheddir = True
                try:
                    if not self.cached_mkdir(remotepath):
                        cacheddir = True
                except OSError:     
                    pass
                self.put_r_portable(localpath, remotepath, preserve_mtime)
                if not cacheddir:
                    logger.info(f'Status:  Created directory {remotepath.replace(self.remote_path, "[WebServer]")}')
            else:
                rc = self.cachedUpload(localpath, remotepath, preserve_mtime)    
                action = 'Cached' if rc else 'Copied'       
                #self.connection.put(localpath, remotepath, preserve_mtime=preserve_mtime)
                if not rc: 
                    logger.info(f'Status:  {action} {remotepath.replace(self.remote_path, "[WebServer]")}')
    
    def safeMkdir(self, remotepath):
        try:
            self.connection.mkdir(remotepath)
        except OSError:     
            pass

    # cache for JPGs already transmitted
    def openFTPCache(self, cachefilepath):
        # Read from the file
        self.openFTPCache = {}
        if os.path.exists(cachefilepath):
            with open(cachefilepath, 'r') as file:
                self.openFTPCache = json.load(file)
            # Convert lists back to tuples
            self.openFTPCache = {key: tuple(value) for key, value in self.openFTPCache.items()}
        return
    
    def closeFTPCache(self, cachefilepath):
        # Convert tuples to lists to make it JSON serializable
        data_json = {key: list(value) for key, value in self.openFTPCache.items()}
        # Write to a file
        with open(cachefilepath, 'w') as file:
            json.dump(data_json, file)

        if (self.FTPhits + self.FTPmisses)>0:
            logger.info(f'{self.FTPhits/(self.FTPhits + self.FTPmisses)*100:.2}% of FTP JPG transfers are cached.')
        return
    
    # cache, uniquenes is size for JPG, CRC fo other files
    def cachedUpload(self, source_local_path, remote_path, preserve_mtime=True):
        # search by target name, value is path and length/crc on computer
        if source_local_path.endswith('.jpg'):
            sizeOrCRC = os.path.getsize(source_local_path)
        else:
            # Read the content of the file
            with open(source_local_path, 'rb') as file:
                content = file.read()
            # Calculate the CRC32 checksum
            sizeOrCRC = binascii.crc32(content) & 0xFFFFFFFF

        if  remote_path in self.openFTPCache:                   # present
            cachedObject = self.openFTPCache[remote_path]       # get file name and length
            if os.path.basename(source_local_path) == os.path.basename(cachedObject[0]) and \
                sizeOrCRC == cachedObject[1]:
                self.FTPhits += 1
                return True
        # not this one cached or nothing found
        try:
            self.connection.put(source_local_path, remote_path, preserve_mtime=True)
            self.openFTPCache[remote_path]  = (source_local_path, sizeOrCRC)
            self.FTPmisses += 1            
        except Exception as err:
            raise Exception(err)
        return False
    
    def cached_mkdir(self, remote_path):
        if not remote_path in self.openFTPCache:                # directory resent
            try:
                self.connection.mkdir(remote_path)
            except OSError:     
                pass
            self.openFTPCache[remote_path] = ('directory', 0)
            self.FTPmisses += 1 
            return False
       
        # not this one cached or nothing found
        self.FTPhits += 1            
        return True
    