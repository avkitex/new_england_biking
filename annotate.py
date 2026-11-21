"""Trail annotations: a fixed vocabulary applied to trails (or parts of trails) by file.

`annotations/vocabulary.json` lists every allowed value (surface, passability, grade,
road crossings, tags). `annotations/annotations.json` holds the edits. Both are public
and committed. An item targets one of:

  {"trail": <catalog id>, "name": "<trail name>"}                          whole trail
  {"trail": <catalog id>, "name": "...", "from_m": 0, "to_m": 800}         part of a trail
  {"geometry": {GeoJSON LineString | MultiLineString}}                     any other line

`from_m`/`to_m` are metres along the trail (EPSG:26919, components in file order).
Fields in `set` override per field; later items win. Tags are unioned; comments are joined.
"""
import json
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import substring

import etl

ROOT = Path(__file__).resolve().parent
FIELD_KEYS = ('surface', 'passability', 'grade', 'crossings')
MIN_PIECE_M = 1.0
MAX_COMMENT = 1000


class AnnotationError(ValueError):
    pass


def load_vocabulary(path=None):
    vocab = etl.read(path or ROOT / 'annotations/vocabulary.json')
    if vocab.get('version') != 1 or set(vocab['fields']) != set(FIELD_KEYS):
        raise AnnotationError('Unsupported vocabulary file.')
    return vocab


def load_items(vocab, path=None):
    path = Path(path or ROOT / 'annotations/annotations.json')
    if not path.exists():
        return []
    data = etl.read(path)
    if data.get('version') != 1 or not isinstance(data.get('items'), list):
        raise AnnotationError('Unsupported annotations file.')
    validate_items(vocab, data['items'])
    return data['items']


def validate_items(vocab, items):
    allowed = {k: {v['id'] for v in vocab['fields'][k]['values']} for k in FIELD_KEYS}
    tags = {t['id'] for t in vocab['tags']}
    seen = set()
    for number, item in enumerate(items, 1):
        label = f"Annotation #{number}"
        if not isinstance(item, dict) or set(item) - {'id', 'target', 'set', 'tags', 'comment'}:
            raise AnnotationError(f'{label}: unexpected structure.')
        if not isinstance(item.get('id'), str) or not item['id'] or item['id'] in seen:
            raise AnnotationError(f'{label}: missing or duplicate id.')
        seen.add(item['id'])
        target = item.get('target')
        if not isinstance(target, dict):
            raise AnnotationError(f'{label}: missing target.')
        if 'trail' in target:
            if set(target) - {'trail', 'name', 'from_m', 'to_m'} or not isinstance(target['trail'], int):
                raise AnnotationError(f'{label}: invalid trail target.')
            a, b = target.get('from_m', 0), target.get('to_m')
            if not isinstance(a, (int, float)) or (b is not None and (not isinstance(b, (int, float)) or b <= a)) or a < 0:
                raise AnnotationError(f'{label}: invalid range.')
        elif set(target) == {'geometry'} and target['geometry'].get('type') in ('LineString', 'MultiLineString'):
            shape(target['geometry'])
        elif set(target) == {'area'} and isinstance(target['area'], dict) and target['area'].get('type') in ('Polygon', 'MultiPolygon'):
            shape(target['area'])
        else:
            raise AnnotationError(f'{label}: invalid target.')
        for key, value in (item.get('set') or {}).items():
            if key not in allowed or value not in allowed[key]:
                raise AnnotationError(f'{label}: value not in vocabulary.')
        if any(t not in tags for t in item.get('tags', [])):
            raise AnnotationError(f'{label}: unknown tag.')
        comment = item.get('comment', '')
        if not isinstance(comment, str) or len(comment) > MAX_COMMENT:
            raise AnnotationError(f'{label}: comment too long.')


def _effective(items):
    props = {}
    tags, comments = [], []
    for item in items:
        props.update(item.get('set') or {})
        tags.extend(t for t in item.get('tags', []) if t not in tags)
        comment = item.get('comment', '').strip()
        if comment and comment not in comments:
            comments.append(comment)
    out = {k: props[k] for k in FIELD_KEYS if k in props}
    if tags:
        out['tags'] = tags
    if comments:
        out['comment'] = ' · '.join(comments)
    return out


def _emit(part, props, trail, privacy, result):
    if privacy is not None:
        pieces = privacy.clean(part, '', geographic_only=True)
    else:
        pieces = etl.lines(part)
    if pieces:
        result.append({'type': 'Feature', 'properties': {**props, 'trail': trail},
                       'geometry': etl.geometry(pieces)})


def resolve(catalog, items, vocab, privacy=None):
    """Return (overlays FeatureCollection, orphan count). Mutates catalog surface for whole-trail items."""
    classes = {v['id']: v['class'] for v in vocab['fields']['surface']['values']}
    by_trail, geometry_items, area_items, orphans = {}, [], [], 0
    features = {f['properties']['id']: f for f in catalog['features']}
    for item in items:
        target = item['target']
        if 'geometry' in target:
            geometry_items.append(item)
            continue
        if 'area' in target:
            area_items.append(item)
            continue
        feature = features.get(target['trail'])
        if feature is None or ('name' in target and feature['properties']['name'] != target['name']):
            orphans += 1  # catalog changed under this annotation; never reassigned silently
            continue
        by_trail.setdefault(target['trail'], []).append(item)
    result = []
    for trail_id, group in by_trail.items():
        feature = features[trail_id]
        components = etl.lines(etl.transform(etl.FW, shape(feature['geometry'])))
        total = sum(c.length for c in components)
        ranges = [(i, i['target'].get('from_m', 0), i['target'].get('to_m', total)) for i in group]
        offset = 0.0
        for line in components:
            cuts = {0.0, line.length}
            for _, a, b in ranges:
                for v in (a - offset, b - offset):
                    if 0 < v < line.length:
                        cuts.add(v)
            cuts = sorted(cuts)
            merged = []  # (start, end, props)
            for s, e in zip(cuts, cuts[1:]):
                if e - s < MIN_PIECE_M:
                    continue
                mid = offset + (s + e) / 2
                props = _effective([i for i, a, b in ranges if a <= mid <= b])
                if not props:
                    continue
                if merged and merged[-1][2] == props and abs(merged[-1][1] - s) < 1e-6:
                    merged[-1] = (merged[-1][0], e, props)
                else:
                    merged.append((s, e, props))
            for s, e, props in merged:
                _emit(substring(line, s, e), props, trail_id, privacy, result)
            offset += line.length
        for item, a, b in ranges:  # a whole-trail surface edit also updates the trail itself
            if 'surface' in (item.get('set') or {}) and a <= 0 and b >= total - 0.5:
                feature['properties']['surface'] = classes[item['set']['surface']]
                feature['properties']['surface_detail'] = item['set']['surface']
    for item in geometry_items:
        geom = etl.transform(etl.FW, shape(item['target']['geometry']))
        _emit(geom, {**_effective([item]), 'item': item['id']}, None, privacy, result)
    import shapely
    for item in area_items:
        geom = etl.transform(etl.FW, shape(item['target']['area']))
        if privacy is not None:
            geom = privacy.clean_area(geom)
        if geom.is_empty or geom.area < 1:
            continue
        out = etl.transform(etl.BW, geom)
        mapped = shapely.geometry.mapping(out)
        coords = json.loads(json.dumps(mapped['coordinates']))

        def rnd(v):
            return [rnd(x) for x in v] if isinstance(v, list) else round(v, 6)
        result.append({'type': 'Feature', 'properties': {**_effective([item]), 'item': item['id'], 'trail': None},
                       'geometry': {'type': mapped['type'], 'coordinates': rnd(coords)}})
    return etl.collection(result), orphans
