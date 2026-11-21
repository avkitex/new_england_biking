import csv, json, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import etl, annotate, public
from shapely.geometry import LineString, Point, shape

VOCAB = annotate.load_vocabulary()


def trail(line, tid=7, name='T'):
    return {'type': 'Feature', 'properties': {'id': tid, 'name': name, 'surface': 'unknown', 'category': 'path', 'status': 'existing',
            'state': 'MA', 'states': ['MA'], 'km': 3, 'source': 's', 'url': 'u', 'ridden_km': 0, 'coverage': 0},
            'geometry': etl.geometry([line])}


def make(items):
    x, y = etl.FW(-71, 42)
    line = LineString([(x, y), (x + 3000, y)])
    cat = etl.collection([trail(line)])
    return cat, line, (x, y)


def item(i, target, **kw):
    return {'id': i, 'target': target, **kw}


class ResolveTest(unittest.TestCase):
    def test_whole_trail_and_part_override_per_field(self):
        cat, _, _ = make(None)
        items = [item('a', {'trail': 7, 'name': 'T'}, **{'set': {'surface': 'fine_gravel', 'passability': 'easy'}}),
                 item('b', {'trail': 7, 'name': 'T', 'from_m': 1000, 'to_m': 2000}, **{'set': {'passability': 'hard'}, 'tags': ['closure'], 'comment': 'Roots'})]
        overlays, orphans = annotate.resolve(cat, items, VOCAB)
        self.assertEqual(orphans, 0)
        props = [f['properties'] for f in overlays['features']]
        self.assertEqual(len(props), 3)  # before, middle, after
        self.assertEqual([p['passability'] for p in props], ['easy', 'hard', 'easy'])
        self.assertTrue(all(p['surface'] == 'fine_gravel' for p in props))   # untouched field survives
        self.assertEqual(props[1]['tags'], ['closure']); self.assertEqual(props[1]['comment'], 'Roots')
        # whole-trail surface edit also corrects the trail itself (class, plus detail)
        p = cat['features'][0]['properties']
        self.assertEqual((p['surface'], p['surface_detail']), ('unpaved', 'fine_gravel'))

    def test_identical_neighbours_merge(self):
        cat, _, _ = make(None)
        items = [item('a', {'trail': 7, 'from_m': 0, 'to_m': 1500}, **{'set': {'grade': 'flat'}}),
                 item('b', {'trail': 7, 'from_m': 1500, 'to_m': 3000}, **{'set': {'grade': 'flat'}})]
        overlays, _ = annotate.resolve(cat, items, VOCAB)
        self.assertEqual(len(overlays['features']), 1)

    def test_catalog_drift_is_orphaned_not_reassigned(self):
        cat, _, _ = make(None)
        overlays, orphans = annotate.resolve(cat, [item('a', {'trail': 7, 'name': 'Renamed'}, **{'set': {'grade': 'flat'}}),
                                                   item('b', {'trail': 99}, **{'set': {'grade': 'flat'}})], VOCAB)
        self.assertEqual((len(overlays['features']), orphans), (0, 2))

    def test_geometry_target_and_masks(self):
        cat, line, (x, y) = make(None)
        class Mask:  # stands in for etl.Privacy
            def clean(self, g, rid, geographic_only=False):
                return etl.lines(g.difference(Point(x + 1500, y).buffer(300)))
        overlays, _ = annotate.resolve(cat, [item('a', {'trail': 7}, **{'set': {'grade': 'steep'}})], VOCAB, Mask())
        for f in overlays['features']:
            self.assertFalse(etl.transform(etl.FW, shape(f['geometry'])).intersects(Point(x + 1500, y).buffer(299)))
        geo = {'type': 'LineString', 'coordinates': [[-71, 42], [-71.01, 42]]}
        o2, _ = annotate.resolve(cat, [item('g', {'geometry': geo}, **{'set': {'surface': 'dirt'}})], VOCAB)
        self.assertEqual(o2['features'][0]['properties'], {'surface': 'dirt', 'item': 'g', 'trail': None})

    def test_public_export_carries_only_allowed_overlay_fields(self):
        cat, _, _ = make(None)
        overlays, _ = annotate.resolve(cat, [item('secret-id', {'trail': 7}, **{'set': {'grade': 'flat'}, 'comment': 'ok'})], VOCAB)
        data = {'catalog': cat, 'matched': etl.collection([]), 'rides': etl.collection([]), 'overlays': overlays, 'vocabulary': VOCAB}
        text = json.dumps(public.export_public(data))
        self.assertNotIn('secret-id', text)
        self.assertEqual(set(overlays['features'][0]['properties']) - set(public.OVERLAY_FIELDS), set())


class ValidationTest(unittest.TestCase):
    def check(self, items):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.json'; p.write_text(json.dumps({'version': 1, 'items': items}))
            return annotate.load_items(VOCAB, p)

    def test_good_and_bad(self):
        self.assertEqual(len(self.check([item('a', {'trail': 1}, **{'set': {'surface': 'dirt'}, 'tags': ['parking']})])), 1)
        for bad in [item('a', {'trail': 1}, **{'set': {'surface': 'lava'}}), item('a', {'trail': 1}, tags=['nope']),
                    item('a', {'trail': 1, 'from_m': 5, 'to_m': 2}), item('a', {'trail': '1'}),
                    item('a', {'trail': 1}, comment='x' * 2000), item('a', {'trail': 1}, extra=1), {'target': {'trail': 1}}]:
            with self.assertRaises(annotate.AnnotationError):
                self.check([bad])
        with self.assertRaises(annotate.AnnotationError):
            self.check([item('a', {'trail': 1}), item('a', {'trail': 2})])


if __name__ == '__main__':
    unittest.main()
