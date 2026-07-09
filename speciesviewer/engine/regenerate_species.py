# regenerate_species.py: re-render ONE master species' website page from the
# CURRENT master data and upload it — the "Regenerate this species' page" action.
#
# Approach (a): NO re-read of the picture files. It loads the master exactly like
# sv5BuildWebsite (readProject), then renders just this one species for both
# languages via makeSpeciesWebDirectory (→ addOneSpeciesPage, which re-rates each
# JPG per-file and excludes the -small thumbnail), and SFTP-uploads that species'
# web subtree. Best-picks therefore reflect whatever is in the master data now
# (e.g. edits made through the master DB). Refreshing the master data FROM the
# files (true re-read / update-in-place) is a separate operation, by design.
#
# Caveat: only the species' own page + JPGs are regenerated. Parent index pages
# (family/order) still show their old best-pick thumbnail until a full build.

import os
import tempfile
import configparser
import logging

logger = logging.getLogger("speciesviewer")


def _resolve_leaf(sp, scientific_name):
    """Find the species_dict key for scientific_name (keys are the stored, usually
    lowercase, ScientificName). Exact first, then case-insensitive."""
    if scientific_name in sp.species_dict:
        return scientific_name
    target = (scientific_name or "").casefold()
    for key in sp.species_dict:
        if key.casefold() == target:
            return key
    return None


def _upload_species(MyProject, config, target_path, progress_cb):
    """SFTP-upload the one species' web subtree (HTML + JPGs) to the live site.
    Mirrors sv7WriteCommit.putRecursiveTreeWeb, scoped to this species folder."""
    import engine.sftplib
    tp = (target_path or "").replace("\\", "/")
    fromdisk = os.path.join(MyProject.getBiotaDir(), tp)
    if not os.path.isdir(fromdisk):
        return 0
    password = config.get("websettings", "Sftppwd", fallback="").strip("'")
    sftp = engine.sftplib.Sftp(
        hostname="houtfp.ssh.transip.me", username="houtekamercom", password=password)
    location_cache = os.path.join(MyProject.getRepositoryPath(), "admin", "FTPcache.json")
    sftp.openFTPCache(location_cache)
    sftp.connect()
    try:
        toweb = sftp.remotePrefix() + MyProject.getBiotaWebRoot() + tp
        if progress_cb:
            progress_cb("Uploading page and pictures…")
        sftp.put_r_portable(fromdisk, toweb, preserve_mtime=True)
    finally:
        sftp.closeFTPCache(location_cache)
        sftp.disconnect()
    return 1


def regenerate_species_page(scientific_name, master_db, progress_cb=None, upload=True):
    """Re-render + upload one master species' page. `master_db` is the open
    MasterDB (SQLite index) — used to pull ONLY this species' pictures + files,
    so we never read the 127 MB data_files.csv. Returns a result dict:
    {ok, error?, scientific_name, target_path, urls:[...]}."""
    import engine.projectlib
    import engine.speciesclassification
    import engine.specieslib
    import engine.connectivity as connectivity
    from utils.batch_config import generate_svconfig_ini

    def _p(msg):
        if progress_cb:
            progress_cb(msg)
        logger.info(f"regenerate_species: {msg}")

    if upload and not connectivity.publish_reachable():
        return {"ok": False, "error": connectivity.PUBLISH_UNREACHABLE_MSG}
    if master_db is None:
        return {"ok": False, "error": "Master database is not open."}

    tmp_cfg = os.path.join(tempfile.gettempdir(), "sv_regenspecies_svconfig.ini")
    generate_svconfig_ini(svconfig_path=tmp_cfg, master_mode=True)
    config = configparser.ConfigParser()
    config.read(tmp_cfg)

    _p("Loading the master…")
    MyProject = engine.projectlib.Project(
        config.get("repository", "BiotaDatabase"),
        config.get("project", "PictureDirectory", fallback=""),
        config.get("repository", "BiotaWebRoot"))
    MyProject.initMasterAndTrip(config, inittrip=True)
    sc = engine.speciesclassification.SpeciesClassification(MyProject)
    MyProject.registerSpeciesClassification(sc)
    sp = engine.specieslib.Species(MyProject)
    MyProject.registerSpecies(sp)

    # Species TREE from the small species.csv (1.3 MB — full tree + JPGmap +
    # ancestors for the breadcrumb). Read directly from metadata/active/ (NOT
    # readProject, which reads a trip's review/<project>/species.csv).
    active = os.path.join(MyProject.getMetaDir(), "active")
    _p("Reading the master species list…")
    MyProject.readSpeciesInfo(os.path.join(active, "species.csv"), False)
    MyProject.sc.readSpeciesCollection(os.path.join(active, "speciescollection.csv"), False)

    leaf = _resolve_leaf(sp, scientific_name)
    if leaf is None:
        return {"ok": False,
                "error": f"'{scientific_name}' was not found in the master species list."}
    species = sp.species_dict[leaf]
    target_path = species.get("TargetPath", "")

    # PICTURES + FILES for just this species, straight from the SQLite index.
    _p("Loading this species' pictures from the database…")
    pics = master_db.pictures_for_species(species["ScientificName"])
    for pic in pics:
        MyProject.UniquePictureName_dict[pic["UniquePictureName"]] = pic
    files = master_db.files_for_upns([p["UniquePictureName"] for p in pics])
    MyProject.files_dict.update(files)
    if not target_path:
        return {"ok": False,
                "error": f"'{scientific_name}' has no target path in the master (nothing to render)."}

    MyProject.setSkipJPGs(False)
    MyProject.setAllOrNew("All")   # render straight into Repository/web/
    urls = []
    for lang in ("nl", "en"):
        _p(f"Rendering the {lang.upper()} page…")
        MyProject.setLanguage(lang)
        MyProject.makeSpeciesWebDirectory(leaf, "All")
        urls.append(MyProject.getBiotaWebRoot() + target_path.replace("\\", "/")
                    + species["ScientificName"] + "-" + lang + ".html")

    if upload:
        try:
            _upload_species(MyProject, config, target_path, progress_cb)
        except Exception as e:
            logger.warning(f"regenerate_species: upload failed: {e}")
            return {"ok": False, "rendered": True,
                    "error": f"Rendered locally but the upload failed: {e}"}

    _p("Done.")
    return {"ok": True, "scientific_name": species["ScientificName"],
            "target_path": target_path, "urls": urls}
