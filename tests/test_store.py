import json, sys, tempfile, threading, unittest, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import etl, annotate, annotation_store as store, server
from shapely.geometry import LineString, Point

VOCAB = annotate.load_vocabulary()
X, Y = etl.FW(-71, 42)


def feature(tid, name, length=3000, dy=0):
    line = LineString([(X, Y + dy), (X + length, Y + dy)])
    return {'type': 'Feature', 'properties': {'id': tid, 'name': name, 'surface': 'unknown', 'category': 'path', 'status': 'existing',
            'state': 'MA', 'states': ['MA'], 'km': 3, 'source': 's', 'url': 'u', 'ridden_km': 0, 'coverage': 0}, 'geometry': etl.geometry([line])}


CAT = {7: feature(7, 'A'), 8: feature(8, 'B', dy=500)}


def point(m, dy=0):
    lon, lat = etl.BW(X + m, Y + dy)
    return [lon, lat]


class StoreTest(unittest.TestCase):
    def edit(self, items, **kw):
        kw.setdefault('targets', [{'trail': 7}])
        return store.apply_edit(items, CAT, VOCAB, kw)

    def test_along_is_metric(self):
        self.assertAlmostEqual(store.along(CAT[7], *point(1200, 20)), 1200, delta=2)

    def test_whole_then_range_then_merge_back(self):
        items = self.edit([], set={'surface': 'dirt'})
        self.assertEqual(len(items), 1); self.assertNotIn('from_m', items[0]['target'])
        items = self.edit(items, targets=[{'trail': 7, 'a': point(1000), 'b': point(2000)}], set={'passability': 'hard'}, tags_add=['closure'], comment='Roots')
        self.assertEqual(len(items), 3)
        mid = [i for i in items if i['target'].get('from_m', 0) > 0 and i['target'].get('to_m', 1e9) < 2999][0]
        self.assertEqual((mid['set'], mid['tags'], mid['comment']), ({'surface': 'dirt', 'passability': 'hard'}, ['closure'], 'Roots'))
        # Undo everything in the middle -> pieces merge back into one whole-trail item.
        items = self.edit(items, targets=[{'trail': 7, 'a': point(1000), 'b': point(2000)}], set={'passability': None}, tags_remove=['closure'], comment='')
        self.assertEqual(len(items), 1); self.assertEqual(items[0]['set'], {'surface': 'dirt'})

    def test_clear_removes_item_and_unrelated_items_survive(self):
        geo = {'id': 'n-1', 'target': {'geometry': {'type': 'LineString', 'coordinates': [[-71, 42], [-71.01, 42]]}}, 'comment': 'note'}
        orphan = {'id': 'o', 'target': {'trail': 7, 'name': 'Old name'}, 'set': {'grade': 'flat'}}
        items = self.edit([geo, orphan], set={'grade': 'steep'})
        self.assertIn(geo, items); self.assertIn(orphan, items)
        items = self.edit(items, set={'grade': None})
        self.assertEqual([i['id'] for i in items], ['o', 'n-1'])

    def test_multi_trail_bulk_and_validation(self):
        items = self.edit([], targets=[{'trail': 7}, {'trail': 8}], set={'surface': 'asphalt'})
        self.assertEqual({i['target']['trail'] for i in items}, {7, 8})
        for bad in [dict(set={'surface': 'lava'}), dict(tags_add=['nope']), dict(targets=[{'trail': 99}]), dict(targets=[]),
                    dict(targets=[{'trail': 7, 'a': point(5)}]), dict(targets=[{'trail': 7, 'a': point(5), 'b': point(5.5)}]), dict(comment='x' * 5000)]:
            with self.assertRaises(annotate.AnnotationError):
                self.edit([], **{'set': {'grade': 'flat'}, **bad})

    def test_resolve_reads_back_what_was_written(self):
        items = self.edit([], targets=[{'trail': 7, 'a': point(500), 'b': point(1500)}], set={'crossings': 'many'})
        st = store.state(items, CAT, VOCAB)
        self.assertEqual(len(st['overlays']['features']), 1)
        self.assertEqual(st['surfaces'][7], ['unknown', None])


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); root = Path(self.temp.name)
        self.old = (server.ANNOTATIONS, server.CATALOG, server.PRIVATE)
        (root / 'cat.json').write_text(json.dumps({'catalog': etl.collection(list(CAT.values()))}))
        server.ANNOTATIONS, server.CATALOG, server.PRIVATE = root / 'a.json', root / 'cat.json', root / 'priv'
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.http.server_port}'

    def tearDown(self):
        self.http.shutdown(); self.http.server_close(); server.ANNOTATIONS, server.CATALOG, server.PRIVATE = self.old; self.temp.cleanup()

    def req(self, payload=None, token=None):
        headers = {'X-Notes-Token': token} if token is not None else {}
        r = urllib.request.Request(self.url + '/api/annotations', data=json.dumps(payload).encode() if payload else None, headers=headers)
        try:
            with urllib.request.urlopen(r) as resp: return resp.status, json.load(resp)
        except urllib.error.HTTPError as e: return e.code, json.load(e)

    def test_save_conflict_and_token(self):
        code, d = self.req(); self.assertEqual(code, 200); self.assertEqual(d['count'], 0)
        edit = {'targets': [{'trail': 7}], 'set': {'surface': 'fine_gravel'}}
        self.assertEqual(self.req({'revision': d['revision'], 'edit': edit}, 'bad')[0], 403)
        code, saved = self.req({'revision': d['revision'], 'edit': edit}, d['token']); self.assertEqual(code, 200)
        self.assertEqual(saved['surfaces']['7'], ['unpaved', 'fine_gravel'])
        self.assertEqual(self.req({'revision': d['revision'], 'edit': edit}, d['token'])[0], 409)   # stale revision
        self.assertEqual(self.req({'revision': saved['revision'], 'edit': {'targets': [{'trail': 7}], 'set': {'surface': 'lava'}}}, d['token'])[0], 400)
        self.assertEqual(json.loads(server.ANNOTATIONS.read_text())['items'][0]['set'], {'surface': 'fine_gravel'})
        self.assertTrue((server.PRIVATE / 'annotations.backup.json').exists() is False)  # first write: nothing to back up



SECRET_RIDE = '555123456789'


def seg(sid, x0, x1, dy=0, ride=SECRET_RIDE):
    a, b = etl.BW(X + x0, Y + dy), etl.BW(X + x1, Y + dy)
    return {'type': 'Feature', 'properties': {'segment_id': sid, 'kind': 'ride', 'parent_id': ride, 'name': 'Cycling ride', 'date': '2020-02-01'},
            'geometry': {'type': 'LineString', 'coordinates': [list(a), list(b)]}}


def privacy_with(rows):
    import csv
    d = tempfile.mkdtemp(); path = Path(d) / 'x.csv'
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames='enabled,kind,ride_id,lat,lon,radius_m,west,south,east,north,start_mile,end_mile'.split(',')); w.writeheader(); w.writerows(rows)
    return etl.Privacy(path)


class RoadsTest(unittest.TestCase):
    SEGS = {'s1': seg('s1', 0, 800), 's2': seg('s2', 800, 1600), 's3': seg('s3', 0, 800, dy=2)}

    def test_geometry_has_no_ride_data_and_is_cut_by_ride_scoped_mask(self):
        from shapely.geometry import shape as shp
        lon, lat = etl.BW(X + 400, Y)
        priv = privacy_with([dict(kind='circle', ride_id=SECRET_RIDE, lat=lat, lon=lon, radius_m=150)])
        g = store.roads_geometry(self.SEGS, priv, ['s1', 's2'])
        self.assertNotIn(SECRET_RIDE, json.dumps(g)); self.assertNotIn('2026', json.dumps(g))
        self.assertFalse(etl.transform(etl.FW, shp(g)).intersects(Point(X + 400, Y).buffer(150)))
        self.assertTrue(etl.transform(etl.FW, shp(g)).length > 800)
        self.assertIsNone(store.roads_geometry({'s1': seg('s1', 300, 500)}, priv, ['s1']))   # entirely inside the mask
        with self.assertRaises(annotate.AnnotationError): store.roads_geometry(self.SEGS, None, ['s1'])
        with self.assertRaises(annotate.AnnotationError): store.roads_geometry(self.SEGS, priv, ['nope'])

    def test_apply_roads_then_item_edits_and_clear(self):
        priv = privacy_with([])
        roads = lambda ids: store.roads_geometry(self.SEGS, priv, ids)
        run = lambda items, **kw: store.apply_edit(items, CAT, VOCAB, kw, roads)
        items = run([], targets=[{'roads': ['s1', 's2']}], set={'surface': 'dirt'}, comment='Muddy')
        self.assertEqual(len(items), 1); rid = items[0]['id']; self.assertTrue(rid.startswith('r-'))
        self.assertNotIn(SECRET_RIDE, json.dumps(items))
        again = run(items, targets=[{'roads': ['s1', 's2']}], set={'grade': 'flat'})            # identical selection updates in place
        self.assertEqual((len(again), again[0]['id'], again[0]['set']), (1, rid, {'surface': 'dirt', 'grade': 'flat'}))
        edited = run(again, targets=[{'item': rid}], tags_add=['parking'], set={'surface': None})
        self.assertEqual((edited[0]['set'], edited[0]['tags'], edited[0]['comment']), ({'grade': 'flat'}, ['parking'], 'Muddy'))
        cleared = run(edited, targets=[{'item': rid}], set={'grade': None}, tags_remove=['parking'], comment='')
        self.assertEqual(cleared, [])
        with self.assertRaises(annotate.AnnotationError): run([], targets=[{'item': 'r-missing'}], set={'grade': 'flat'})
        with self.assertRaises(annotate.AnnotationError): store.apply_edit([], CAT, VOCAB, dict(targets=[{'roads': ['s1']}], set={'grade': 'flat'}))

    def test_public_export_hides_item_ids(self):
        import public
        priv = privacy_with([])
        items = store.apply_edit([], CAT, VOCAB, dict(targets=[{'roads': ['s1']}], set={'grade': 'steep'}), lambda ids: store.roads_geometry(self.SEGS, priv, ids))
        st = store.state(items, CAT, VOCAB)
        self.assertIn(items[0]['id'], json.dumps(st['overlays']))     # the editor sees it
        data = {'catalog': etl.collection([]), 'matched': etl.collection([]), 'rides': etl.collection([]), 'overlays': st['overlays'], 'vocabulary': VOCAB}
        self.assertNotIn(items[0]['id'], json.dumps(public.export_public(data)))


class RoadsApiTest(unittest.TestCase):
    def test_server_end_to_end_with_exclusions(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'data').mkdir(); priv = root / 'priv'; priv.mkdir()
            lon, lat = etl.BW(X + 400, Y)
            header = 'enabled,kind,ride_id,lat,lon,radius_m,west,south,east,north,start_mile,end_mile'
            (priv / 'exclusions.csv').write_text(header + chr(10) + f'1,circle,,{lat},{lon},150,,,,,,' + chr(10))
            (priv / 'settings.json').write_text(json.dumps({'exclusions': 'exclusions.csv', 'snapshots': []}))
            (root / 'cat.json').write_text(json.dumps({'catalog': etl.collection(list(CAT.values()))}))
            (root / 'data/versions.json').write_text(json.dumps({'versions': [{'date': 'x', 'url': 'data/x.json'}]}))
            (root / 'data/x.json').write_text(json.dumps({'note_segments': etl.collection([seg('s1', 0, 1600)])}))
            old = (server.ROOT, server.ANNOTATIONS, server.CATALOG, server.PRIVATE)
            server.ROOT, server.ANNOTATIONS, server.CATALOG, server.PRIVATE = root, root / 'a.json', root / 'cat.json', priv
            http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler); threading.Thread(target=http.serve_forever, daemon=True).start()
            try:
                url = f'http://127.0.0.1:{http.server_port}/api/annotations'
                with urllib.request.urlopen(url) as r: d0 = json.load(r)
                body = json.dumps({'revision': d0['revision'], 'edit': {'targets': [{'roads': ['s1']}], 'set': {'surface': 'dirt'}}}).encode()
                req = urllib.request.Request(url, data=body, headers={'X-Notes-Token': d0['token']})
                with urllib.request.urlopen(req) as r: saved = json.load(r)
                text = (root / 'a.json').read_text()
                self.assertEqual(saved['count'], 1); self.assertNotIn(SECRET_RIDE, text)
                from shapely.geometry import shape as shp
                geo = json.loads(text)['items'][0]['target']['geometry']
                self.assertFalse(etl.transform(etl.FW, shp(geo)).intersects(Point(X + 400, Y).buffer(150)))
            finally:
                http.shutdown(); http.server_close(); server.ROOT, server.ANNOTATIONS, server.CATALOG, server.PRIVATE = old



class DrawTest(unittest.TestCase):
    def test_drawn_blocked_line_is_cut_and_edited_like_a_road(self):
        from shapely.geometry import shape as shp
        lon, lat = etl.BW(X + 400, Y)
        priv = privacy_with([dict(kind='circle', lat=lat, lon=lon, radius_m=100)])
        pts = [list(etl.BW(X, Y)), list(etl.BW(X + 800, Y))]
        draw = lambda p: store.drawn_geometry(priv, p)
        items = store.apply_edit([], CAT, VOCAB, dict(targets=[{'draw': pts}], set={'passability': 'no'}), draw=draw)
        self.assertEqual((len(items), items[0]['set']), (1, {'passability': 'no'})); self.assertTrue(items[0]['id'].startswith('r-'))
        g = items[0]['target']['geometry']
        self.assertFalse(etl.transform(etl.FW, shp(g)).intersects(Point(X + 400, Y).buffer(99)))
        again = store.apply_edit(items, CAT, VOCAB, dict(targets=[{'item': items[0]['id']}], set={'passability': None}))
        self.assertEqual(again, [])
        for bad in [[pts[0]], [[0, 0], [1, 1]], 'x', [[-71, 42, 1], [-71, 42]]]:
            with self.assertRaises(annotate.AnnotationError):
                store.apply_edit([], CAT, VOCAB, dict(targets=[{'draw': bad}], set={'passability': 'no'}), draw=draw)
        with self.assertRaises(annotate.AnnotationError):
            store.drawn_geometry(None, pts)



class AreaTest(unittest.TestCase):
    def test_area_is_cut_stored_without_ids_and_deletable(self):
        from shapely.geometry import shape as shp
        lon, lat = etl.BW(X + 400, Y)
        priv = privacy_with([dict(kind='circle', lat=lat, lon=lon, radius_m=100)])
        pts = [list(etl.BW(X, Y - 200)), list(etl.BW(X + 800, Y - 200)), list(etl.BW(X + 800, Y + 200)), list(etl.BW(X, Y + 200))]
        area = lambda p: store.area_geometry(priv, p)
        items = store.apply_edit([], CAT, VOCAB, dict(targets=[{'area': pts}], set={'passability': 'no'}, comment='Marsh'), area=area)
        self.assertEqual(len(items), 1); self.assertIn('area', items[0]['target']); self.assertTrue(items[0]['id'].startswith('r-'))
        g = items[0]['target']['area']
        self.assertFalse(etl.transform(etl.FW, shp(g)).intersects(Point(X + 400, Y).buffer(99)))
        ov, _ = annotate.resolve(etl.collection([]), items, VOCAB, priv)
        self.assertEqual((ov['features'][0]['geometry']['type'], ov['features'][0]['properties']['passability']), (g['type'], 'no'))
        gone = store.apply_edit(items, CAT, VOCAB, dict(targets=[{'item': items[0]['id']}], set={'passability': None}, comment=''))
        self.assertEqual(gone, [])
        bow = [[-71, 42], [-70.9, 42.1], [-71, 42.1], [-70.9, 42]]
        for bad in [bow, pts[:2]]:
            with self.assertRaises(annotate.AnnotationError):
                store.apply_edit([], CAT, VOCAB, dict(targets=[{'area': bad}], set={'passability': 'no'}), area=area)
        with self.assertRaises(annotate.AnnotationError):
            store.area_geometry(None, pts)


if __name__ == '__main__':
    unittest.main()
