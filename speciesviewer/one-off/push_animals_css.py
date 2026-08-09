# push_animals_css.py — refresh 'includes web/animals.css' on the live site.
#
# WHY THIS EXISTS
# Each trip / album / master site carries its OWN copy of animals.css under
# '<site>/includes web/'. Only two paths ever put it there: sv1MakeReposity at
# repository creation, and svv4BuildWebsite for albums. So an existing trip's
# copy is frozen at the moment that trip was created, and a stylesheet fix (e.g.
# the phone layout) never reaches it. This script pushes the current
# 'dist/includes web/animals.css' to every site that already has one.
#
# It only ever touches files literally named animals.css. It never creates a new
# one: a directory without animals.css is not a site we manage, and is skipped.
#
# SAFETY
#   * Dry run is the DEFAULT. It connects, reports, and writes nothing.
#   * --apply downloads each remote copy into a local backup folder BEFORE
#     overwriting it, so any single site can be put back byte for byte.
#
# USAGE  (from the speciesviewer/ directory, with it on the import path)
#   set PYTHONPATH=.        (PowerShell: $env:PYTHONPATH = ".")
#   python one-off/push_animals_css.py                 # dry run — see the plan
#   python one-off/push_animals_css.py --apply         # upload
#   python one-off/push_animals_css.py --apply --only gambia
#   python one-off/push_animals_css.py --restore <backup-dir>   # put it all back

import argparse
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

import engine.sftplib
from utils.config import _config

HOSTNAME = "houtfp.ssh.transip.me"
USERNAME = "houtekamercom"

# Where 'includes web' folders live, relative to the remote prefix. Each entry is
# scanned one level deep for '<child>/includes web/animals.css', and itself for
# 'includes web/animals.css'. Kept explicit and shallow: a blind recursive walk
# of the whole site would descend into ~2000 species folders for nothing.
SEARCH_ROOTS = ("/trips", "/nature", "/albums")

LOCAL_CSS = Path(__file__).resolve().parent.parent / "dist" / "includes web" / "animals.css"


def _sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:12]


def _connect():
    pwd = _config.get("websettings", "sftppwd", fallback="").strip("'").strip()
    if not pwd:
        sys.exit("No SFTP password configured (Global Settings ▸ Website password).")
    sftp = engine.sftplib.Sftp(hostname=HOSTNAME, username=USERNAME, password=pwd)
    sftp.connect()
    return sftp


def _find_css(sftp):
    """Every remote 'includes web/animals.css' under the search roots."""
    prefix = sftp.remotePrefix()
    found = []

    def check(site_dir):
        remote = f"{site_dir}/includes web/animals.css"
        try:
            if sftp.connection.isfile(remote):
                found.append(remote)
        except Exception:
            pass

    for root in SEARCH_ROOTS:
        base = prefix + root
        try:
            if not sftp.connection.isdir(base):
                continue
        except Exception:
            continue
        check(base)                       # e.g. /nature/includes web/
        for name in sftp.connection.listdir(base):
            child = f"{base}/{name}"
            try:
                if sftp.connection.isdir(child):
                    check(child)          # e.g. /trips/gambia/includes web/
            except Exception:
                continue
    return sorted(set(found))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually upload (default is a dry run that writes nothing)")
    ap.add_argument("--only", default="",
                    help="only sites whose remote path contains this text")
    ap.add_argument("--restore", metavar="BACKUP_DIR", default="",
                    help="put back the copies saved in an earlier --apply run")
    args = ap.parse_args()

    if not LOCAL_CSS.is_file():
        sys.exit(f"Local stylesheet not found: {LOCAL_CSS}")
    new_bytes = LOCAL_CSS.read_bytes()
    print(f"Local  : {LOCAL_CSS}")
    print(f"         {len(new_bytes)} bytes, sha {_sha(new_bytes)}\n")

    sftp = _connect()
    try:
        if args.restore:
            _restore(sftp, Path(args.restore))
            return

        targets = [t for t in _find_css(sftp) if args.only.lower() in t.lower()]
        if not targets:
            print("No 'includes web/animals.css' found. Nothing to do.")
            return

        backup_dir = None
        if args.apply:
            stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            backup_dir = LOCAL_CSS.parent.parent.parent / "one-off" / f"animals_css_backup_{stamp}"
            backup_dir.mkdir(parents=True, exist_ok=True)
            print(f"Backups: {backup_dir}\n")

        same = changed = 0
        for remote in targets:
            label = remote.replace(sftp.remotePrefix(), "")
            try:
                with sftp.connection.open(remote, "rb") as fh:
                    cur = fh.read()
            except Exception as e:
                print(f"  ?  {label}  (could not read: {e})")
                continue

            if _sha(cur) == _sha(new_bytes):
                print(f"  =  {label}  already current")
                same += 1
                continue

            changed += 1
            print(f"  ->  {label}  {len(cur)} bytes (sha {_sha(cur)}) -> {len(new_bytes)}")
            if args.apply:
                safe = label.strip("/").replace("/", "__")
                (backup_dir / safe).write_bytes(cur)
                sftp.upload(str(LOCAL_CSS), remote)

        print(f"\n{len(targets)} site(s): {changed} to update, {same} already current.")
        if not args.apply:
            print("DRY RUN — nothing was written. Re-run with --apply to upload.")
        elif changed:
            print(f"Uploaded. To undo: --restore \"{backup_dir}\"")
    finally:
        sftp.disconnect()


def _restore(sftp, backup_dir: Path):
    if not backup_dir.is_dir():
        sys.exit(f"No such backup folder: {backup_dir}")
    files = sorted(backup_dir.glob("*"))
    if not files:
        sys.exit(f"Backup folder is empty: {backup_dir}")
    for f in files:
        remote = sftp.remotePrefix() + "/" + f.name.replace("__", "/")
        print(f"  <-  {f.name.replace('__', '/')}")
        sftp.upload(str(f), remote)
    print(f"\nRestored {len(files)} file(s) from {backup_dir}.")


if __name__ == "__main__":
    main()
