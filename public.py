"""Public export: the only data that may leave this computer.

Allowlist, not blocklist. The public file contains the trail catalog, trail-matched
geometry and ONE dissolved coverage layer. It never contains ride IDs, dates, ride
counts, ride order, per-ride geometry, Strava links, note-segment IDs or other snapshots.
"""
import json
import random

import shapely
from pathlib import Path

from shapely.geometry import MultiLineString, mapping, shape
from shapely.ops import linemerge, unary_union

import etl

OVERLAY_FIELDS = ('trail', 'surface', 'passability', 'grade', 'crossings', 'tags', 'comment')
CATALOG_FIELDS = ('id', 'name', 'category', 'status', 'surface', 'surface_detail', 'state', 'states',
                  'km', 'source', 'url', 'ridden_km', 'coverage')
SCHEMA_VERSION = 2
SNAP_M = 8


def _round(value, digits):
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, (list, tuple)):
        return [_round(v, digits) for v in value]
    return value


def _geometry(geom, digits):
    out = mapping(geom)
    return {'type': out['type'], 'coordinates': _round(out['coordinates'], digits)}


def coverage(rides, privacy=None):
    """Dissolve every visible ride into one anonymous layer (no per-ride features)."""
    parts = []
    for ride in rides:
        for line in etl.lines(etl.transform(etl.FW, shape(ride['geometry']))):
            # Snap to a coarse grid so near-identical passes collapse onto the same
            # vertices; otherwise GPS jitter keeps repeated rides as separate lines.
            snapped = etl.lines(shapely.set_precision(line, SNAP_M))
            if privacy is None:
                parts.extend(snapped)
                continue
            # Snapping can move a vertex ~6 m, so re-apply this ride's own masks
            # (ride-scoped rules too) AFTER snapping and before anything is merged.
            for piece in snapped:
                parts.extend(privacy.clean(piece, ride['properties']['id'], geographic_only=True))
    if not parts:
        return None, 0.0
    merged = unary_union(parts)
    pieces = etl.lines(linemerge(merged) if merged.geom_type == 'MultiLineString' else merged)
    if privacy is not None:
        # Defence in depth: geographic masks again, on the dissolved result.
        cleaned = []
        for piece in pieces:
            cleaned.extend(privacy.clean(piece, '', geographic_only=True))
        pieces = cleaned
    # Original order encodes chronology; destroy it deterministically.
    random.Random(0).shuffle(pieces)
    pieces.sort(key=lambda g: (round(g.bounds[0]), round(g.bounds[1]), round(g.length)))
    km = round(sum(p.length for p in pieces) / 1000, 3)
    geom = etl.transform(etl.BW, MultiLineString(pieces))
    return {'type': 'Feature', 'properties': {}, 'geometry': _geometry(geom, 5)}, km


def export_public(data, privacy=None):
    """Build the public document from ONE internal snapshot (see etl.build)."""
    catalog = []
    for f in data['catalog']['features']:
        props = {k: f['properties'][k] for k in CATALOG_FIELDS if k in f['properties']}
        catalog.append({'type': 'Feature', 'properties': props, 'geometry': f['geometry']})
    matched = [{'type': 'Feature', 'properties': {'id': f['properties']['id']}, 'geometry': f['geometry']}
               for f in data['matched']['features']]
    overlays = [{'type': 'Feature', 'properties': {k: f['properties'][k] for k in OVERLAY_FIELDS if k in f['properties']},
                 'geometry': f['geometry']} for f in data.get('overlays', {}).get('features', [])]
    cover, km = coverage(data['rides']['features'], privacy)
    return {'schema_version': SCHEMA_VERSION, 'kind': 'public',
            'catalog': etl.collection(catalog), 'matched': etl.collection(matched),
            'coverage': cover, 'stats': {'coverage_km': km},
            'vocabulary': data.get('vocabulary'), 'overlays': etl.collection(overlays)}


def write_site(public, out_dir, assemble_html):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'data.json').write_text(
        json.dumps(public, ensure_ascii=False, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    (out_dir / 'index.html').write_text(assemble_html(False), encoding='utf-8')
    (out_dir / '.nojekyll').write_text('', encoding='utf-8')
