# Country names — one table, any spelling in, one name out

Status: AGREED (2026-11-10): option B (all ISO countries from CLDR), wrong values as aliases with a
note. Former names go in as aliases (Eswatini ← Swaziland). Country is never a key, so this is
display only. New names arrive from the place lookup (Nominatim), so its EN/NL spellings must
resolve too; the inventory report catches any that don't.

Progress: steps 1+2 committed 414cb1e. Steps 3+4 built 2026-11-10, awaiting a GUI check. The map
opens on the whole range and zooms to the country (`MapView(focus_coords=…)`).

## Problem

The `Country` field in pictures holds whatever was typed or geocoded over the years:
`Netherlands` / `Nederland`, `UK` / `England` / `United Kingdom`, `USA` / `United States` /
`Verenigde Staten van Amerika`, `Belgie` / `Belgium`, `Ecuados`, and three values that are not
countries at all (`Friesland`, `Oostvoorne`, `Texas`). About 40 distinct values, ~25 real countries
(see `docs/design-docs/landcodes.xlsx`, Sheet2 = inventory, Sheet1 = the table).

Effects today:

- The Biota Browser chip shows `South Africa · Zuid-Afrika` as if they were two countries.
- Find Trips groups by `(country, state)`, so a Dutch weekend under `Nederland` and one under
  `Netherlands` are counted as different places.
- The website shows the Dutch name on an English page and the other way round.

## Rule

**The files are never changed.** The pictures keep their stored text. One table translates
*any spelling we have used* → ISO code → *the name for the reader's language*.

```
stored "Zuid-Afrika" ─┐
stored "South Africa" ─┼─► ZA ─► EN page: "South Africa"
                       │         NL page: "Zuid-Afrika"
```

Unknown spellings pass through **unchanged** (never blank, never guessed) and show up in the
inventory report, so a new country (Madagascar) or a new spelling is visible, not silent.

## The table — `countries.csv` (shipped with the app)

| iso | en | nl | aliases (`;`-separated) |
|---|---|---|---|
| ZA | South Africa | Zuid-Afrika | |
| GB | United Kingdom | Verenigd Koninkrijk | England; UK; Groot-Brittannië |
| US | United States | Verenigde Staten | USA; VS; Verenigde Staten van Amerika; Texas |
| NL | Netherlands | Nederland | Friesland; Oostvoorne |
| BE | Belgium | België | Belgie |
| EC | Ecuador | Ecuador | Ecuados |
| … | | | |

- Matching is case- and accent-insensitive (`Belgie` = `België`), so we need fewer aliases.
- The `en` and `nl` columns count as aliases too: a picture tagged `Nederland` resolves to NL.
- A CSV, not the .xlsx: readable in a diff, no Excel dependency at runtime, works offline.

Notes from your Sheet1:

- Brazil: the EN column says `Brazilie`; that should be EN `Brazil`, NL `Brazilië`, alias `Brasil`.
- `Zweden` is in the inventory (Sheet2) but missing from Sheet1 → SE, Sweden / Zweden.
- Eswatini has no NL name yet (it is `Eswatini` in Dutch too).
- **Wrong values** (`Friesland`, `Oostvoorne`, `Texas`): either add them as aliases (they show
  correctly at once, files untouched) **or** leave them unknown, so they stay visible as data to fix
  with the Location editor.
  *Recommendation: add them as aliases, marked in a `note` column,* so the website is right now and
  the report still lists them as "resolved by a fix-up alias".

### Only our countries, or all of them?

- **A. Only our ~27 countries plus Madagascar.** Small, hand-checked. A new country shows its stored
  name until you add a row.
- **B. All ~250 ISO countries** with EN/NL names, generated once from CLDR (the Unicode locale data)
  by a dev-only script, then committed. Your own aliases go on top.

*Recommendation: B.* It costs one script run, and Madagascar, or any later trip, works offline
without a table edit. Windows' own country API is not an option: it only names countries in the
Windows language and does not exist on the Mac.

## Which language

| Where | Language comes from |
|---|---|
| Website pages | the page's own language: `MyProject.getLanguage()`, so EN and NL pages each get their own |
| GUI (Biota, viewers, Find Trips, maps) | the first of `en`/`nl` in the **Preferred names** profile (`utils/name_profile`); `sci_en` → en, `nl_sci` → nl |

No new setting.

## Steps (each one stands alone and ships separately)

### 1. `engine/countries.py` + `countries.csv`: no behaviour change

Three functions, loaded once and cached:

- `iso_of(text) -> str`: `"Zuid-Afrika"` → `"ZA"`; unknown → `""`
- `country_name(text, lang) -> str`: display name; unknown → `text` unchanged
- `inventory(values) -> rows`: (stored text, count, iso, how it resolved: name / alias / unknown)

Unit tests: every Sheet2 value resolves; unknown passes through; accent/case folding.

### 2. Inventory report: read-only

`tools/country_inventory.py` (or a Master ▸ menu item later) runs `SELECT Country, COUNT(*)` on the
master and prints the inventory, with **unknown** at the top. This is the QA guardrail: after
Madagascar, run it and see what Nominatim wrote.

### 3. Display: route the touch points through `country_name()`

The display paths go through a few functions, as you expected:

| File | What |
|---|---|
| [engine/websupport.py:138](../../speciesviewer/engine/websupport.py#L138) `getFormattedCityStateCountry` | **the** website function. It gets a `lang` argument. `_locationParts` / `getFormattedLocation` pass it through; their callers (lines 803, 1090, 1142, [webmap.py:215](../../speciesviewer/engine/webmap.py#L215)) already have `MyProject`. Also the `Location == Country` comparison at line 186 |
| [gui/species_panel.py:371](../../speciesviewer/gui/species_panel.py#L371) | Biota chip: dedupe **by ISO**, show the localized name |
| [engine/trip_finder.py:117](../../speciesviewer/engine/trip_finder.py#L117) | group by ISO, so NL and Nederland count together; label in the GUI language |
| [engine/range_check.py:61](../../speciesviewer/engine/range_check.py#L61) `_place_of` | count by ISO, show the localized name |
| [gui/large_image_view.py:2315](../../speciesviewer/gui/large_image_view.py#L2315), [gui/map_view.py:2818](../../speciesviewer/gui/map_view.py#L2818) | admin line under a picture / map bubble |
| [db/master_db.py:714](../../speciesviewer/db/master_db.py#L714) | map bubble "place" fallback; localize in Python after the query |

**Not touched:** reading, writing and editing (`geolocate`, `location_edit`, `place_sync`,
sidecars, clipboard copy/paste). The Location editor keeps showing the stored text, because that is
what you are editing.

Published websites change their country text only after a rebuild, the same as any other template
change.

### 4. Clickable country chip in the Biota Browser (your sample)

- Left-click the `South Africa` chip → the existing species **Map**, zoomed to this species' points
  **in that country**. The Map button stays as it is (whole range).
- Context menu on the chip: *Show on map* · *Copy "South Africa"*.
- One chip per country (up to 3, as now). A country with no GPS points for this species gets a
  plain, non-clickable chip, so it never looks clickable when it does nothing.

How it works: `gps_points_for_species` also returns each point's `Country`. The chip handler keeps
the points whose `iso_of()` matches and opens the same `MapView` with them, so the map fits to that
country's points. No country outlines or bounding boxes are needed, and it works offline.

Option for later: the map opens on the whole range and the country is only *zoomed* (all points
still drawn). This needs a `zoom_to=` bounds argument on `MapView`. *Recommendation: start with
"zoom only"* if `fit_to_points` can take a subset cheaply; otherwise filter. To be decided when
building.

## Risk / what to test

- Find Trips results change where NL/Nederland were split, which is intended. Compare before and
  after on the main master.
- An EN website page that used to show `Nederland` now shows `Netherlands`. Rebuild one album and
  look at it.
- Nothing writes to pictures, CSVs or sidecars, so there is no data-loss path.

## Estimate

Step 1+2: small (one module, one CSV, one script, tests). Step 3: about 7 call sites. Step 4: one
chip handler plus a country column in one query.
