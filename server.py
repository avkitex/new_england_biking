"""Loopback-only map and private note storage; no arbitrary file access."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json, os, secrets, threading
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parent
import paths
PRIVATE=paths.private_dir()
NOTES=PRIVATE/'private-notes.json'
ANNOTATIONS=ROOT/'annotations/annotations.json'
CATALOG=ROOT/'assets/catalog.json'
LOCK=threading.RLock()
TOKEN=secrets.token_urlsafe(32)
def public_segments():
    manifest=json.loads((ROOT/'data/versions.json').read_text(encoding='utf-8'))
    return {f['properties']['segment_id']:f for v in manifest['versions']
            for f in json.loads((ROOT/v['url']).read_text(encoding='utf-8'))['note_segments']['features']}
def read_notes():
    if not NOTES.exists():return {'version':1,'notes':{}}
    data=json.loads(NOTES.read_text(encoding='utf-8'))
    if data.get('version')!=1 or not isinstance(data.get('notes'),dict):raise ValueError('Invalid notes file; left unchanged.')
    return data
_SEGMENTS={}
def ride_segments():
    """segment_id -> feature for every ride section of every registered snapshot (cached by mtime)."""
    manifest=json.loads((ROOT/'data/versions.json').read_text(encoding='utf-8'));out={}
    for v in manifest['versions']:
        path=ROOT/v['url'];key=(str(path),path.stat().st_mtime_ns)
        if key not in _SEGMENTS:
            for old in [k for k in _SEGMENTS if k[0]==key[0]]:del _SEGMENTS[old]
            data=json.loads(path.read_text(encoding='utf-8'))
            _SEGMENTS[key]={f['properties']['segment_id']:f for f in data['note_segments']['features'] if f['properties']['kind']=='ride'}
        out.update(_SEGMENTS[key])
    return out
def load_privacy():
    import etl
    settings=PRIVATE/'settings.json'
    if not settings.exists():return None
    return etl.Privacy(paths.resolve(PRIVATE,etl.read(settings)['exclusions']))
def annotation_payload(items,rev):
    import annotate,annotation_store
    vocab=annotate.load_vocabulary()
    return {**annotation_store.state(items,annotation_store.load_catalog(CATALOG),vocab),'revision':rev}
class Handler(BaseHTTPRequestHandler):
    def trusted(self,write=False):
        hosts={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in hosts:return False
        origin=self.headers.get('Origin')
        if origin and origin not in {'http://'+h for h in hosts}:return False
        return not write or secrets.compare_digest(self.headers.get('X-Notes-Token',''),TOKEN)
    def respond(self,status,data):
        body=json.dumps(data,ensure_ascii=False).encode('utf-8');self.send_response(status)
        for k,v in {'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Content-Length':str(len(body))}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if not self.trusted():return self.respond(403,{'error':'Local access only.'})
        path=self.path.split('?')[0]
        if path=='/api/annotations':
            try:
                import annotate,annotation_store
                with LOCK:
                    rev=annotation_store.revision(ANNOTATIONS);items=annotate.load_items(annotate.load_vocabulary(),ANNOTATIONS)
                    return self.respond(200,{**annotation_payload(items,rev),'token':TOKEN})
            except (ValueError,OSError) as e:return self.respond(500,{'error':str(e)})
        if path=='/api/notes':
            try:
                with LOCK:data=read_notes()
                segments=public_segments()
                visible={sid:{**note,'segment':segments[sid]} for sid,note in data['notes'].items() if sid in segments}
                return self.respond(200,{**data,'notes':visible,'token':TOKEN})
            except (ValueError,OSError) as e:return self.respond(500,{'error':str(e)})
        manifest=json.loads((ROOT/'data/versions.json').read_text(encoding='utf-8'))
        allowed={'/data/versions.json', *('/'+v['url'] for v in manifest['versions'])}
        if path in ('/','/index.html','/new-england-cycling.html'):
            target=ROOT/'index.html';mime='text/html; charset=utf-8'
        elif path in allowed:
            target=ROOT/path.lstrip('/');mime='application/json; charset=utf-8'
        else:return self.respond(404,{'error':'Not found.'})
        body=target.read_bytes();self.send_response(200)
        for k,v in {'Content-Type':mime,'Content-Length':str(len(body)),'Cache-Control':'no-store','Referrer-Policy':'strict-origin-when-cross-origin'}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(body)
    def post_annotations(self):
        import annotate,annotation_store
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=1_000_000:return self.respond(413,{'error':'Request too large.'})
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict) or not isinstance(payload.get('edit'),dict):return self.respond(400,{'error':'Expected an edit.'})
            vocab=annotate.load_vocabulary()
            with LOCK:
                rev=annotation_store.revision(ANNOTATIONS)
                if payload.get('revision')!=rev:return self.respond(409,{'error':'Annotations changed elsewhere. Reload before saving.'})
                items=annotate.load_items(vocab,ANNOTATIONS)
                roads=lambda ids:annotation_store.roads_geometry(ride_segments(),load_privacy(),ids)
                draw=lambda pts:annotation_store.drawn_geometry(load_privacy(),pts)
                area=lambda pts:annotation_store.area_geometry(load_privacy(),pts)
                new=annotation_store.apply_edit(items,annotation_store.load_catalog(CATALOG),vocab,payload['edit'],roads,draw,area)
                annotation_store.write_items(ANNOTATIONS,new,PRIVATE)
                return self.respond(200,annotation_payload(new,annotation_store.revision(ANNOTATIONS)))
        except annotate.AnnotationError as e:return self.respond(400,{'error':str(e)})
        except (ValueError,OSError,TypeError) as e:return self.respond(500,{'error':str(e)})
    def post_exclusion(self):
        import cuts_store
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=100000:return self.respond(413,{'error':'Request too large.'})
            payload=json.loads(self.rfile.read(length))
            settings=PRIVATE/'settings.json'
            if not isinstance(payload,dict) or not settings.exists():return self.respond(400,{'error':'Private settings not found.'})
            import etl
            target=paths.resolve(PRIVATE,etl.read(settings)['exclusions'])
            with LOCK:count=cuts_store.add_rule(target,payload,PRIVATE)
            return self.respond(200,{'rules':count})
        except cuts_store.ExclusionError as e:return self.respond(400,{'error':str(e)})
        except (ValueError,OSError,KeyError,TypeError):return self.respond(500,{'error':'Could not save the rule.'})
    def post_rebuild(self):
        import subprocess,sys
        try:
            with LOCK:
                r=subprocess.run([sys.executable,str(ROOT/'etl.py')],cwd=str(ROOT),capture_output=True,text=True,timeout=900)
            if r.returncode!=0:return self.respond(500,{'error':'Rebuild failed; check the private inputs.'})
            return self.respond(200,{'ok':True})
        except (OSError,subprocess.SubprocessError):return self.respond(500,{'error':'Rebuild failed.'})
    def do_POST(self):
        if self.path in('/api/exclusions','/api/rebuild'):
            if not self.trusted(True):return self.respond(403,{'error':'Untrusted request. Reload the local map.'})
            return self.post_exclusion() if self.path=='/api/exclusions' else self.post_rebuild()
        if self.path=='/api/annotations':
            if not self.trusted(True):return self.respond(403,{'error':'Untrusted request. Reload the local map.'})
            return self.post_annotations()
        if self.path!='/api/notes':return self.respond(404,{'error':'Not found.'})
        if not self.trusted(True):return self.respond(403,{'error':'Untrusted request. Reload the local map.'})
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=50000:return self.respond(413,{'error':'Note too large.'})
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict):return self.respond(400,{'error':'Expected a JSON object.'})
            sid=payload.get('segment_id');text=payload.get('text')
            segments=public_segments()
            if sid not in segments or not isinstance(text,str) or len(text)>10000:return self.respond(400,{'error':'Invalid segment or note (maximum 10,000 characters).'})
            with LOCK:
                data=read_notes();old=data['notes'].get(sid,{})
                if payload.get('revision',0)!=old.get('revision',0):return self.respond(409,{'error':'This note changed in another tab. Reload notes before saving.','current':old})
                now=datetime.now(timezone.utc).isoformat()
                note={'text':text,'revision':old.get('revision',0)+1,'created_at':old.get('created_at',now),'updated_at':now,'segment':segments[sid]}
                data['notes'][sid]=note;tmp=NOTES.with_suffix('.tmp')
                with tmp.open('w',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
                if NOTES.exists():NOTES.with_suffix('.backup.json').write_bytes(NOTES.read_bytes())
                os.replace(tmp,NOTES)
            return self.respond(200,{'note':note})
        except (ValueError,OSError,TypeError) as e:return self.respond(500,{'error':str(e)})
    def log_message(self,*args):pass
def main():
    PRIVATE.mkdir(parents=True,exist_ok=True)
    if not NOTES.exists():NOTES.write_text('{"version":1,"notes":{}}',encoding='utf-8')
    ThreadingHTTPServer(('127.0.0.1',8765),Handler).serve_forever()
if __name__=='__main__':main()
