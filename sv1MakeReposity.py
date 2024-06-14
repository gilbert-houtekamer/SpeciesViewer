# create new project
#
import os
import sys
import shutil
import ghexif.sftplib
import configparser
#
INI_LOCATION = "svconfig.ini"
#
scriptpath = os.path.dirname(__file__)
modpath = os.path.abspath(os.path.join(scriptpath, os.pardir))
os.chdir(modpath)
sys.path.append(modpath)

config = configparser.ConfigParser()
config.read(INI_LOCATION)

MY_GHEXIF_DIR = modpath + '\\'                                      
NEW_DB_TREE = config.get("repository","BiotaDatebase")    # "e:/test3/SpeciesViewDB/"
WEB_TREE = config.get("repository","BiotaWebRoot")

os.makedirs(NEW_DB_TREE, exist_ok=True)               # root for data, metadata and web contents
#
# root for data repository with all pictures (will be very big)
# many directories
os.makedirs(NEW_DB_TREE+"biota/", exist_ok=True)
os.makedirs(NEW_DB_TREE+"admin/", exist_ok=True)
# the metadata describes the data in the repository, and has review subdirectories as specified below  
os.makedirs(NEW_DB_TREE+"metadata/", exist_ok=True)   
# files are expected to use these names:
# data_files.csv
# data_pictures.csv
# species.csv
# more 'in review' directories is likely very desirable, sub-directories by day or by last qualifier directory
os.makedirs(NEW_DB_TREE+"metadata/active/", exist_ok=True)  # what is in the big just repository
os.makedirs(NEW_DB_TREE+"metadata/review/", exist_ok=True)  # the data that is beging reviewed

initspeciesdir = NEW_DB_TREE+"metadata/initialize/"
os.makedirs(initspeciesdir, exist_ok=True)
shutil.copy(MY_GHEXIF_DIR+'installation files/empty_species.csv', initspeciesdir+'empty_species.csv')
shutil.copy(MY_GHEXIF_DIR+'installation files/empty_species.csv', NEW_DB_TREE+'metadata/active/species.csv')
shutil.copy(MY_GHEXIF_DIR+'installation files/initial_speciescollection.csv', initspeciesdir+'initial_speciescollection.csv')
shutil.copy(MY_GHEXIF_DIR+'installation files/initial_speciescollection.csv', NEW_DB_TREE+'metadata/active/speciescollection.csv')
shutil.copy(MY_GHEXIF_DIR+'installation files/empty_data_files.csv', initspeciesdir+'empty_data_files.csv')
shutil.copy(MY_GHEXIF_DIR+'installation files/empty_data_files.csv', NEW_DB_TREE+'metadata/active/data_files.csv')
shutil.copy(MY_GHEXIF_DIR+'installation files/empty_data_pictures.csv', initspeciesdir+'empty_data_pictures.csv')
shutil.copy(MY_GHEXIF_DIR+'installation files/empty_data_pictures.csv', NEW_DB_TREE+'metadata/active/data_pictures.csv')

# the web site is first create locally
# copy of the web site and directory for new files just frozen
os.makedirs(NEW_DB_TREE+"web", exist_ok=True)        
os.makedirs(NEW_DB_TREE+"web/biota/", exist_ok=True)
os.makedirs(NEW_DB_TREE+"web/biotablog/", exist_ok=True)    # mostly staging

initwebfiles_source = MY_GHEXIF_DIR+"includes web/" 
initwebfiles_target = NEW_DB_TREE+"web/includes web/"
os.makedirs(NEW_DB_TREE+"web/includes web/", exist_ok=True)
for filename in os.listdir(initwebfiles_source):
    source = os.path.join(initwebfiles_source, filename)
    target = os.path.join(initwebfiles_target, filename)
    shutil.copy(source, target)

os.makedirs(NEW_DB_TREE+"web/summaries/", exist_ok=True) 
#
if WEB_TREE != "":
    password = config.get("websettings","Sftppwd").strip("'")
    sftp = ghexif.sftplib.Sftp(
        hostname='houtfp.ssh.transip.me',
        username='houtekamercom',
        password=password,
    )
    # Connect to SFTP
    sftp.connect()
    # 
    sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE)
    sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE + '/biota')
    sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE + '/summaries')
    sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE + '/biotablog')
    sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE + '/admin')
    sftp.safeMkdir(sftp.remotePrefix() + WEB_TREE + '/includes web')    
    fromdir = NEW_DB_TREE + "web/includes web/" 
    towebdir = sftp.remotePrefix() + WEB_TREE + '/includes web/'
    sftp.put_r_portable(fromdir, towebdir, preserve_mtime=True)
    sftp.disconnect()
