"""Append exclusion rules to the PRIVATE exclusions.csv from the local editor.

The file never leaves the private folder and rule values are never echoed in errors.
Columns: enabled,kind,ride_id,lat,lon,radius_m,west,south,east,north,start_mile,end_mile,polygon
(`polygon` = "lon lat;lon lat;..." in WGS84; older files without that column are upgraded.)
"""
import csv
import os
import tempfile
from pathlib import Path

import etl

BASE = 'enabled,kind,ride_id,lat,lon,radius_m,west,south,east,north,start_mile,end_mile'.split(',')
MAX_POINTS = 500


class ExclusionError(ValueError):
    pass


def _num(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ExclusionError('Invalid shape.')
    return round(float(value), 6)


def row_from(rule):
    if not isinstance(rule, dict):
        raise ExclusionError('Invalid shape.')
    kind = rule.get('kind')
    row = {'enabled': '1', 'kind': kind}
    if kind == 'circle':
        row.update(lat=_num(rule.get('lat'), 40, 48), lon=_num(rule.get('lon'), -74, -66), radius_m=_num(rule.get('radius_m'), 5, 20000))
    elif kind == 'box':
        w, s, e, n = (_num(rule.get(k), lo, hi) for k, lo, hi in (('west', -74, -66), ('south', 40, 48), ('east', -74, -66), ('north', 40, 48)))
        row.update(west=w, south=s, east=e, north=n)
    elif kind == 'polygon':
        pts = rule.get('points')
        if not isinstance(pts, list) or not 3 <= len(pts) <= MAX_POINTS:
            raise ExclusionError('Invalid shape.')
        row['polygon'] = ';'.join(f"{_num(p[0], -74, -66)} {_num(p[1], 40, 48)}" for p in pts if isinstance(p, list) and len(p) == 2)
        if row['polygon'].count(';') + 1 != len(pts):
            raise ExclusionError('Invalid shape.')
    else:
        raise ExclusionError('Unsupported rule kind.')
    return row


def add_rule(path, rule, backup_dir=None):
    """Validate by loading the new file as real rules before replacing the old one (fail closed)."""
    path = Path(path)
    row = row_from(rule)
    rows, fields = [], list(BASE)
    if path.exists():
        with path.open(encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            fields = list(reader.fieldnames or BASE)
            rows = list(reader)
    if 'polygon' not in fields:
        fields.append('polygon')
    rows.append(row)
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='', dir=path.parent, delete=False, suffix='.tmp') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
        temp = Path(f.name)
    try:
        etl.Privacy(temp)
    except ValueError:
        temp.unlink()
        raise ExclusionError('That shape is not valid.') from None
    if path.exists() and backup_dir:
        Path(backup_dir).mkdir(parents=True, exist_ok=True)
        (Path(backup_dir) / 'exclusions.backup.csv').write_bytes(path.read_bytes())
    os.replace(temp, path)
    return len(rows)
