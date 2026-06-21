import pysftp
import stat
from urllib.parse import urlparse
import os
import json
import logging
import warnings
import binascii

warnings.filterwarnings('ignore','.*Failed to load HostKeys.*')
logger = logging.getLogger("speciesviewer")


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

    def remove(self, remote_path):
        """Delete a single remote file. Missing files are logged as warnings,
        not raised — callers (cleanup flows) generally don't care if the
        target was already gone."""
        try:
            self.connection.remove(remote_path)
            logger.info(f"removed remote file: {remote_path}")
        except IOError as e:
            logger.warning(f"could not remove remote file {remote_path}: {e}")

    def rmtree(self, remote_path):
        """Recursively delete a remote directory. Walks depth-first, logs
        but doesn't raise on individual failures. No-op if the path is
        already absent on the server."""
        try:
            entries = list(self.connection.listdir_attr(remote_path))
        except IOError:
            logger.info(f"rmtree no-op (no remote dir): {remote_path}")
            return
        for entry in entries:
            full = remote_path.rstrip('/') + '/' + entry.filename
            if stat.S_ISDIR(entry.st_mode):
                self.rmtree(full)
            else:
                try:
                    self.connection.remove(full)
                except IOError as e:
                    logger.warning(f"could not remove remote file {full}: {e}")
        try:
            self.connection.rmdir(remote_path)
            logger.info(f"removed remote dir: {remote_path}")
        except IOError as e:
            logger.warning(f"could not rmdir remote {remote_path}: {e}")

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
            #      f"uploading to {self.hostname} as {self.username} [(remote path: {remote_path});(source local path: {source_local_path})]"
            # )
            # Download file from SFTP
            self.connection.put(source_local_path, remote_path, preserve_mtime=True)
            # logger.info("upload completed")
        except Exception as err:
            # raise Exception(err)
            logger.warning(f"FTP uploading '{source_local_path}' to '{remote_path}' failed, {err}")
            
    def ensure_remote_dir(self, remote_path):
        import posixpath

        remote_dir = posixpath.dirname(remote_path)
        try:
            self.connection.chdir(remote_dir)
        except IOError:
            # Directory doesn't exist, so create it recursively
            parts = remote_dir.strip("/").split("/")
            current = ""
            for part in parts:
                current = posixpath.join(current, part)
                try:
                    self.connection.chdir(current)
                except IOError:
                    self.connection.mkdir(current)
                    self.connection.chdir(current)

    def put_r_portable(self, localdir, remotedir, preserve_mtime=True):
        try:
            entries = os.listdir(localdir)
        except OSError as e:
            logger.warning(f"FTP upload skipped — cannot list '{localdir}': {e}")
            return
        for entry in entries:
            remotepath = remotedir + "/" + entry
            localpath = os.path.join(localdir, entry)
            try:
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
                    if not rc:
                        logger.info(f'Status:  {action} {remotepath.replace(self.remote_path, "[WebServer]")}')
            except Exception as e:
                logger.warning(f"FTP upload skipped '{localpath}': {e}")
    
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
            logger.info(f'{self.FTPhits/(self.FTPhits + self.FTPmisses)*100:.0f}% of FTP JPG transfers are cached.')
        return
    
    # cache, uniquenes is size for JPG, CRC fo other files
    def cachedUpload(self, source_local_path, remote_path, preserve_mtime=True):
        # Skip (don't crash the whole upload) when the source is missing — e.g. a
        # species whose master best-JPG path is broken/unregistered. One bad file
        # must not abort building a virtual trip.
        if not os.path.isfile(source_local_path):
            logger.warning(f"FTP upload skipped — source missing: {source_local_path}")
            return True
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
        except Exception:
            # Most often the remote parent directory doesn't exist yet (e.g. the
            # small-JPG step uploads into species folders the page step didn't
            # create). Create the remote dir tree and retry once.
            try:
                parent = remote_path.rsplit("/", 1)[0]
                self.connection.makedirs(parent)
                self.connection.put(source_local_path, remote_path, preserve_mtime=True)
                self.openFTPCache[remote_path] = (source_local_path, sizeOrCRC)
                self.FTPmisses += 1
            except Exception as err2:
                # Warn and continue — a single file's upload failure shouldn't
                # abort the whole tree (was: re-raise, which killed the build).
                logger.warning(f"FTP upload failed for '{source_local_path}' -> '{remote_path}': {err2}")
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
    