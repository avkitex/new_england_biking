import json, sys, tempfile, threading, unittest, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import etl, cuts_store as ex, server
from shapely.geometry import LineString, Point

HEADER = 'enabled,kind,ride_id,lat,lon,radius_m,west,south,east,north,start_mile,end_mile'
X, Y = etl.FW(-71, 42)


def csvfile(d, text=HEADER + chr(10)):
    p = Path(d) / 'exclusions.csv'; p.write_text(text); return p


class StoreTest(unittest.TestCase):
    def test_polygon_circle_box_cut_and_header_upgrade(self):
        with tempfile.TemporaryDirectory() as d:
            p = csvfile(d)
            ex.add_rule(p, {'kind': 'circle', 'lat': 42.0, 'lon': -71.0, 'radius_m': 100}, d)
            n = ex.add_rule(p, {'kind': 'polygon', 'points': [[-70.99, 41.99], [-70.98, 41.99], [-70.98, 42.0]]}, Path(d) / 'bk')
            self.assertEqual(n, 2); self.assertIn('polygon', p.read_text().splitlines()[0])
            self.assertTrue((Path(d) / 'bk/exclusions.backup.csv').exists())
            priv = etl.Privacy(p)
            line = LineString([(X - 500, Y), (X + 500, Y)])
            self.assertTrue(all(not l.intersects(Point(X, Y).buffer(100)) for l in priv.clean(line, '1')))
            ex.add_rule(p, {'kind': 'box', 'west': -71.01, 'south': 41.99, 'east': -71.005, 'north': 42.01}, d)
            self.assertEqual(len(etl.Privacy(p).rules), 3)

    def test_polygon_mask_removes_line(self):
        with tempfile.TemporaryDirectory() as d:
            p = csvfile(d); lon0, lat0 = etl.BW(X, Y)
            ex.add_rule(p, {'kind': 'polygon', 'points': [[lon0 - .001, lat0 - .001], [lon0 + .001, lat0 - .001], [lon0 + .001, lat0 + .001], [lon0 - .001, lat0 + .001]]})
            parts = etl.Privacy(p).clean(LineString([(X - 500, Y), (X + 500, Y)]), '1')
            self.assertEqual(len(parts), 2)

    def test_invalid_rules_rejected_and_file_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            p = csvfile(d); before = p.read_text()
            for bad in [{'kind': 'mystery'}, {'kind': 'circle', 'lat': 0, 'lon': 0, 'radius_m': 100}, {'kind': 'circle', 'lat': 42, 'lon': -71, 'radius_m': 1},
                        {'kind': 'polygon', 'points': [[-71, 42], [-71, 42], [-71, 42]]}, {'kind': 'polygon', 'points': [[-71, 42]]},
                        {'kind': 'polygon', 'points': [[-71, 42], [-70.9, 42.1], [-71, 42.1], [-70.9, 42]]},  # self-intersecting
                        {'kind': 'box', 'west': -70, 'south': 42, 'east': -71, 'north': 43}, 'x', {'kind': 'circle', 'lat': True, 'lon': -71, 'radius_m': 50}]:
                with self.assertRaises(ex.ExclusionError):
                    ex.add_rule(p, bad)
            self.assertEqual(p.read_text(), before)
            self.assertEqual(list(Path(d).glob('*.tmp')), [])


class ApiTest(unittest.TestCase):
    def test_endpoint_token_and_no_echo(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'exclusions.csv').write_text(HEADER + chr(10))
            (root / 'settings.json').write_text(json.dumps({'exclusions': 'exclusions.csv', 'snapshots': []}))
            old = server.PRIVATE; server.PRIVATE = root
            http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler); threading.Thread(target=http.serve_forever, daemon=True).start()
            url = f'http://127.0.0.1:{http.server_port}/api/exclusions'
            def post(body, token):
                r = urllib.request.Request(url, data=json.dumps(body).encode(), headers={'X-Notes-Token': token})
                try:
                    with urllib.request.urlopen(r) as resp: return resp.status, resp.read().decode()
                except urllib.error.HTTPError as e: return e.code, e.read().decode()
            try:
                rule = {'kind': 'circle', 'lat': 42.123456, 'lon': -71.654321, 'radius_m': 200}
                self.assertEqual(post(rule, 'bad')[0], 403)
                self.assertEqual(post(rule, server.TOKEN)[0], 200)
                self.assertIn('42.123456', (root / 'exclusions.csv').read_text())
                code, text = post({**rule, 'radius_m': 1}, server.TOKEN)
                self.assertEqual(code, 400); self.assertNotIn('42.123456', text)
            finally:
                http.shutdown(); http.server_close(); server.PRIVATE = old


if __name__ == '__main__':
    unittest.main()
