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
import re
import shutil
import tempfile
import configparser
import logging
from utils.ini_io import read_ini

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


WEB_ASSETS = ("leaflet.css", "leaflet.js")   # the occurrence map, self-hosted


def _stage_web_assets(MyProject) -> str:
    """Copy the shipped Leaflet files into the repository's `web/includes web/`,
    so the site serves the map library itself rather than a CDN.

    Self-hosting is the whole point (no third-party request from a visitor, and a
    map that cannot break because a CDN did), but it means the two files have to
    reach the server. They go in the shared includes folder, NOT the species
    folder — one copy for all ~2000 species pages, and the browser caches it after
    the first page. Returns the includes dir, or "" when there is nothing to do."""
    src_dir = os.path.join(MyProject.modpath, "dist", "includes web")
    dst_dir = os.path.join(MyProject.getBiotaDir(), "includes web")
    if not os.path.isdir(src_dir):
        return ""
    os.makedirs(dst_dir, exist_ok=True)
    for name in WEB_ASSETS:
        src, dst = os.path.join(src_dir, name), os.path.join(dst_dir, name)
        if not os.path.isfile(src):
            logger.warning(f"web assets: {name} is missing from {src_dir}")
            continue
        if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
            continue                                  # already staged
        shutil.copy2(src, dst)
        logger.info(f"web assets: staged {name} into {dst_dir}")
    return dst_dir


_LINK_RE = re.compile(r'(?:src|href)="([^"]+)"', re.IGNORECASE)
_PICTURE_EXTS = (".jpg", ".jpeg", ".png")


def _pictures_the_page_links(web_dir, target_path) -> list[str]:
    """The picture filenames the RENDERED page actually references.

    Read out of the HTML we just wrote, not derived from JPGmap — because the page
    shows only a SUBSET of it. addOneSpeciesPage drops any picture taken within 15
    minutes of one already shown (websupport.py:1427) and caps how many it takes per
    age/sex/plumage group, so the reed warbler publishes 2 of its 6, and the Galapagos
    penguins would publish a handful of ~50 TG-3 frames. Uploading JPGmap would push
    every one of them to serve four.

    Reading the links back is also the honest definition of the job — "ship what the
    page asks for" — and it cannot fall out of step with the selection rules the way a
    second guess at them would."""
    want = "/" + (target_path or "").strip("/").replace("\\", "/").lower() + "/"
    names, seen = [], set()
    for fn in sorted(os.listdir(web_dir)):
        if not fn.lower().endswith(".html"):
            continue
        try:
            with open(os.path.join(web_dir, fn), encoding="utf-8") as fh:
                html = fh.read()
        except OSError as e:
            logger.warning(f"regenerate: cannot read {fn}: {e}")
            continue
        for link in _LINK_RE.findall(html):
            u = link.replace("\\", "/")
            if want not in u.lower():
                continue                      # banner, stylesheet, another species
            base = u.rsplit("/", 1)[-1]
            # The page also links its own SIBLING (the EN/NL language switch lives in
            # this same folder), so match on the extension, not just the folder.
            if not base.lower().endswith(_PICTURE_EXTS):
                continue
            if base.lower() not in seen:
                seen.add(base.lower())
                names.append(base)
    return names


def _master_source(MyProject, config, target_path, basename) -> str:
    """Where a picture the page links to actually LIVES.

    NOT under getBiotaAllorNewDir() — that is the web STAGING tree, which holds only
    the rendered HTML. In master mode websupport deliberately skips the staging copy
    (`_src_already_at_final`) because the JPG is already at its final place in
    master/biota, and the server mirrors that tree. So the file rows are the truth:
    WindowsFileName is the same source websupport itself copies from."""
    from utils.paths import to_fs_path
    row = MyProject.files_dict.get(basename)
    if row:
        win = row.get("WindowsFileName", "")
        if win:
            p = to_fs_path(win)               # host-independent (Win-authored ok on Mac)
            if p and p.exists():
                return str(p)
    root = config.get("master", "masterbiotadatabase", fallback="").strip()
    if root:
        p = os.path.join(root, (target_path or "").replace("/", os.sep), basename)
        if os.path.isfile(p):
            return p
    return ""


def _upload_species_pictures(MyProject, config, sftp, toweb, target_path, progress_cb):
    """Upload the pictures the page links to.

    The page links them straight out of `/nature/biota/…` — the same tree the master
    keeps them in — while the regenerate only ever uploaded the rendered HTML. That was
    invisible for as long as a page could only name a JPG some earlier FULL build had
    already shipped. The moment a picture's public face can CHANGE — a pinned re-crop,
    a freshly ingested DxO render — the page names a file the server has never seen,
    and it renders as a broken image.

    So the pictures go up with the page that references them, and only those.
    cachedUpload makes the unchanged ones free."""
    web_dir = os.path.join(MyProject.getBiotaDir(), target_path)
    wanted = _pictures_the_page_links(web_dir, target_path)
    before = sftp.FTPmisses
    for base in wanted:
        src = _master_source(MyProject, config, target_path, base)
        if not src:
            logger.warning(f"regenerate: the page links {base!r} but it is nowhere on "
                           f"disk — it will render as a broken image.")
            continue
        sftp.cachedUpload(src, toweb.rstrip("/") + "/" + base, preserve_mtime=True)
    sent = sftp.FTPmisses - before
    logger.info(f"regenerate: page links {len(wanted)} picture(s); uploaded {sent} "
                f"new/changed, the rest already on the server.")
    if progress_cb and sent:
        progress_cb(f"Uploaded {sent} new/changed picture(s)…")
    return sent


def _upload_species(MyProject, config, target_path, progress_cb):
    """SFTP-upload the one species' web subtree (HTML + JPGs) to the live site.
    Mirrors sv7WriteCommit.putRecursiveTreeWeb, scoped to this species folder."""
    import engine.sftplib
    tp = (target_path or "").replace("\\", "/")
    fromdisk = os.path.join(MyProject.getBiotaDir(), tp)
    if not os.path.isdir(fromdisk):
        return 0
    assets_dir = _stage_web_assets(MyProject)
    password = config.get("websettings", "Sftppwd", fallback="").strip("'")
    sftp = engine.sftplib.open_site(password=password)
    location_cache = os.path.join(MyProject.getRepositoryPath(), "admin", "FTPcache.json")
    sftp.openFTPCache(location_cache)
    sftp.connect()
    try:
        toweb = sftp.remotePrefix() + MyProject.getBiotaWebRoot() + tp
        if progress_cb:
            progress_cb("Uploading page and pictures…")
        sftp.put_r_portable(fromdisk, toweb, preserve_mtime=True)
        _upload_species_pictures(MyProject, config, sftp, toweb, tp, progress_cb)
        # The shared includes folder carries leaflet.{css,js}. put_r_portable is
        # cached (cachedUpload skips unchanged files), so this costs one upload the
        # first time and nothing on every regenerate after it.
        if assets_dir:
            if progress_cb:
                progress_cb("Checking the map library on the site…")
            sftp.put_r_portable(
                assets_dir,
                sftp.remotePrefix() + MyProject.getBiotaWebRoot() + "includes web",
                preserve_mtime=True)
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
        return {"ok": False, "error": connectivity.publish_unreachable_msg()}
    if master_db is None:
        return {"ok": False, "error": "Master database is not open."}

    tmp_cfg = os.path.join(tempfile.gettempdir(), "sv_regenspecies_svconfig.ini")
    generate_svconfig_ini(svconfig_path=tmp_cfg, master_mode=True)
    config = configparser.ConfigParser()
    read_ini(config, tmp_cfg)

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

    # Re-derive sex/age/plumage from each picture's SUBJECT keyword. The master
    # stores the qualifier in the keyword ("['aasgier', 'juv']") but historically
    # never filled the malefemale/ageintext/summerwinter columns that token 101
    # (the "jonge aasgier" caption) and the age/sex/plumage grouping read. This
    # mirrors what the viewer does, so the website reflects the same qualifiers.
    for pic in MyProject.UniquePictureName_dict.values():
        mf, age, sw = sp.getBirdInfo(pic.get("subject", "") or "")
        pic["malefemale"], pic["ageintext"], pic["summerwinter"] = mf, age, sw
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
