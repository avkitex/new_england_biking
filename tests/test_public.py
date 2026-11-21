import csv, json, re, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import etl, public
from shapely.geometry import LineString, Point, shape

ROOT = Path(__file__).resolve().parents[1]
RIDE_IDS = ('987654321', '987654322')
FIELDS = 'enabled,kind,ride_id,lat,lon,radius_m,west,south,east,north,start_mile,end_mile'.split(',')


def synthetic(rules=()):
    temp = tempfile.TemporaryDirectory()
    path = Path(temp.name) / 'rules.csv'
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS); w.writeheader(); w.writerows(rules)
    privacy = etl.Privacy(path)
    x, y = etl.FW(-71, 42)
    line = LineString([(x - 3000, y), (x + 3000, y)])
    # Two passes over the same road, 2 m apart (GPS jitter), different dates.
    rides = [etl.make_ride(RIDE_IDS[0], '2020-01-01', line, etl.collection([]), privacy),
             etl.make_ride(RIDE_IDS[1], '2020-02-01', LineString([(x - 3000, y + 2), (x + 3000, y + 2)]), etl.collection([]), privacy)]
    trail = {'type': 'Feature', 'properties': {'id': 1, 'name': 'Test trail', 'category': 'path', 'status': 'existing',
                                               'state': 'MA', 'states': ['MA'], 'surface': 'paved', 'source': 'Src', 'url': 'https://example.org/t'},
             'geometry': etl.geometry([line])}
    assets = {'catalog': etl.collection([trail]), 'states': etl.collection([]), 'catalog_info': {}}
    data = etl.build([r for r in rides if r], '2020-02-01', assets, privacy)
    temp.cleanup()
    return data, privacy


class PublicExportTest(unittest.TestCase):
    def test_no_identifying_fields(self):
        data, privacy = synthetic()
        text = json.dumps(public.export_public(data, privacy))
        for needle in RIDE_IDS + ('2020-01', '2020-02', 'strava', 'segment_id', 'parent_id', '"rides"', '"date"'):
            self.assertNotIn(needle, text)

    def test_rides_are_dissolved_into_one_feature(self):
        data, privacy = synthetic()
        pub = public.export_public(data, privacy)
        self.assertEqual(pub['coverage']['geometry']['type'], 'MultiLineString')
        # Two passes ~6 km each collapse to roughly one 6 km layer, not 12 km.
        self.assertLess(pub['stats']['coverage_km'], 8)
        self.assertEqual(set(pub), {'schema_version', 'kind', 'catalog', 'matched', 'coverage', 'stats', 'vocabulary', 'overlays'})
        self.assertEqual(set(pub['matched']['features'][0]['properties']), {'id'})

    def test_masks_apply_to_public_coverage_and_matches(self):
        rules = [dict(kind='circle', lat=42, lon=-71, radius_m=300)]
        data, privacy = synthetic(rules)
        pub = public.export_public(data, privacy)
        x, y = etl.FW(-71, 42); mask = Point(x, y).buffer(300)
        for g in [pub['coverage']['geometry']] + [f['geometry'] for f in pub['matched']['features']]:
            self.assertFalse(etl.transform(etl.FW, shape(g)).intersects(mask))

    def test_ride_scoped_mask_survives_grid_snapping(self):
        # Rule applies to one ride only; the 8 m snap must not move its track back into the mask.
        for rule_ride in RIDE_IDS:
            rules = [dict(kind='circle', ride_id=rule_ride, lat=42, lon=-71, radius_m=300)]
            data, privacy = synthetic(rules)
            pub = public.export_public(data, privacy)
            x, y = etl.FW(-71, 42); mask = Point(x, y).buffer(300)
            # The other ride still passes through the area, so only the owner's cut is tested:
            # check that no ride-scoped mask is violated by recomputing per ride.
            for ride in data['rides']['features']:
                if ride['properties']['id'] == rule_ride:
                    self.assertFalse(etl.transform(etl.FW, shape(ride['geometry'])).intersects(mask))
            snapped_only = public.coverage([r for r in data['rides']['features'] if r['properties']['id'] == rule_ride], privacy)[0]
            self.assertFalse(etl.transform(etl.FW, shape(snapped_only['geometry'])).intersects(mask))

    def test_pages_have_no_absolute_paths_or_private_features(self):
        pub_html, ed_html = etl.assemble(False), etl.assemble(True)
        for needle in ("fetch('/", 'fetch("/', '/api/', 'strava.com', 'private-notes', 'Private segment notes', 'data/versions.json'):
            self.assertNotIn(needle, pub_html)
        self.assertIn('/api/annotations', ed_html); self.assertNotIn('/api/annotations', pub_html)
        self.assertNotIn('<!--#', pub_html); self.assertNotIn('<!--#', ed_html)
        self.assertNotIn('/*SCRIPT*/', pub_html)
        for needle in ('id="exPanel"', 'ExcludeTool', '/api/exclusions', '/api/rebuild', 'id="editPanel"'):
            self.assertNotIn(needle, pub_html)
        self.assertIn('/api/exclusions', ed_html)


@unittest.skipUnless((ROOT / 'site/data.json').exists() and (ROOT / 'data/versions.json').exists(), 'no built site')
class BuiltSiteTest(unittest.TestCase):
    """Runs against the real build output when present (python etl.py first)."""
    def test_real_site_leaks_nothing(self):
        text = (ROOT / 'site/data.json').read_text(encoding='utf-8')
        manifest = json.loads((ROOT / 'data/versions.json').read_text(encoding='utf-8'))
        latest = json.loads((ROOT / [v['url'] for v in manifest['versions'] if v['date'] == manifest['latest']][0]).read_text(encoding='utf-8'))
        for f in latest['rides']['features']:
            self.assertNotIn('"' + f['properties']['id'] + '"', text)
            self.assertNotIn(f['properties']['id'], text)
        self.assertNotIn('strava', text.lower())
        self.assertEqual(re.findall(r'20\d\d-\d\d-\d\d', text), [])
        self.assertLess(len(text), 8_000_000)
        for page in ('index.html',):
            html = (ROOT / 'site' / page).read_text(encoding='utf-8')
            self.assertNotIn('strava.com', html)


if __name__ == '__main__':
    unittest.main()
