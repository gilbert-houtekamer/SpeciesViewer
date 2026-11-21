# How-to: GPS and place names

GPS comes up in many chapters, because a position can be added at many moments.
This page is the overview: how a picture gets a position, how a position becomes
a place name, and where to go for each.

## How a picture gets a position

| Source | When | Where |
|---|---|---|
| **The camera** records it | When you shoot, if the camera (or a connected phone) has GPS on | Nothing to do. A position the camera recorded is never moved or removed by SpeciesViewer. |
| **A GPS log** (tracklog) | The camera had no GPS, or you want **altitude**, which cameras do not record | Put the log in `<pictures folder>\gpslogs\<year>\`. Then the **🛰️** column in the [Days panel](03-days.md#the-days-panel) or the [Trip panel](04-trips.md#the-trip-panel); for the master, [Geotag from Tracklog](06-master.md). |
| **A neighbouring picture** | One picture of a series has no GPS | *Copy / Paste GPS + place* in the viewers ([10. The viewers](10-viewers.md)). |
| **By hand on the map** | No GPS at all — an old trip, a scanned slide, a picture from a friend | Drag it onto the map, let SpeciesViewer suggest a spot, or look a place up by name: [Placing pictures without GPS on the map](10-viewers.md#placing-pictures-without-gps-on-the-map). |

A position placed by hand can be moved again or removed (*Remove GPS +
place…*). One that came from the camera cannot.

## From a position to a place name

SpeciesViewer looks the place up on OpenStreetMap ("Lesvos, Greece"). How
detailed the name is, in which language, and whether a lookup may replace a
place you already have, is set in [File ▸ Settings ▸ Location](01-file.md#location).
A place name you typed yourself always wins over one that was looked up.

Looking up needs an internet connection. Without one, the position is kept and
the name follows later.

## Finding pictures without GPS

- In a viewer, the **📍** view filter shows only the pictures without GPS, and a
  card shows **❓** where the position is missing ([10. The viewers](10-viewers.md)).
- On a map, the pictures without GPS wait in a strip above the map, ready to be
  placed.

## Repairs in the master

In the *Master* menu ([6. Master](06-master.md)); filling GPS from the files is a repair, in *Master ▸ Check the Master…*:

- **Add Missing Location Names…** — pictures with a position but no place name.
- **Fill Picture GPS from Files…** — the camera file has a position, but the
  master's list does not.
- **Geotag from Tracklog…** — match master pictures to a GPS log by time.

All three show what they would change and ask first. For an album:
[Sync Placed GPS into the Index…](07-albums.md).

## On the website

The website shows where a species was seen on a map, but **the published JPGs
carry no GPS**. That is on purpose: a nest or a rare orchid should not be found
from a downloaded picture.
