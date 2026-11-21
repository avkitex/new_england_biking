"""Offline Strava -> map schema v1. Private inputs always live outside this repository."""
import argparse
import copy
import csv
import gzip
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime

from pyproj import Transformer
from shapely import transform as transform_xy
from shapely.geometry import LineString, MultiLineString, Point, Polygon, box, shape
from shapely.ops import linemerge, substring, unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent
import paths
PRIVATE = paths.private_dir()
FW = Transformer.from_crs(4326, 26919, always_xy=True).transform
BW = Transformer.from_crs(26919, 4326, always_xy=True).transform
MILE = 1609.344


def transform(project, geometry):
    return transform_xy(geometry, project, interleaved=False)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     delete=False, suffix='.tmp') as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        temp = stream.name
    os.replace(temp, path)


def lines(geometry):
    if geometry.geom_type == 'LineString':
        return [geometry] if geometry.length > .1 else []
    return [line for part in getattr(geometry, 'geoms', []) for line in lines(part)]


def collection(features):
    return {'type': 'FeatureCollection', 'features': features}


class Privacy:
    """Rules are never copied into an output or a log. Subtract complete line geometry."""
    def __init__(self, path):
        self.rules = []
        with Path(path).open(encoding='utf-8-sig', newline='') as stream:
            for number, row in enumerate(csv.DictReader(stream), 2):
                if not row.get('kind', '').strip() or row.get('enabled', '1').strip() in ('0', 'false'):
                    continue
                try:
                    kind = row['kind'].strip()
                    rule = {'kind': kind, 'ride_id': row.get('ride_id', '').strip()}
                    if kind == 'circle':
                        radius = float(row['radius_m'])
                        if radius <= 0: raise ValueError()
                        rule['mask'] = transform(FW, Point(float(row['lon']), float(row['lat']))).buffer(radius, quad_segs=64)
                    elif kind == 'box':
                        w, s, e, n = [float(row[key]) for key in ('west', 'south', 'east', 'north')]
                        if w >= e or s >= n: raise ValueError()
                        rule['mask'] = transform(FW, box(w, s, e, n))
                    elif kind == 'polygon':
                        points = [tuple(float(v) for v in pair.split()) for pair in (row.get('polygon') or '').split(';')]
                        polygon = Polygon(points)
                        if len(points) < 3 or not polygon.is_valid or polygon.area == 0                                 or any(len(p) != 2 for p in points): raise ValueError()
                        rule['mask'] = transform(FW, polygon)
                    elif kind == 'span':
                        rule['start'], rule['end'] = float(row['start_mile']) * MILE, float(row['end_mile']) * MILE
                        if not rule['ride_id'] or not 0 <= rule['start'] < rule['end']: raise ValueError()
                    elif kind != 'ride' or not rule['ride_id']:
                        raise ValueError()
                    self.rules.append(rule)
                except (ValueError, KeyError):
                    raise ValueError(f'Invalid private exclusion at CSV line {number}; no output written.') from None

    def clean_area(self, geometry):
        """Subtract every global mask (and a 2 m guard) from a polygon; ride-scoped rules do not apply to areas."""
        masks = [r['mask'] for r in self.rules if 'mask' in r and not r['ride_id']]
        if not masks:
            return geometry
        return geometry.difference(unary_union(masks).buffer(2))

    def clean(self, geometry, ride_id, geographic_only=False):
        applicable = [r for r in self.rules if not r['ride_id'] or r['ride_id'] == str(ride_id)]
        if any(r['kind'] == 'ride' for r in applicable): return []
        masks = [r['mask'] for r in applicable if 'mask' in r]
        spans = [r for r in applicable if r['kind'] == 'span' and not geographic_only]
        result, offset = [], 0
        for component in lines(geometry):
            intervals = [(0, component.length)]
            for rule in spans:
                start, end = rule['start'] - offset, rule['end'] - offset
                intervals = [(a, min(b, start)) for a, b in intervals if a < start] + [(max(a, end), b) for a, b in intervals if b > end]
            for start, end in sorted(intervals):
                if end - start < .1: continue
                piece = substring(component, start, end)
                # A small guard also protects rounded output endpoints.
                if masks: piece = piece.difference(unary_union(masks).buffer(2))
                result.extend(lines(piece))
            offset += component.length
        # Never simplify across a removed area or reconnect disconnected pieces.
        return result


def geometry(parts):
    coords = [[[round(x, 7), round(y, 7)] for x, y in transform(BW, line).coords] for line in parts]
    return {'type': 'LineString', 'coordinates': coords[0]} if len(coords) == 1 else {'type': 'MultiLineString', 'coordinates': coords}


def make_ride(ride_id, date, projected, states, privacy):
    parts = privacy.clean(projected, ride_id)
    if not parts: return None
    geo = geometry(parts)
    # Only public map fields: no original titles, descriptions, biometrics, source filenames or totals.
    props = {'id': str(ride_id), 'date': date, 'name': 'Cycling ride',
             'km': round(sum(l.length for l in parts) / 1000, 3),
             'gps_km': round(sum(l.length for l in parts) / 1000, 3),
             'states': [f['properties']['name'] for f in states['features'] if shape(geo).intersects(shape(f['geometry']))]}
    return {'type': 'Feature', 'properties': props, 'geometry': geo}


def extract(source, states, privacy):
    region = unary_union([shape(f['geometry']) for f in states['features']])
    output = []
    with (source / 'activities.csv').open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            if row['Activity Type'] not in ('Ride', 'E-Bike Ride', 'Gravel Ride', 'Mountain Bike Ride'): continue
            filename = row.get('Filename', '')
            if not filename: continue
            path = (source / filename).resolve()
            if not path.is_relative_to(source.resolve()): raise ValueError('Invalid activity file path.')
            # Fail closed for unsupported cycling GPS formats rather than silently dropping rides.
            if not path.exists(): raise ValueError('Missing cycling GPS file in export.')
            if not (filename.endswith('.gpx') or filename.endswith('.gpx.gz')):
                raise ValueError('Unsupported cycling GPS format: convert to GPX before import.')
            with (gzip.open(path, 'rb') if filename.endswith('.gz') else path.open('rb')) as track:
                root = ET.parse(track).getroot()
            parts = []
            for segment in root.iter():
                if segment.tag.split('}')[-1] not in ('trkseg', 'rte'): continue
                coords = [(float(p.attrib['lon']), float(p.attrib['lat'])) for p in segment
                          if p.tag.split('}')[-1] in ('trkpt', 'rtept')]
                if len(coords) > 1: parts.append(LineString(coords))
            if not parts or not any(line.intersects(region) for line in parts): continue
            date = datetime.strptime(row['Activity Date'], '%b %d, %Y, %I:%M:%S %p').date().isoformat()
            # Strava's exported activity date is used as the display calendar date.
            projected = transform(FW, MultiLineString(parts))
            ride = make_ride(row['Activity ID'], date, projected, states, privacy)
            if ride: output.append(ride)
    return sorted(output, key=lambda f: (f['properties']['date'], f['properties']['id']))


def match(catalog, rides):
    segments, ride_ids = [], []
    for ride in rides:
        for line in lines(transform(FW, shape(ride['geometry']))):
            for a, b in zip(line.coords, list(line.coords)[1:]):
                segment = LineString([a, b])
                if segment.length >= 1:
                    segments.append(segment); ride_ids.append(ride['properties']['id'])
    tree = STRtree(segments)
    output, matched = copy.deepcopy(catalog), []
    for feature in output['features']:
        p = feature['properties']; g = transform(FW, shape(feature['geometry']))
        hits, ids = [], set()
        if segments and p['category'] == 'path' and p['status'] != 'planned':
            for line in lines(g):
                n = max(1, math.ceil(line.length / 20)); step = line.length / n
                mids = [line.interpolate((j + .5) * step) for j in range(n)]
                pairs = tree.query_nearest(mids, max_distance=20, all_matches=False)
                valid = {}
                for j, k in zip(*pairs):
                    a = line.interpolate(max(0, (j + .5) * step - 8)); b = line.interpolate(min(line.length, (j + .5) * step + 8))
                    c, d = segments[int(k)].coords
                    v, w = (b.x-a.x, b.y-a.y), (d[0]-c[0], d[1]-c[1])
                    cosine = abs(v[0]*w[0]+v[1]*w[1]) / (math.hypot(*v)*math.hypot(*w)+1e-9)
                    if cosine >= math.cos(math.radians(30)): valid[int(j)] = int(k)
                run = []
                for j in range(n+1):
                    if j in valid: run.append(j)
                    else:
                        if len(run)*step >= 100:
                            # Clip matched paths to the visible GPS corridor: no hidden gap inference.
                            hit = substring(line, run[0]*step, (run[-1]+1)*step)
                            corridor = unary_union([segments[valid[v]].buffer(20) for v in run])
                            hits.extend(lines(hit.intersection(corridor)))
                            ids.update(ride_ids[valid[v]] for v in run)
                        run = []
        hit = unary_union(hits)
        p.update(km=round(g.length/1000, 3), ridden_km=round(hit.length/1000, 3),
                 rides=sorted(ids), coverage=round(hit.length/g.length, 3) if g.length else 0)
        if hits: matched.append({'type':'Feature','properties':copy.deepcopy(p),'geometry':geometry(lines(hit))})
    return output, collection(matched)


def note_segments(data):
    result = []
    for kind, key in [('trail','matched'), ('ride','rides')]:
        for feature in data[key]['features']:
            p = feature['properties']; g = transform(FW, shape(feature['geometry']))
            if g.geom_type == 'MultiLineString': g = linemerge(g)
            ordinal = 0
            for line in lines(g):
                for i in range(math.ceil(line.length / (MILE/2))):
                    part = substring(line, i*MILE/2, min((i+1)*MILE/2, line.length))
                    if part.geom_type != 'LineString' or part.length < .1: continue
                    ordinal += 1
                    coords = [[round(x,6), round(y,6)] for x,y in transform(BW,part).coords]
                    identity = json.dumps([kind, str(p['id']) if kind=='ride' else p['name'].casefold(), min(coords, list(reversed(coords)))],separators=(',',':'))
                    sid = kind+'-'+hashlib.sha256(identity.encode()).hexdigest()[:24]
                    result.append({'type':'Feature','properties':{'segment_id':sid,'kind':kind,'parent_id':p['id'],'name':p['name'],'number':ordinal,'miles':round(part.length/MILE,4),'date':p.get('date'),'states':p.get('states',[p.get('state')])},'geometry':{'type':'LineString','coordinates':coords}})
    # Coincident duplicate components should not create duplicate IDs.
    return collection(list({f['properties']['segment_id']:f for f in result}.values()))


def build(rides, date, assets, privacy, annotations=None):
    catalog, matched = match(assets['catalog'], rides)
    # Matching can extend by its 20 m tolerance; enforce geographic masks again.
    safe = []
    for feature in matched['features']:
        parts = []
        for ride_id in feature['properties']['rides']:
            parts.extend(privacy.clean(transform(FW,shape(feature['geometry'])), ride_id, geographic_only=True))
        if not parts: continue
        g = unary_union(parts)
        feature['geometry'] = geometry(lines(g))
        feature['properties']['ridden_km'] = round(g.length/1000,3)
        feature['properties']['coverage'] = round(g.length/(feature['properties']['km']*1000),3) if feature['properties']['km'] else 0
        safe.append(feature)
    match_by_id = {f['properties']['id']: f for f in safe}
    for feature in catalog['features']:
        p = feature['properties']; current = match_by_id.get(p['id'])
        for key in ('ridden_km','coverage','rides'):
            p[key] = current['properties'][key] if current else ([] if key=='rides' else 0)
    overlays, orphans = collection([]), 0
    if annotations:
        import annotate  # lazy: annotate imports this module
        vocab, items = annotations
        overlays, orphans = annotate.resolve(catalog, items, vocab, privacy)
    data = {'schema_version':1,'version':date,'rides':collection(rides),'catalog':catalog,'matched':collection(safe),'states':assets['states'],
            'catalog_info':assets['catalog_info'],'method':'Visible cycling GPS geometry; approximate trail matching. Distances measure displayed geometry.',
            'vocabulary':annotations[0] if annotations else None,'overlays':overlays,'orphaned_annotations':orphans}
    data['note_segments'] = note_segments(data)
    return data


def assemble(editor):
    """Build one page from web/ parts; editor-only markup is stripped from the public page."""
    web = ROOT / 'web'
    html = (web / 'page.html').read_text(encoding='utf-8')
    html = re.sub(r'<!--#editor-->(.*?)<!--#end-->', (lambda m: m.group(1)) if editor else '', html, flags=re.S)
    script = ((web / 'exclude.js').read_text(encoding='utf-8') + '\n' if editor else '') + (web / ('editor.js' if editor else 'viewer.js')).read_text(encoding='utf-8') + '\n' + (web / 'core.js').read_text(encoding='utf-8')
    for key, text in [('/*STYLE*/', (web / 'style.css').read_text(encoding='utf-8')), ('/*SCRIPT*/', script),
                      ('/*LEAFLET_CSS*/', (ROOT / 'assets/leaflet.css').read_text(encoding='utf-8')),
                      ('/*LEAFLET_JS*/', (ROOT / 'assets/leaflet.js').read_text(encoding='utf-8'))]:
        html = html.replace(key, text)
    return html


def render():
    (ROOT / 'index.html').write_text(assemble(True), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=PRIVATE/'settings.json')
    args = parser.parse_args()
    config = read(args.config)
    base = args.config.resolve().parent
    privacy = Privacy(paths.resolve(base, config['exclusions']))
    assets = read(ROOT/'assets/catalog.json')
    import annotate
    vocab = annotate.load_vocabulary()
    annotations = (vocab, annotate.load_items(vocab))
    versions = []
    for snapshot in sorted(config['snapshots'],key=lambda s:s['date']):
        date = snapshot['date']
        datetime.strptime(date,'%Y-%m-%d')
        if snapshot['kind'] == 'strava':
            rides = extract(paths.resolve(base, snapshot['path']),assets['states'],privacy)
        elif snapshot['kind'] == 'baseline':
            rides = []
            for feature in read(paths.resolve(base, snapshot['path']))['rides']['features']:
                p = feature['properties']
                ride = make_ride(p['id'],p['date'],transform(FW,shape(feature['geometry'])),assets['states'],privacy)
                if ride: rides.append(ride)
        else: raise ValueError('Unknown private snapshot kind.')
        versions.append(build(rides,date,assets,privacy,annotations))
    if len({v['version'] for v in versions}) != len(versions): raise ValueError('Duplicate snapshot dates.')
    # Compute and validate every snapshot before changing any public file.
    for data in versions:
        assert all(f['properties']['miles'] <= .5 for f in data['note_segments']['features'])
    manifest = {'schema_version':1,'latest':versions[-1]['version'],'versions':[]}
    for data in versions:
        path = 'data/'+data['version']+'.json'
        write(ROOT/path,data)
        manifest['versions'].append({'date':data['version'],'url':path,'rides':len(data['rides']['features'])})
    write(ROOT/'data/versions.json',manifest)
    render()
    import public  # lazy: public imports this module
    public.write_site(public.export_public(versions[-1], privacy), ROOT/'site', assemble)
    if versions[-1]['orphaned_annotations']: print(f"Warning: {versions[-1]['orphaned_annotations']} annotation(s) no longer match the catalog and were skipped.")
    print('Built map snapshots: '+', '.join(f"{v['date']} ({v['rides']} rides)" for v in manifest['versions']))


if __name__ == '__main__': main()
