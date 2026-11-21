import csv,json,sys,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import etl,server
from shapely.geometry import LineString,Point
from http.server import ThreadingHTTPServer
FIELDS='enabled,kind,ride_id,lat,lon,radius_m,west,south,east,north,start_mile,end_mile'.split(',')
class PrivacyTest(unittest.TestCase):
 def setUp(self):self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'rules.csv'
 def tearDown(self):self.temp.cleanup()
 def rules(self,rows):
  with self.path.open('w',newline='') as f:
   w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)
  return etl.Privacy(self.path)
 def test_circle_cuts_line_even_without_vertices_inside(self):
  p=self.rules([dict(kind='circle',lat=42,lon=-71,radius_m=100)])
  x,y=etl.FW(-71,42);parts=p.clean(LineString([(x-500,y),(x+500,y)]),'1')
  self.assertEqual(len(parts),2);self.assertTrue(all(not l.intersects(Point(x,y).buffer(100)) for l in parts))
 def test_span_and_ride_rules(self):
  p=self.rules([dict(kind='span',ride_id='1',start_mile=.2,end_mile=.4),dict(kind='ride',ride_id='2')]);line=LineString([(0,0),(etl.MILE,0)])
  self.assertEqual(len(p.clean(line,'1')),2);self.assertAlmostEqual(sum(l.length for l in p.clean(line,'1')),etl.MILE*.8)
  self.assertEqual(p.clean(line,'2'),[]);self.assertEqual(len(p.clean(line,'3')),1)
 def test_invalid_rules_fail_closed(self):
  with self.assertRaises(ValueError):self.rules([dict(kind='mystery')])
 def test_half_mile_segments(self):
  p=self.rules([]);x,y=etl.FW(-71,42)
  f=etl.make_ride('1','2020-01-01',LineString([(x,y),(x+etl.MILE*1.2,y)]),etl.collection([]),p)
  fs=etl.note_segments({'rides':etl.collection([f]),'matched':etl.collection([])})['features']
  self.assertEqual(len(fs),3);self.assertTrue(all(f['properties']['miles']<=.5 for f in fs))
  self.assertEqual(f['properties']['name'],'Cycling ride')
 def test_box_and_multiple_spans(self):
  p=self.rules([dict(kind='box',ride_id='1',west=-71.001,south=41.999,east=-70.999,north=42.001)])
  x,y=etl.FW(-71,42);line=LineString([(x-500,y),(x+500,y)])
  self.assertEqual(len(p.clean(line,'1')),2);self.assertEqual(len(p.clean(line,'2')),1)
  p=self.rules([dict(kind='span',ride_id='1',start_mile=.1,end_mile=.2),dict(kind='span',ride_id='1',start_mile=.6,end_mile=.8)])
  self.assertAlmostEqual(sum(l.length for l in p.clean(LineString([(0,0),(etl.MILE,0)]),'1')),etl.MILE*.7)
 def test_matching_and_notes_cannot_restore_hidden_circle(self):
  p=self.rules([dict(kind='circle',lat=42,lon=-71,radius_m=100)])
  x,y=etl.FW(-71,42);line=LineString([(x-1000,y),(x+1000,y)])
  ride=etl.make_ride('1','2020-01-01',line,etl.collection([]),p)
  trail={'type':'Feature','properties':{'id':1,'name':'Test trail','category':'path','status':'existing','state':'MA'},'geometry':etl.geometry([line])}
  assets={'catalog':etl.collection([trail]),'states':etl.collection([]),'catalog_info':{}}
  d=etl.build([ride],'2020-01-01',assets,p)
  from shapely.geometry import shape
  for key in ['rides','matched','note_segments']:
   for f in d[key]['features']:
    g=etl.transform(etl.FW,shape(f['geometry']))
    self.assertFalse(g.intersects(Point(x,y).buffer(100)))
class NotesTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.old=server.ROOT,server.NOTES
  server.ROOT=Path(self.temp.name);server.NOTES=server.ROOT/'notes.json';(server.ROOT/'data').mkdir()
  self.segment={'type':'Feature','properties':{'segment_id':'safe'},'geometry':{'type':'LineString','coordinates':[[0,0],[1,1]]}}
  (server.ROOT/'data/versions.json').write_text(json.dumps({'versions':[{'url':'data/test.json'}]}))
  (server.ROOT/'data/test.json').write_text(json.dumps({'note_segments':etl.collection([self.segment])}))
  self.http=ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=self.http.serve_forever,daemon=True).start();self.url='http://127.0.0.1:'+str(self.http.server_port)
 def tearDown(self):self.http.shutdown();self.http.server_close();server.ROOT,server.NOTES=self.old;self.temp.cleanup()
 def req(self,path='/api/notes',payload=None,token=None,origin=None):
  headers={}
  if token is not None:headers['X-Notes-Token']=token
  if origin is not None:headers['Origin']=origin
  req=urllib.request.Request(self.url+path,data=json.dumps(payload).encode() if payload else None,headers=headers)
  try:
   with urllib.request.urlopen(req) as r:return r.status,json.load(r)
  except urllib.error.HTTPError as e:return e.code,json.load(e)
 def test_save_revision_backup_and_hidden_note(self):
  code,d=self.req();self.assertEqual(code,200);p={'segment_id':'safe','text':'Test','revision':0}
  self.assertEqual(self.req(payload=p,token=d['token'])[0],200);self.assertEqual(self.req(payload=p,token=d['token'])[0],409)
  p.update(text='Edited',revision=1);self.assertEqual(self.req(payload=p,token=d['token'])[0],200)
  self.assertTrue(server.NOTES.with_suffix('.backup.json').exists())
  (server.ROOT/'data/test.json').write_text(json.dumps({'note_segments':etl.collection([])}))
  self.assertEqual(self.req()[1]['notes'],{});self.assertIn('safe',json.loads(server.NOTES.read_text())['notes'])
 def test_private_urls_and_untrusted_writes_denied(self):
  self.assertEqual(self.req('/settings.json')[0],404);self.assertEqual(self.req('/../notes.json')[0],404)
  p={'segment_id':'safe','text':'Test','revision':0}
  self.assertEqual(self.req(payload=p,token='bad')[0],403);self.assertEqual(self.req(payload=p,token=server.TOKEN,origin='https://example.com')[0],403)
 def test_corrupt_file_preserved(self):
  server.NOTES.write_text('broken');self.assertEqual(self.req()[0],500);self.assertEqual(server.NOTES.read_text(),'broken')
if __name__=='__main__':unittest.main()
