# New England cycling map

Turn your own Strava rides into a map of New England bike trails: a private ETL and local editor, and a public read-only page that shows only anonymous coverage and trail annotations.

Run **Start Map.ps1**, then open http://127.0.0.1:8765/ (the editor). **Data updated** switches between your registered private snapshots. The trail catalog was downloaded on October 8, 2026.

## Private inputs and exclusions

All machine-specific files are in `data/` of the main checkout (override with the `NE_BIKING_PRIVATE` environment variable). That folder is git-ignored and is not a temp location. Relative paths in `settings.json` are resolved against its own folder:

- `settings.json`: dated input registry; keep each source export or private baseline for reproducible rebuilding.
- `baseline-<date>.json`: an optional earlier processed GPS file registered as a snapshot of kind `baseline`, used only by ETL.
- `exclusions.csv`: local removal rules. Edit this in a text editor or Excel; **Update Data.ps1** rebuilds every registered version.
- `private-notes.json` and `.backup.json`: preserved personal notes. Hidden/orphaned note geometries are not returned to the map.

The initial exclusion file has a header only: **no home location is assumed or removed automatically**. Add a row per rule. Empty `ride_id` means every ride for circle/box rules. Use `enabled=0` to disable a row. Coordinates are longitude/latitude in WGS84; distances in `span` rows are miles along the original ride, across its ordered GPS components. Use the Strava activity ID for `ride_id`.

| kind | Required fields | Effect |
|---|---|---|
| `circle` | `lat,lon,radius_m` | Remove geometry within the radius, with a 2 m rounding guard |
| `box` | `west,south,east,north` | Remove geometry inside a bounding box, with a 2 m guard |
| `ride` | `ride_id` | Remove the whole ride |
| `span` | `ride_id,start_mile,end_mile` | Remove a mileage interval from that ride |

CSV columns, in order:

```text
enabled,kind,ride_id,lat,lon,radius_m,west,south,east,north,start_mile,end_mile
```

For example, enter `1` in enabled, `circle` in kind, your latitude/longitude, and `500` in radius_m; leave other cells empty. Keep these actual values only in the private CSV. The repository does not contain an exclusion list, removal report, original activity titles, or raw export. Removed line pieces are never reconnected. Exclusions precede matching, distance calculations, and comment segmentation; masks are enforced again on approximate matches.

**Git deliberately ignores all generated ride snapshots (`data/`) and generated HTML (`index.html`) and the public build (`site/`), even after sanitizing.** This prevents a place later removed from remaining in earlier Git commits. The files are physically in this repository folder for the local app, but absent from Git history. Only code, the template, dependencies and public catalog assets are committed. Do not force-add personal datasets. A shared copy of prepared JSON still reveals the remaining visible GPS, activity IDs and ride dates. The local notes API is not a deployment target for public hosting.

For future updates, add an entry to the private `settings.json` snapshot array:

```json
{"date":"YYYY-MM-DD","kind":"strava","path":"C:/path/to/extracted/Strava/export"}
```

Then run **Update Data.ps1** and reload the map. It rebuilds **all registered snapshots** under the current exclusions. Do not delete historical exports while they are still registered. GPX and GPX.gz cycling tracks are supported; unsupported cycling GPS formats fail explicitly. Non-cycling activities are not imported. No network access is used by ETL. Removing files from a shared site or Git history created outside this workflow is a separate operation.

## Map data contract v1

`data/versions.json` has `schema_version`, `latest`, and `versions[{date,url,rides}]`. Each dated JSON has `schema_version:1`, `version:YYYY-MM-DD` and GeoJSON FeatureCollections: `rides`, `catalog`, `matched`, `states`, `note_segments`, plus catalog provenance. The page loads these files using fetch; it does not understand or access Strava exports or private ETL inputs. The server only serves these explicitly registered outputs and the map HTML.

Ride properties are allowlisted: activity ID, calendar date, generic title, visible geometry length and intersecting states. Original Strava distance is not published after cuts. Lengths are projected in EPSG:26919; displayed miles are derived from meters. Parts stay separate, without shortcuts across removed areas. Approximate trail matching uses 20 m distance / 30 degree direction / >=100 m continuous overlap. This can confuse parallel roads and paths; coverage is not proof of access or a closure check. Note segments are <=0.5 mile, with geometry-derived IDs. Changed geometry can orphan an old note; it stays in private storage, and is never automatically reassigned.

## Development

Python 3.12+:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python etl.py
.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python server.py
```

`etl.py` transforms all registered private sources, applies exclusions, matches against `assets/catalog.json`, generates half-mile segments and writes display snapshots. It assembles two pages from `web/` (`page.html`, `style.css`, `core.js` plus `editor.js` or `viewer.js`) and the local Leaflet assets:

- `index.html`: the **editor**, served only by `server.py` on 127.0.0.1. It reads every private snapshot, shows Strava links and edits private notes (token/origin checks, atomic replacement, a backup and revision conflict detection).
- `site/index.html` + `site/data.json`: the **public viewer**, built from the latest snapshot only by `public.py`. It is read-only, fetches `data.json` by a relative URL (works under `user.github.io/repo/`) and has no API.

The public file is an allowlist: trail catalog, trail-matched geometry and one dissolved coverage layer (all rides merged on an 8 m grid, shuffled, with no per-ride features). It never contains activity IDs (they reveal time), dates, ride counts, Strava links, note-segment IDs or older snapshots. `tests/test_public.py` checks this, including against the real build when `site/` exists. Run the tests locally before publishing; there is no CI.

The HTTP listener binds to 127.0.0.1 only. ETL errors never intentionally print rule values; invalid rules fail closed.

## Trail annotations

`annotations/vocabulary.json` fixes the allowed values: surface (asphalt, boardwalk, fine gravel, loose gravel, dirt, unknown), passability, grade, road crossings, and tags with icons. `annotations/annotations.json` holds edits as committed, public files. An item targets a whole catalog trail (`{"trail": id, "name": "..."}`), part of one (`from_m`/`to_m` in metres along the trail) or a free line (`{"geometry": ...}`). Per field, later items win; tags are unioned. A whole-trail surface edit also corrects the trail's own surface class, so the dashed "unpaved" style and the Surface filter follow it. If the catalog changes so that an item's name no longer matches its id, the item is skipped and counted as an orphan; it is never reassigned. Both pages can colour trails by any field, filter by tag and show tag icons from zoom 11. Annotations are clipped by the private exclusion masks. Comments are published, so keep them free of private details. A dashed trail line means unpaved; planned trails use a longer orange dash.

### Editing in the local map

In the editor, click a trail to select it; Ctrl/Shift-click adds or removes trails; Shift-drag draws a box that adds every visible trail it touches; **Select all shown** takes the current filters (state, search, surface, tag...). With exactly one trail selected, **Pick part of the selected trail** takes two clicks and edits only that stretch. Every control reads "no change" until you touch it: set a value, tick a tag (a half-ticked tag means only some selected trails have it) or type a comment, then **Apply**. "(clear)" removes a value. Per trail the file stores non-overlapping pieces, so edits merge back into whole-trail records. The server writes `annotations/annotations.json` atomically, keeps a backup in the private folder and rejects stale saves (another tab) with a conflict. Run **Update Data.ps1** afterwards to refresh `site/`.

Dashed GPS tracks are selectable like solid trails, together with them in one selection (the "Catalog trails / GPS tracks" ticks decide what box-select and "Select all shown" take): click half-mile sections of your tracks, Ctrl/Shift-click to add, Shift-drag a box, or click an existing purple road annotation to edit it in place. The server snaps the selection to the same 8 m grid as the public coverage layer and cuts it with your exclusion rules (ride-scoped ones too) BEFORE writing it to `annotations.json`; it refuses to save if the exclusion settings are missing. Only the resulting geometry is stored, under a hash id: no activity ID, date or segment ID. Re-selecting the identical sections updates the same record; overlapping selections layer (later wins) and can only be cleared by selecting the record itself. The old half-mile private-notes flow was removed from the editor; no notes were migrated because none existed.

### Removing places (private)

In the editor's **Remove from map** panel, draw a circle (centre, then edge), box (two corners) or polygon (corners, Enter or double-click). The preview says how many rides and miles of GPS the shape would cut. **Confirm removal** appends the rule to your private `exclusions.csv` (new `polygon` kind with a `polygon` column holding `lon lat;lon lat;...`; older files are upgraded and a backup is kept in the private folder). The file is validated as real rules before it replaces the old one. Nothing changes on the map until **Rebuild now** re-runs the ETL for every snapshot, and the public site then needs the usual publish. Rules apply to all rides; mileage `span` rules still have to be written by hand because the editor only sees cut geometry.


### Selecting long stretches

Besides clicking sections: **Alt-click** a second section of the same ride to take everything between it and the last one you clicked; **Select along a line** (click points along a road, Enter or double-click) takes every GPS section of every ride that runs within the corridor half-width (default 30 m) along that line, so overlapping rides are picked at once; box-select still works. Selecting only changes what the next **Apply** edits.

### Blocked sections

**Draw: non-bikeable area** is for a place where you cannot ride at all (water, private land, a missing link): click the corners, Enter or double-click. It is stored as an area (red, hatched outline, translucent fill, never a line) with passability "not rideable", clipped by the exclusion rules. Click the area to select it; Passability "(clear)" + Apply deletes it. To mark part of a catalog trail that does not exist in reality, pick the trail, use **Pick part of the selected trail** and set Passability to *Not rideable*; such sections are drawn red on both pages. Annotated GPS tracks show as regular solid lines.

## License

Original project code is licensed under the [Apache License 2.0](LICENSE). Third-party libraries and trail datasets retain their own licenses and terms; the Apache license does not relicense those assets.

## Sources and licenses

Catalog geometry is a personal local copy downloaded October 8, 2026: Trailspotting regional rail trails, MassGIS, NHDOT, VTrans, RIDOT/RIGIS and CT DEEP, with source URLs retained per feature. It is not an exhaustive or live inventory. Respect each source's terms before redistributing catalog assets. Basemap tiles are loaded on demand from OpenStreetMap with attribution. Leaflet 1.9.4 is BSD-2-Clause; see `assets/LEAFLET-LICENSE.txt`.
