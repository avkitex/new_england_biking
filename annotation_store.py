"""Edit annotations.json from the local editor.

Edits are "paint" operations on trails (or parts of trails). Per trail the file is kept as
non-overlapping pieces, so clearing a value really clears it and repeated edits don't pile
up layers. Geometry-target items and items orphaned by a catalog change are left untouched.
"""
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path

import shapely
import shapely.geometry
from shapely.geometry import MultiLineString, Point, shape
from shapely.ops import linemerge, unary_union

import annotate
import etl
import public

ROOT = Path(__file__).resolve().parent
EPS = 0.25  # metres; shorter slivers are dropped
MAX_TARGETS = 5000
MAX_ROADS = 20000
MAX_POINTS = 1000
_catalog_cache = {}


def load_catalog(path=None):
    path = Path(path or ROOT / 'assets/catalog.json')
    key = str(path)
    if key not in _catalog_cache:
        _catalog_cache[key] = {f['properties']['id']: f for f in etl.read(path)['catalog']['features']}
    return _catalog_cache[key]


def components(feature):
    return etl.lines(etl.transform(etl.FW, shape(feature['geometry'])))


def total_length(feature):
    return sum(c.length for c in components(feature))


def along(feature, lon, lat):
    """Metres along the trail (components in order) of the point nearest to lon/lat."""
    point = Point(*etl.FW(lon, lat))
    best, offset = None, 0.0
    for line in components(feature):
        distance = line.distance(point)
        if best is None or distance < best[0]:
            best = (distance, offset + line.project(point))
        offset += line.length
    if best is None:
        raise annotate.AnnotationError('Trail has no geometry.')
    return best[1]


def _normalize(props):
    out = {k: props[k] for k in annotate.FIELD_KEYS if k in props}
    if props.get('tags'):
        out['tags'] = list(props['tags'])
    if props.get('comment'):
        out['comment'] = props['comment']
    return out


def _append(out, s, e, props):
    if out and out[-1][2] == props and abs(out[-1][1] - s) < 1.0:
        out[-1] = (out[-1][0], e, props)
    else:
        out.append((s, e, props))


def to_pieces(items, total):
    """Layered items of one trail -> non-overlapping, merged (start, end, props) pieces."""
    ranges = [(i, i['target'].get('from_m', 0), min(i['target'].get('to_m', total), total)) for i in items]
    cuts = sorted({0.0, total, *[v for _, a, b in ranges for v in (a, b) if 0 < v < total]})
    out = []
    for s, e in zip(cuts, cuts[1:]):
        if e - s < EPS:
            continue
        mid = (s + e) / 2
        props = annotate._effective([i for i, a, b in ranges if a <= mid <= b])
        if props:
            _append(out, s, e, props)
    return out


def _edited(props, edit):
    props = copy.deepcopy(props)
    for key, value in (edit.get('set') or {}).items():
        if value is None:
            props.pop(key, None)
        else:
            props[key] = value
    tags = [t for t in props.get('tags', []) if t not in edit.get('tags_remove', [])]
    tags += [t for t in edit.get('tags_add', []) if t not in tags]
    if tags:
        props['tags'] = tags
    else:
        props.pop('tags', None)
    if 'comment' in edit:
        text = (edit['comment'] or '').strip()
        if text:
            props['comment'] = text
        else:
            props.pop('comment', None)
    return _normalize(props)


def paint(pieces, total, a, b, edit):
    cuts = sorted({0.0, total, a, b, *[v for s, e, _ in pieces for v in (s, e)]})
    out = []
    for s, e in zip(cuts, cuts[1:]):
        if e - s < EPS:
            continue
        mid = (s + e) / 2
        props = next((p for ps, pe, p in pieces if ps <= mid <= pe), {})
        if a <= mid <= b:
            props = _edited(props, edit)
        if props:
            _append(out, s, e, props)
    return out


def pieces_to_items(trail_id, name, pieces, total):
    items = []
    for s, e, props in pieces:
        target = {'trail': trail_id, 'name': name}
        if not (s <= 0.5 and e >= total - 0.5):
            target['from_m'], target['to_m'] = round(s, 1), round(e, 1)
        item = {'id': f't{trail_id}-{s:.1f}', 'target': target}
        fields = {k: props[k] for k in annotate.FIELD_KEYS if k in props}
        if fields:
            item['set'] = fields
        if props.get('tags'):
            item['tags'] = props['tags']
        if props.get('comment'):
            item['comment'] = props['comment']
        items.append(item)
    return items


def validate_edit(vocab, edit):
    allowed = {k: {v['id'] for v in vocab['fields'][k]['values']} for k in annotate.FIELD_KEYS}
    tags = {t['id'] for t in vocab['tags']}
    if not isinstance(edit, dict) or set(edit) - {'targets', 'set', 'tags_add', 'tags_remove', 'comment'}:
        raise annotate.AnnotationError('Unexpected edit structure.')
    targets = edit.get('targets')
    if not isinstance(targets, list) or not 0 < len(targets) <= MAX_TARGETS:
        raise annotate.AnnotationError('Select between 1 and %d trails.' % MAX_TARGETS)
    for t in targets:
        if not isinstance(t, dict):
            raise annotate.AnnotationError('Invalid target.')
        if 'roads' in t:
            if set(t) != {'roads'} or not isinstance(t['roads'], list) or not 0 < len(t['roads']) <= MAX_ROADS                     or not all(isinstance(r, str) for r in t['roads']):
                raise annotate.AnnotationError('Invalid GPS road selection.')
            continue
        if 'draw' in t or 'area' in t:
            key = 'draw' if 'draw' in t else 'area'
            pts = t.get(key)
            if set(t) != {key} or not isinstance(pts, list) or not (2 if key == 'draw' else 3) <= len(pts) <= MAX_POINTS                     or not all(isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p) for p in pts)                     or not all(-74 <= p[0] <= -66 and 40 <= p[1] <= 48 for p in pts):
                raise annotate.AnnotationError('Invalid drawn shape. Draw inside New England.')
            continue
        if 'item' in t:
            if set(t) != {'item'} or not isinstance(t['item'], str):
                raise annotate.AnnotationError('Invalid road item.')
            continue
        if set(t) - {'trail', 'a', 'b'} or not isinstance(t.get('trail'), int):
            raise annotate.AnnotationError('Invalid target.')
        if ('a' in t) != ('b' in t):
            raise annotate.AnnotationError('A range needs both points.')
        for key in ('a', 'b'):
            if key in t and not (isinstance(t[key], list) and len(t[key]) == 2 and all(isinstance(v, (int, float)) for v in t[key])):
                raise annotate.AnnotationError('Invalid range point.')
    for key, value in (edit.get('set') or {}).items():
        if key not in allowed or (value is not None and value not in allowed[key]):
            raise annotate.AnnotationError('Value is not in the vocabulary.')
    if any(t not in tags for t in edit.get('tags_add', []) + edit.get('tags_remove', [])):
        raise annotate.AnnotationError('Unknown tag.')
    comment = edit.get('comment')
    if comment is not None and (not isinstance(comment, str) or len(comment) > annotate.MAX_COMMENT):
        raise annotate.AnnotationError('Comment too long.')


def _road_id(geometry):
    return 'r-' + hashlib.sha1(json.dumps(geometry, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:12]


def _paint_item(items, item_id, geometry, edit, key='geometry'):
    """Edit (or create) one geometry item in place; an item left without any value is removed."""
    index = next((n for n, i in enumerate(items) if i['id'] == item_id), None)
    if index is None and geometry is None:
        raise annotate.AnnotationError('Unknown road item.')
    old = items[index] if index is not None else {'id': item_id, 'target': {key: geometry}}
    if 'geometry' not in old['target'] and 'area' not in old['target']:
        raise annotate.AnnotationError('Not a road item.')
    props = _edited(annotate._effective([old]), edit)
    new = {'id': item_id, 'target': old['target']}
    fields = {k: props[k] for k in annotate.FIELD_KEYS if k in props}
    if fields:
        new['set'] = fields
    if props.get('tags'):
        new['tags'] = props['tags']
    if props.get('comment'):
        new['comment'] = props['comment']
    if index is None:
        if props:
            items.append(new)
    elif props:
        items[index] = new
    else:
        del items[index]


def apply_edit(items, catalog, vocab, edit, roads=None, draw=None, area=None):
    """`roads(ids)` -> GeoJSON geometry (already snapped and privacy-cut) or None; supplied by the server."""
    validate_edit(vocab, edit)
    items = list(items)
    for target in edit['targets']:
        if 'item' in target:
            _paint_item(items, target['item'], None, edit)
            continue
        if 'area' in target:
            if area is None:
                raise annotate.AnnotationError('Drawing is unavailable here.')
            geometry = area(target['area'])
            if geometry is None:
                raise annotate.AnnotationError('Nothing is left of that area after the privacy cuts.')
            _paint_item(items, _road_id(geometry), geometry, edit, key='area')
            continue
        if 'draw' in target:
            if draw is None:
                raise annotate.AnnotationError('Drawing is unavailable here.')
            geometry = draw(target['draw'])
            if geometry is None:
                raise annotate.AnnotationError('Nothing is left of that line after the privacy cuts.')
            _paint_item(items, _road_id(geometry), geometry, edit)
            continue
        if 'roads' in target:
            if roads is None:
                raise annotate.AnnotationError('GPS road editing is unavailable here.')
            geometry = roads(target['roads'])
            if geometry is None:
                raise annotate.AnnotationError('Nothing is left of that selection after the privacy cuts.')
            _paint_item(items, _road_id(geometry), geometry, edit)
            continue
        feature = catalog.get(target['trail'])
        if feature is None:
            raise annotate.AnnotationError('Unknown trail.')
        name = feature['properties']['name']
        total = total_length(feature)
        a, b = 0.0, total
        if 'a' in target:
            a, b = sorted((along(feature, *target['a']), along(feature, *target['b'])))
            if b - a < 1:
                raise annotate.AnnotationError('The two points are too close together.')
            if a <= 0.5 and b >= total - 0.5:
                a, b = 0.0, total
        mine, rest = [], []
        for item in items:
            t = item['target']
            (mine if t.get('trail') == target['trail'] and t.get('name', name) == name else rest).append(item)
        pieces = paint(to_pieces(mine, total), total, a, b, edit)
        items = rest + pieces_to_items(target['trail'], name, pieces, total)
    trail_items = sorted((i for i in items if 'trail' in i['target']),
                         key=lambda i: (i['target']['trail'], i['target'].get('from_m', 0)))
    other = [i for i in items if 'trail' not in i['target']]
    items = trail_items + other
    annotate.validate_items(vocab, items)
    return items


def revision(path):
    path = Path(path)
    return hashlib.sha1(path.read_bytes()).hexdigest() if path.exists() else '0'


def write_items(path, items, backup_dir=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and backup_dir:
        Path(backup_dir).mkdir(parents=True, exist_ok=True)
        (Path(backup_dir) / 'annotations.backup.json').write_bytes(path.read_bytes())
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False, suffix='.tmp') as f:
        json.dump({'version': 1, 'items': items}, f, ensure_ascii=False, indent=1)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
        temp = f.name
    os.replace(temp, path)


def state(items, catalog, vocab):
    """Everything the editor needs after a change: resolved overlays and per-trail surface."""
    features = [{'type': 'Feature', 'properties': dict(f['properties']), 'geometry': f['geometry']}
                for f in catalog.values()]
    overlays, orphans = annotate.resolve(etl.collection(features), items, vocab)
    return {'overlays': overlays, 'orphans': orphans, 'count': len(items),
            'surfaces': {f['properties']['id']: [f['properties']['surface'], f['properties'].get('surface_detail')]
                         for f in features}}


def roads_geometry(segments, privacy, ids):
    """Merge selected GPS sections into one public geometry.

    Same treatment as the public coverage layer: snapped to the coarse grid and cut by this
    ride's exclusion masks (ride-scoped ones too) BEFORE it is written to a committed file.
    No ride id is stored; the item id is a hash of the resulting geometry.
    """
    if privacy is None:
        raise annotate.AnnotationError('Exclusion rules were not found; GPS roads are not saved without them.')
    parts = []
    for sid in ids:
        feature = segments.get(sid)
        if feature is None:
            raise annotate.AnnotationError('Unknown GPS section. Reload the map.')
        ride = str(feature['properties']['parent_id'])
        for line in etl.lines(etl.transform(etl.FW, shape(feature['geometry']))):
            for snapped in etl.lines(shapely.set_precision(line, public.SNAP_M)):
                parts.extend(privacy.clean(snapped, ride, geographic_only=True))
    if not parts:
        return None
    merged = unary_union(parts)
    pieces = etl.lines(linemerge(merged) if merged.geom_type == 'MultiLineString' else merged)
    pieces.sort(key=lambda g: (round(g.bounds[0]), round(g.bounds[1]), round(g.length)))
    return public._geometry(etl.transform(etl.BW, MultiLineString(pieces)), 5)


def drawn_geometry(privacy, points):
    """A hand-drawn line, cut by the same exclusion masks as GPS before it is stored."""
    if privacy is None:
        raise annotate.AnnotationError('Exclusion rules were not found; drawn lines are not saved without them.')
    line = etl.transform(etl.FW, shapely.geometry.LineString([(round(x, 6), round(y, 6)) for x, y in points]))
    parts = privacy.clean(line, '', geographic_only=True)
    if not parts:
        return None
    return public._geometry(etl.transform(etl.BW, MultiLineString(parts)), 6)


def area_geometry(privacy, points):
    """A hand-drawn non-bikeable area, cut by the global exclusion masks before it is stored."""
    if privacy is None:
        raise annotate.AnnotationError('Exclusion rules were not found; drawn areas are not saved without them.')
    polygon = shapely.geometry.Polygon([(round(x, 6), round(y, 6)) for x, y in points])
    if not polygon.is_valid or polygon.area == 0:
        raise annotate.AnnotationError('That area crosses itself. Draw it again.')
    geom = privacy.clean_area(etl.transform(etl.FW, polygon))
    if geom.is_empty or geom.area < 1:
        return None
    return public._geometry(etl.transform(etl.BW, geom), 6)
