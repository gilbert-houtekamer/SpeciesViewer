"""Published occurrence map for a species page (Maps Phase 4).

Renders "where I have seen this species" at the foot of a species page: a Leaflet
map over the species' own master pictures. Emitted by token 180 (see websupport
.formatToken) into `vogelsoortpage-<lang>.html`.

Four decisions worth knowing before you touch this:

* IT IS AN OCCURRENCE MAP, NOT A PHOTO MAP. Of a typical species' GPS'd pictures
  only a handful were ever rendered to JPG (the master keeps every RAW; only the
  curated few are developed) — 4 of 54 for the grey heron. So the markers are
  SIGHTINGS. A popup shows a thumbnail only where that spot's picture happens to
  be published on this very page, which quietly marks "this is the one you have a
  photo of". Never make a thumbnail a requirement for a point: you would throw
  away 90% of the map.

* OBSCURE SPECIES GET NO MAP AT ALL. websupport.isObscure already strips GPS from
  the website copy for protected families (orchids). A published map is exactly
  where a protected location would leak, so this returns "" for them rather than
  degrading the precision — a coarse map still says "there is one, roughly here".

* WHOLE-DEGREE COORDINATES ARE DROPPED. Some master pictures (waarneming.nl
  imports) carry a coordinate whose minutes AND seconds are zero: that is a
  ~110 km GRID REFERENCE, not a fix. It looks like valid GPS to everything else,
  and on a public page it would draw a bird in the middle of the North Sea. See
  tools/svr_gps_precision_scan.py.

* LEAFLET IS SELF-HOSTED and the markers are `circleMarker`s (pure SVG). That is
  deliberate: Leaflet's default marker needs marker-icon.png from an `images/`
  folder beside the CSS, which is the classic self-hosting 404. Circles need
  nothing, so the whole map is two files: leaflet.css + leaflet.js.

The tiles are fetched by the VISITOR's browser, not by us, so the map is only
initialised once it scrolls into view (IntersectionObserver) — it keeps the page
fast and it is the polite way to consume OSM's tile service.
"""

from __future__ import annotations

import html
import json
import logging
import os
import re

logger = logging.getLogger("speciesviewer")

# "52 deg 39' 41.52" N" — the master stores GPS as exiftool's DMS string.
_DMS = re.compile(r"(\d+)\s*deg\s*(\d+)'\s*([0-9.]+)\"\s*([NSEW])")

# How close two pictures must be to count as ONE place. This is a question about
# BIRDS, not about floating point: 72 pictures of a red-billed firefinch came out
# as "2 locations" because the bird was photographed from either side of the same
# bush, ~2 m apart. A sighting is a place you stood, not a coordinate — so anything
# within 50 m is one dot. (GPS itself is only good to ~5-10 m, which is another
# reason a 1 m distinction is meaningless.)
_SPOT_MERGE_M = 50.0


def _decimal(part: str):
    """One 'D deg M' S" H' half of a GPSPosition → signed decimal, or None."""
    m = _DMS.search(part or "")
    if not m:
        return None
    deg, minutes, seconds, hemi = m.groups()
    val = int(deg) + int(minutes) / 60.0 + float(seconds) / 3600.0
    return -val if hemi in ("S", "W") else val


def _is_grid_reference(gps: str) -> bool:
    """True for a coordinate whose minutes AND seconds are zero in BOTH halves —
    a whole-degree grid reference (~110 km), not a fix. See the module note."""
    parts = _DMS.findall(gps or "")
    if len(parts) != 2:
        return False
    return (all(int(p[1]) == 0 for p in parts)
            and all(float(p[2]) == 0.0 for p in parts))


def _lat_lon(gps: str):
    """'lat, lon' DMS string → (lat, lon) decimals, or None when unusable."""
    if not gps or _is_grid_reference(gps):
        return None
    halves = [h for h in gps.split(",") if h.strip()]
    if len(halves) != 2:
        return None
    lat, lon = _decimal(halves[0]), _decimal(halves[1])
    if lat is None or lon is None:
        return None
    return lat, lon


def _published_thumb(MyProject, picture, web_root: str, target_path: str):
    """Web URL of this picture's SMALL jpg, or "" when it has none.

    Two things this must get right:

    * The thumbnail lives beside the picture in master/biota, NOT under web/. In
      master mode the JPG is already at its final spot, so the build copies
      nothing and web/ holds only HTML — but the server serves both trees under
      the same /nature/biota/... URL. So we look for the file next to its source
      and publish the URL.

    * ONLY the `-small` (~60 KB) is acceptable. The full JPG of the same picture
      can be 2.7 MB; putting that in a popup would download it on every click. A
      picture with no `-small` simply gets no thumbnail — most points have none
      anyway (they were never rendered to JPG at all), so a missing one is normal,
      not an error."""
    files = picture.get("Files") or []
    if not isinstance(files, list):
        return ""
    for fname in files:
        base = os.path.basename(fname)
        small = _small_name(base)
        if not small:
            continue
        row = MyProject.files_dict.get(base)
        if not row:
            continue
        src = row.get("WindowsFileName", "")
        if not src:
            continue
        if not os.path.isfile(os.path.join(os.path.dirname(src), small)):
            continue                       # no rendered thumbnail → no popup image
        return (web_root + target_path.replace("\\", "/") + small).replace(" ", "%20")
    return ""


def _small_name(base: str) -> str:
    """'2024 april Spanje-N4250696.JPG' → '...-N4250696-small.jpg'; "" for a
    non-JPG.

    NOT websupport.getSmallJPG: that one does `replace('.jpg', '-small.jpg')`,
    which is CASE-SENSITIVE, so an uppercase `.JPG` comes back UNCHANGED — i.e. it
    hands you the full-size file and calls it a thumbnail. Here that would put a
    2.7 MB image in a map popup. (The master holds 1,717 uppercase `.JPG` files,
    so this is not hypothetical; the same flaw affects the summary pages, which is
    a separate matter from this map.)"""
    stem, ext = os.path.splitext(base)
    if ext.lower() not in (".jpg", ".jpeg"):
        return ""
    if stem.lower().endswith("-small"):
        return base
    return stem + "-small.jpg"


def _metres(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in metres (haversine)."""
    import math
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _spots(MyProject, species, language: str) -> list[dict]:
    """The species' sightings, one dot per PLACE: {lat, lon, n, place, from, to,
    thumb}.

    Pictures are merged into a place when they are within _SPOT_MERGE_M of each
    other. Greedy, biggest cluster first, so the busiest spot claims its
    neighbours rather than a stray shot seeding its own dot. The dot lands on the
    picture-weighted mean of its members, and takes the place name of whichever
    member has one (the same bush can be geocoded on one shot and not the next)."""
    from engine.websupport import getFormattedLocation
    sci = species.get("ScientificName", "")
    web_root = MyProject.getBiotaWebRoot()
    target_path = species.get("TargetPath", "")

    pics = []
    for pic in MyProject.UniquePictureName_dict.values():
        if pic.get("WetSoortNaam", "") != sci:
            continue
        ll = _lat_lon(pic.get("GPSPosition", ""))
        if not ll:
            continue
        pics.append((ll[0], ll[1], pic))
    if not pics:
        return []

    # Exact coordinates first (a burst shares one fix), then merge what is close.
    exact: dict = {}
    for lat, lon, pic in pics:
        exact.setdefault((lat, lon), []).append(pic)
    groups = sorted(exact.items(), key=lambda kv: -len(kv[1]))

    clusters: list[dict] = []
    for (lat, lon), members in groups:
        for c in clusters:
            if _metres(lat, lon, c["lat"], c["lon"]) <= _SPOT_MERGE_M:
                # Weighted mean: the dot follows the pictures, not the first fix.
                total = c["n"] + len(members)
                c["lat"] = (c["lat"] * c["n"] + lat * len(members)) / total
                c["lon"] = (c["lon"] * c["n"] + lon * len(members)) / total
                c["n"] = total
                c["pics"].extend(members)
                break
        else:
            clusters.append({"lat": lat, "lon": lon, "n": len(members),
                             "pics": list(members)})

    spots = []
    for c in clusters:
        dates = sorted(d for d in
                       ((p.get("DateTimeOriginal", "") or "")[:10].replace(":", "-")
                        for p in c["pics"]) if d)
        place = ""
        thumb = ""
        for p in c["pics"]:
            place = place or (getFormattedLocation(p) or "")
            thumb = thumb or _published_thumb(MyProject, p, web_root, target_path)
        spots.append({
            "lat": round(c["lat"], 6), "lon": round(c["lon"], 6), "n": c["n"],
            "place": place,
            "from": dates[0] if dates else "", "to": dates[-1] if dates else "",
            "thumb": thumb,
        })
    return sorted(spots, key=lambda s: -s["n"])


def speciesOccurrenceMap(MyProject, species, language: str) -> str:
    """The whole map section, or "" when there is nothing (or nothing we may
    publish) to show. Safe to call for every species page."""
    from engine.websupport import isObscure
    try:
        if isObscure(MyProject, species):
            return ""                      # protected: no map, not a coarse one
        spots = _spots(MyProject, species, language)
        if not spots:
            return ""

        pictures = sum(s["n"] for s in spots)
        n_spots = len(spots)
        if language == "nl":
            title = "Waar ik deze soort zag"
            lead = (f"In de database zijn {pictures} foto"
                    f"{chr(39) + 's' if pictures != 1 else ''} op {n_spots} "
                    f"locatie{'s' if n_spots != 1 else ''}. "
                    f"Klik op een stip voor plaats en datum; met + en − zoom je in "
                    f"en uit, en door te slepen verschuif je de kaart.")
            one, many = "foto", "foto's"
        else:
            title = "Where I have seen this species"
            lead = (f"The database holds {pictures} picture"
                    f"{'s' if pictures != 1 else ''} at {n_spots} "
                    f"location{'s' if n_spots != 1 else ''}. "
                    f"Click a dot for the place and date; use + and − to zoom in and "
                    f"out, and drag the map to move around.")
            one, many = "picture", "pictures"

        web_root = MyProject.getBiotaWebRoot()
        data = json.dumps(spots, ensure_ascii=False)
        return _TEMPLATE.format(
            title=html.escape(title), lead=html.escape(lead),
            css=web_root + "includes%20web/leaflet.css",
            js=web_root + "includes%20web/leaflet.js",
            data=data,
            # json.dumps, NOT bare words in quotes: the Dutch plural is "foto's",
            # and an apostrophe dropped into a single-quoted JS string is a syntax
            # error that kills the whole script — the map silently never appears,
            # and only in Dutch. Let JSON do the quoting for every string we hand
            # to JavaScript.
            one=json.dumps(one, ensure_ascii=False),
            many=json.dumps(many, ensure_ascii=False))
    except Exception as e:                 # a map must never take a page down
        logger.warning(f"speciesOccurrenceMap: {species.get('ScientificName','?')}: {e}")
        return ""


# The map initialises only when scrolled into view: the tiles are fetched by the
# VISITOR's browser, so a page that is never scrolled costs OSM nothing.
_TEMPLATE = """
<link rel="stylesheet" href="{css}">
<div class="occurrence-map" style="grid-column:1/-1;margin-top:2em">
  <h3>{title}</h3>
  <p>{lead}</p>
  <div id="occmap" style="height:460px;border:1px solid #ccc"></div>
</div>
<script src="{js}"></script>
<script>
(function () {{
  var spots = {data};
  var el = document.getElementById('occmap');
  if (!el || !window.L || !spots.length) return;
  var started = false;
  function start() {{
    if (started) return;
    started = true;
    var map = L.map('occmap', {{scrollWheelZoom: false}});
    L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
      maxZoom: 17,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }}).addTo(map);
    // Scale bar: without it a dot in a featureless desert gives a reader no sense
    // of distance at all, which is exactly where the map is least self-explanatory.
    L.control.scale({{imperial: false, maxWidth: 140}}).addTo(map);
    var bounds = [];
    spots.forEach(function (s) {{
      bounds.push([s.lat, s.lon]);
      var r = Math.min(14, 6 + Math.sqrt(s.n) * 2);
      var html = '<b>' + (s.place || (s.lat.toFixed(4) + ', ' + s.lon.toFixed(4))) + '</b>';
      var when = s.from && s.to && s.from !== s.to ? s.from + ' &ndash; ' + s.to : (s.from || '');
      if (when) html += '<br>' + when;
      html += '<br>' + s.n + ' ' + (s.n === 1 ? {one} : {many});
      if (s.thumb) html += '<br><img src="' + s.thumb + '" alt="" style="width:220px;margin-top:6px">';
      L.circleMarker([s.lat, s.lon], {{
        radius: r, color: '#b00', weight: 2, fillColor: '#e33', fillOpacity: 0.65
      }}).addTo(map).bindPopup(html, {{maxWidth: 240}});
    }});
    // maxZoom 4, not 9: when a species was only ever seen in one place, fitBounds
    // otherwise slams you into the local streets. Zoom 7 was enough in Europe,
    // where the coastlines and borders are dense enough to recognise — and useless
    // in the Sahel, where it shows an unnamed patch of desert. Zoom 4 puts a lone
    // sighting on a map you can actually place (country, coast, continent), and
    // the reader zooms IN if they want the bush. A wide distribution fits below
    // this cap anyway, so it changes nothing there.
    map.fitBounds(bounds, {{padding: [30, 30], maxZoom: 4}});
  }}
  if ('IntersectionObserver' in window) {{
    var io = new IntersectionObserver(function (entries) {{
      entries.forEach(function (e) {{ if (e.isIntersecting) {{ start(); io.disconnect(); }} }});
    }});
    io.observe(el);
  }} else {{
    start();
  }}
}})();
</script>
"""
