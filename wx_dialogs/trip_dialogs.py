import os
from datetime import datetime
from configparser import ConfigParser

def read_trip_ini(trip_root: str):
    ini_path = os.path.join(trip_root, "trip.ini")
    if not os.path.isfile(ini_path):
        return None

    parser = ConfigParser()
    parser.read(ini_path, encoding="utf-8")

    return {
        "trip_banner": parser.get("trip_config", "trip_banner", fallback=""),
        "trip_picture": parser.get("trip_config", "trip_picture", fallback=""),
        "fromdate": parser.get("trip_config", "fromdate", fallback=""),
        "todate": parser.get("trip_config", "todate", fallback=""),
        "title_nl": parser.get("trip_text", "title_nl", fallback=""),
        "message_nl": parser.get("trip_text", "message_nl", fallback=""),
        "title_en": parser.get("trip_text", "title_en", fallback=""),
        "message_en": parser.get("trip_text", "message_en", fallback=""),
    }

def generate_trip_ini(
    trip_root: str,
    trip_name: str,
    banner_filename: str,
    picture_filename: str,
    from_date: str,
    to_date: str,
    title_nl: str,
    message_nl: str,
    title_en: str,
    message_en: str,
    config,
    logger
):
    ini_path = os.path.join(trip_root, "trip.ini")

    master_web = config.get("master", "masterbiotawebroot", fallback="/nature/")
    master_disk = config.get("master", "masterbiotadatabase", fallback="E:\\Dropbox\\naturedb")
    trip_url_name = config.get("trip", "tripbiotawebroot", fallback=trip_name)

    ini_text = f"""\
[trip_config]
master_repository_web = {master_web}
master_repository_disk = {master_disk}
trip_banner = {banner_filename}
trip_picture = {picture_filename}
created = {datetime.now().strftime('%Y-%m-%d %H:%M')}
trip_name = {trip_url_name}
fromdate = {from_date}
todate = {to_date}
oneblog = day

[trip_text]
title_nl = {title_nl}
message_nl = {message_nl}
title_en = {title_en}
message_en = {message_en}
"""

    try:
        with open(ini_path, "w", encoding="utf-8") as f:
            f.write(ini_text)
        logger.info(f"✅ trip.ini written to {ini_path}")
    except Exception as err:
        logger.info(f"❌ Failed to write trip.ini: {err}")
