#!/usr/bin/env python3
"""Local, Markdown-backed CRM. Python standard library only."""
import argparse, copy, hashlib, json, os, re, tempfile, threading, uuid
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

BLOCK = re.compile(r'(## Structured record\s*\n```json\s*\n)(.*?)(\n```)', re.S)
ID = re.compile(r'^[a-zA-Z0-9_-]{1,100}$')
LOCK = threading.RLock()
# Exact CSS observed in Codex's annotation preload. Keep script and inline-attribute restrictions.
ANNOTATION_STYLE_HASHES = (
    'sha256-OUoShR0c3lqWNHEaHdorwfYnp/0EJHI/7QhTEUtan9w=',
    'sha256-bcCtXUz3q46yDLxfuGeAP5Mgp9ZCmIkyTmh9IVgoGF4=',
)
CONTENT_SECURITY_POLICY = ("default-src 'self'; style-src 'self' " +
    ' '.join("'" + value + "'" for value in ANNOTATION_STYLE_HASHES) +
    "; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
class Problem(Exception):
    def __init__(self, message, status=400): self.message, self.status = message, status

def read_document(path):
    try:
        raw = path.read_bytes(); text = raw.decode('utf-8'); matches = list(BLOCK.finditer(text))
        if len(matches) != 1: raise ValueError('Expected one structured record block')
        value = json.loads(matches[0].group(2))
        if not isinstance(value, dict): raise ValueError('Record is not an object')
        return value, hashlib.sha256(raw).hexdigest(), text
    except (OSError, ValueError, UnicodeError) as e: raise Problem(f'Cannot read {path.name}: {e}', 422)

def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.crm-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f: f.write(text); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def replace_block(text, value):
    match = BLOCK.search(text)
    return text[:match.start(2)] + json.dumps(value, ensure_ascii=False, indent=2) + text[match.end(2):]

class Store:
    def __init__(self, root): self.root = Path(root).resolve()
    def safe(self, path):
        path=Path(path)
        try: relative=path.relative_to(self.root)
        except ValueError: raise Problem('Path outside CRM directory',403)
        current=self.root
        for part in relative.parts:
            current=current/part
            if current.is_symlink(): raise Problem('Symlinks are not supported in CRM data',403)
        if not path.resolve().is_relative_to(self.root): raise Problem('Path outside CRM directory',403)
        return path
    def edited_evidence(self,value,patch):
        now=datetime.now(timezone.utc).isoformat()
        fields=value.setdefault('additionalFields',{})
        if not isinstance(fields,dict): raise Problem('Invalid additionalFields',422)
        evidence=fields.setdefault('fieldEvidence',{})
        if not isinstance(evidence,dict): raise Problem('Invalid field evidence',422)
        for key in patch:
            evidence[key]={'sourceDate':now,'source':'Local CRM user edit','basis':'Manual edit through the local CRM'}
        value['updatedAt']=now
    def path(self, ident):
        if not isinstance(ident, str) or not ID.fullmatch(ident): raise Problem('Invalid record id')
        directory=self.safe(self.root/'records')
        matches=list(directory.glob('*__'+ident+'.md'))
        fallback=directory/(ident+'.md')
        if fallback.exists() or fallback.is_symlink(): matches.append(fallback)
        if len(matches)>1: raise Problem('Ambiguous record id',422)
        return self.safe(matches[0] if matches else fallback)
    def boards(self):
        value, rev, text = read_document(self.safe(self.root / 'boards.md'))
        if value.get('schemaVersion') != 1 or not isinstance(value.get('boards'), list): raise Problem('Invalid board document', 422)
        for b in value['boards']:
            if not isinstance(b, dict) or not isinstance(b.get('id'), str) or not isinstance(b.get('name'), str) or not isinstance(b.get('stages'), list) or not all(isinstance(s,str) for s in b['stages']): raise Problem('Invalid board definition',422)
        return value, rev, text
    def state(self):
        with LOCK:
            boards, revision, _ = self.boards(); records = []
            for p in sorted(self.safe(self.root / 'records').glob('*.md')):
                record, rev, _ = read_document(self.safe(p))
                if record.get('id') != p.stem.rsplit('__',1)[-1]: raise Problem(f'ID mismatch in {p.name}',422)
                records.append(dict(record, revision=rev))
            return {'boards': boards['boards'], 'boardsRevision': revision, 'records': records}
    def validate(self, patch):
        allowed = {'name','kind','website','fundingStages','industries','investmentRating','lastCommunication','notes','memberships','communicationType','replyStatus','replyDueAt','nextAction'}
        if not isinstance(patch,dict) or set(patch)-allowed: raise Problem('Unsupported editable fields')
        for key in ('name','website','notes','nextAction'):
            if key in patch and (not isinstance(patch[key],str) or len(patch[key]) > (30000 if key=='notes' else 4000 if key=='nextAction' else 2000)): raise Problem(f'Invalid {key}')
        enums={'communicationType':{None,'email','call','video_call','meeting','message','other'},'replyStatus':{None,'needs_reply','waiting_on_them','done'}}
        for key,choices in enums.items():
            if key in patch and (patch[key] is not None and not isinstance(patch[key],str) or patch[key] not in choices): raise Problem(f'Invalid {key}')
        if patch.get('replyDueAt') is not None:
            value=patch['replyDueAt']
            if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value): raise Problem('Reply due date must be YYYY-MM-DD')
            try: date.fromisoformat(value)
            except ValueError: raise Problem('Invalid reply due date')
        if 'name' in patch and not patch['name'].strip(): raise Problem('Name is required')
        if 'kind' in patch and patch['kind'] not in ('company','person'): raise Problem('Invalid kind')
        if patch.get('website'):
            u = urlsplit(patch['website'])
            if u.scheme not in ('http','https') or not u.netloc or u.username or u.password: raise Problem('Website must be an http or https URL')
        for key in ('fundingStages','industries'):
            if key in patch and (not isinstance(patch[key],list) or len(patch[key])>50 or not all(isinstance(x,str) and 0<len(x)<=100 for x in patch[key])): raise Problem(f'Invalid {key}')
        if 'investmentRating' in patch and patch['investmentRating'] is not None and (type(patch['investmentRating']) is not int or not 1<=patch['investmentRating']<=5): raise Problem('Rating must be 1–5 stars or empty')
        if 'lastCommunication' in patch and patch['lastCommunication'] is not None:
            try: datetime.fromisoformat(patch['lastCommunication'].replace('Z','+00:00'))
            except (ValueError,AttributeError): raise Problem('Invalid communication date')
        if 'memberships' in patch:
            boards = {b['id']:b for b in self.boards()[0]['boards']}
            if not isinstance(patch['memberships'],dict): raise Problem('Invalid memberships')
            for bid,m in patch['memberships'].items():
                if bid not in boards or not isinstance(m,dict) or set(m)-{'stage','order'} or m.get('stage') not in boards[bid]['stages'] or type(m.get('order')) not in (int,float) or not 0<=m['order']<=1e12: raise Problem('Invalid stage or order')
    def log(self, ident, fields):
        p=self.safe(self.root/'Changes.md'); old=p.read_text() if p.exists() else '# CRM changes\n\n'
        atomic_write(p,old+f"- {datetime.now(timezone.utc).isoformat()} · Local CRM · {ident} · {', '.join(fields)}\n")
    def update(self, ident, payload):
        with LOCK:
            if not isinstance(payload,dict): raise Problem('Expected an object')
            patch=payload.get('changes'); self.validate(patch)
            p=self.path(ident); value,rev,text=read_document(p)
            if payload.get('expectedRevision') != rev: raise Problem('This record changed. Refresh and review before saving.',409)
            changed={k:v for k,v in patch.items() if value.get(k)!=v}
            if not changed: return dict(value,revision=rev)
            value.update(changed)
            self.edited_evidence(value,changed)
            atomic_write(p,replace_block(text,value)); self.log(ident,list(changed))
            result,newrev,_=read_document(p); return dict(result,revision=newrev)
    def create(self,payload):
        with LOCK:
            self.validate(payload)
            if not payload.get('name'): raise Problem('Name is required')
            ident=str(uuid.uuid4()); value={'schemaVersion':1,'id':ident,'kind':'company','name':'','website':'','fundingStages':[],'industries':[],'investmentRating':None,'lastCommunication':None,'notes':'','memberships':{},'communicationType':None,'replyStatus':None,'replyDueAt':None,'nextAction':'','source':{'system':'Local CRM','url':'','capturedAt':datetime.now(timezone.utc).isoformat(),'rawRef':''},'vaultLinks':[],'reconciliation':{'state':'local','notes':''},'additionalFields':{}}
            value.update(payload); self.edited_evidence(value,payload); filename=re.sub(r'[^A-Za-z0-9_-]+','_',payload['name']).strip('_')[:90] or 'Record'
            newpath=self.safe(self.root/'records'/(filename+'__'+ident+'.md'))
            atomic_write(newpath,'# CRM record\n\n## Structured record\n```json\n'+json.dumps(value,ensure_ascii=False,indent=2)+'\n```\n'); self.log(ident,['created'])
            v,r,_=read_document(self.path(ident)); return dict(v,revision=r)
    def reorder_boards(self,payload):
        with LOCK:
            if not isinstance(payload,dict): raise Problem('Expected an object')
            value,rev,text=self.boards()
            if payload.get('expectedRevision')!=rev: raise Problem('Boards changed. Refresh before saving.',409)
            bid=payload.get('boardId'); stages=payload.get('stages'); board=next((b for b in value['boards'] if b['id']==bid),None)
            if not board or not isinstance(stages,list) or len(stages)!=len(board['stages']) or sorted(stages)!=sorted(board['stages']): raise Problem('Only existing stages may be reordered')
            board['stages']=stages; atomic_write(self.safe(self.root/'boards.md'),replace_block(text,value)); self.log('boards',['stage order']); return self.state()

class Handler(BaseHTTPRequestHandler):
    def valid_request(self,unsafe=False):
        port=self.server.server_port; hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
        if self.headers.get('Host') not in hosts: raise Problem('Untrusted Host',403)
        origin=self.headers.get('Origin')
        if origin and origin not in {f'http://{h}' for h in hosts}: raise Problem('Untrusted Origin',403)
        if unsafe and self.headers.get('Content-Type','').split(';')[0]!='application/json': raise Problem('JSON required',415)
        if unsafe and not origin: raise Problem('Origin required',403)
    def respond(self,value,status=200):
        raw=json.dumps(value,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def body(self):
        length=int(self.headers.get('Content-Length','0'))
        if not 0<length<=100000: raise Problem('Invalid body size',413)
        try: return json.loads(self.rfile.read(length))
        except ValueError: raise Problem('Invalid JSON')
    def do_GET(self):
        try:
            self.valid_request()
            request_path=urlsplit(self.path).path
            if request_path=='/api/state': return self.respond(self.server.store.state())
            assets={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/fonts/geist-latin.woff2':'fonts/geist-latin.woff2'}
            if request_path not in assets: raise Problem('Not found',404)
            p=Path(__file__).parent/'static'/assets[request_path]; raw=p.read_bytes(); self.send_response(200)
            self.send_header('Content-Type',{'html':'text/html','js':'text/javascript','css':'text/css','woff2':'font/woff2'}[p.suffix[1:]])
            self.send_header('Content-Security-Policy',CONTENT_SECURITY_POLICY)
            self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
        except Problem as e: self.respond({'error':e.message},e.status)
    def mutate(self):
        try:
            self.valid_request(True); payload=self.body()
            if self.command=='POST' and self.path=='/api/records': return self.respond(self.server.store.create(payload),201)
            if self.command=='PATCH' and self.path=='/api/boards': return self.respond(self.server.store.reorder_boards(payload))
            if self.command=='PATCH' and self.path.startswith('/api/records/'): return self.respond(self.server.store.update(self.path[len('/api/records/'):],payload))
            raise Problem('Not found',404)
        except Problem as e: self.respond({'error':e.message},e.status)
        except (ValueError,TypeError) as e: self.respond({'error':'Invalid request'},400)
        except OSError: self.respond({'error':'Filesystem write failed. Refresh to inspect the current record before retrying.'},500)
    do_PATCH=mutate
    do_POST=mutate
    def log_message(self,*args): pass

def main():
    a=argparse.ArgumentParser(); a.add_argument('--data-dir',required=True); a.add_argument('--port',type=int,default=8765); args=a.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler); server.store=Store(args.data_dir)
    print(f'Local CRM: http://127.0.0.1:{args.port}',flush=True); server.serve_forever()
if __name__=='__main__': main()
