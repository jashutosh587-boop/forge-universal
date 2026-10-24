"""Forge API server using only Python's standard library and SQLite.
Run: python server.py
"""
from __future__ import annotations
import hashlib, hmac, json, mimetypes, os, re, secrets, sqlite3, urllib.parse
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('FORGE_DB_PATH', str(ROOT / 'forge.db')))
HOST = os.environ.get('HOST', os.environ.get('FORGE_HOST', '127.0.0.1'))
PORT = int(os.environ.get('PORT', os.environ.get('FORGE_PORT', '8000')))
MAX_BODY = 25 * 1024 * 1024

def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def connect_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def init_db():
    with connect_db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT NOT NULL UNIQUE COLLATE NOCASE,password_hash TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,created_at TEXT NOT NULL,expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,name TEXT NOT NULL,type TEXT NOT NULL DEFAULT 'Universal',description TEXT NOT NULL DEFAULT '',visibility TEXT NOT NULL DEFAULT 'private',data_json TEXT NOT NULL,forked_from TEXT REFERENCES projects(id) ON DELETE SET NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,author_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,name TEXT NOT NULL,data_json TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id,updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_projects_public ON projects(visibility,updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_revisions_project ON revisions(project_id,id DESC);
        ''')
def token_hash(token): return hashlib.sha256(token.encode()).hexdigest()
def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310000)
    return 'pbkdf2_sha256$310000$%s$%s' % (salt.hex(), derived.hex())
def check_password(password, stored):
    try:
        scheme, rounds, salt, expected = stored.split('$')
        if scheme != 'pbkdf2_sha256': return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError): return False
def public_user(row): return {'id':row['id'],'email':row['email'],'created_at':row['created_at']}
def project_json(row, include_data=False):
    p={'id':row['id'],'name':row['name'],'type':row['type'],'description':row['description'],'visibility':row['visibility'],'forked_from':row['forked_from'],'created_at':row['created_at'],'updated_at':row['updated_at']}
    if include_data: p['data']=json.loads(row['data_json'])
    return p
def clean_project(body):
    if not isinstance(body,dict): raise ValueError('Expected a JSON object.')
    name=str(body.get('name','Untitled project')).strip()[:160] or 'Untitled project'
    kind=str(body.get('type','Universal')).strip()[:80] or 'Universal'
    description=str(body.get('description',''))[:5000]
    visibility=str(body.get('visibility','private'))
    if visibility not in ('private','public'): visibility='private'
    data=body.get('data',{})
    if not isinstance(data,dict): raise ValueError('Project data must be an object.')
    encoded=json.dumps(data,ensure_ascii=False,separators=(',',':'))
    if len(encoded.encode('utf-8')) > 20*1024*1024: raise ValueError('Project data is too large (20 MB maximum).')
    return name,kind,description,visibility,encoded

class ForgeHandler(BaseHTTPRequestHandler):
    server_version='ForgeDev/1.0'
    def log_message(self, fmt, *args): print('[%s] %s' % (self.log_date_time_string(), fmt % args))
    def send_json(self, payload, status=200):
        raw=json.dumps(payload,ensure_ascii=False).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(raw)
    def error(self, message, status=400): self.send_json({'error':message},status)
    def read_json(self):
        length=int(self.headers.get('Content-Length','0'))
        if length > MAX_BODY: raise OverflowError
        raw=self.rfile.read(length) if length else b'{}'
        try: return json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError,json.JSONDecodeError): raise ValueError('Request body must be valid JSON.')
    def auth_user(self):
        auth=self.headers.get('Authorization','')
        if not auth.startswith('Bearer '): return None
        token=auth[7:].strip()
        with connect_db() as db:
            row=db.execute('SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?',(token_hash(token),now())).fetchone()
            return dict(row) if row else None
    def require_user(self):
        user=self.auth_user()
        if not user: self.error('Sign in required or session expired.',401); return None
        return user
    def do_GET(self):
        path=urllib.parse.urlsplit(self.path); route=path.path; query=urllib.parse.parse_qs(path.query)
        try:
            if route=='/api/health': return self.send_json({'ok':True,'service':'Forge API','database':'sqlite','time':now()})
            if route=='/api/me':
                u=self.require_user()
                if u: self.send_json({'user':public_user(u)})
                return
            if route=='/api/projects':
                u=self.require_user()
                if not u:return
                with connect_db() as db: rows=db.execute('SELECT * FROM projects WHERE owner_id=? ORDER BY updated_at DESC',(u['id'],)).fetchall()
                return self.send_json({'projects':[project_json(r) for r in rows]})
            if route=='/api/public/projects':
                q=(query.get('q',[''])[0] or '').strip()[:100]
                with connect_db() as db:
                    if q: rows=db.execute("SELECT p.*,u.email AS owner_email FROM projects p JOIN users u ON u.id=p.owner_id WHERE p.visibility='public' AND (p.name LIKE ? OR p.description LIKE ?) ORDER BY p.updated_at DESC LIMIT 100",(f'%{q}%',f'%{q}%')).fetchall()
                    else: rows=db.execute("SELECT p.*,u.email AS owner_email FROM projects p JOIN users u ON u.id=p.owner_id WHERE p.visibility='public' ORDER BY p.updated_at DESC LIMIT 100").fetchall()
                return self.send_json({'projects':[{**project_json(r),'owner_email':r['owner_email']} for r in rows]})
            m=re.fullmatch(r'/api/projects/([^/]+)(?:/revisions)?',route)
            if m and route.endswith('/revisions'):
                u=self.require_user()
                if not u:return
                pid=urllib.parse.unquote(route.split('/')[3])
                with connect_db() as db:
                    owned=db.execute('SELECT id FROM projects WHERE id=? AND owner_id=?',(pid,u['id'])).fetchone()
                    if not owned:return self.error('Project not found.',404)
                    rows=db.execute('SELECT id,name,created_at FROM revisions WHERE project_id=? ORDER BY id DESC',(pid,)).fetchall()
                return self.send_json({'revisions':[dict(r) for r in rows]})
            m=re.fullmatch(r'/api/projects/([^/]+)',route)
            if m:
                u=self.require_user()
                if not u:return
                pid=urllib.parse.unquote(m.group(1))
                with connect_db() as db: row=db.execute('SELECT * FROM projects WHERE id=? AND owner_id=?',(pid,u['id'])).fetchone()
                if not row:return self.error('Project not found.',404)
                return self.send_json({'project':project_json(row,True)})
            # Static files only; never expose the database or backend source.
            rel=urllib.parse.unquote(route.lstrip('/')) or 'index.html'
            target=(ROOT/rel).resolve()
            if not target.is_relative_to(ROOT.resolve()) or target.name in ('server.py',DB_PATH.name) or not target.is_file(): return self.error('Not found.',404)
            raw=target.read_bytes(); mime=mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
            self.send_response(200); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(raw))); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(raw)
        except OverflowError: self.error('Request is too large.',413)
        except Exception as e: print('GET error:',repr(e)); self.error('Internal server error.',500)
    def do_POST(self):
        route=urllib.parse.urlsplit(self.path).path
        try: body=self.read_json()
        except OverflowError:return self.error('Request is too large.',413)
        except ValueError as e:return self.error(str(e))
        try:
            if route=='/api/auth/register':
                if not isinstance(body,dict):return self.error('Expected a JSON object.')
                email=str(body.get('email','')).strip().lower(); password=str(body.get('password',''))
                if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email) or len(email)>254:return self.error('Enter a valid email address.')
                if len(password)<8:return self.error('Password must be at least 8 characters.')
                if len(password)>1024:return self.error('Password is too long.')
                uid=secrets.token_urlsafe(16); created=now(); token=secrets.token_urlsafe(32); expires=(datetime.now(timezone.utc)+timedelta(days=14)).isoformat(timespec='seconds')
                try:
                    with connect_db() as db:
                        db.execute('INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)',(uid,email,password_hash(password),created))
                        db.execute('INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)',(token_hash(token),uid,created,expires))
                        user=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
                except sqlite3.IntegrityError:return self.error('An account with that email already exists.',409)
                return self.send_json({'token':token,'user':public_user(user)},201)
            if route=='/api/auth/login':
                email=str(body.get('email','')).strip().lower(); password=str(body.get('password',''))
                with connect_db() as db:user=db.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
                if not user or not check_password(password,user['password_hash']):return self.error('Incorrect email or password.',401)
                token=secrets.token_urlsafe(32); created=now(); expires=(datetime.now(timezone.utc)+timedelta(days=14)).isoformat(timespec='seconds')
                with connect_db() as db:db.execute('INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)',(token_hash(token),user['id'],created,expires))
                return self.send_json({'token':token,'user':public_user(user)})
            if route=='/api/auth/logout':
                u=self.require_user()
                if not u:return
                token=self.headers.get('Authorization','')[7:].strip()
                with connect_db() as db:db.execute('DELETE FROM sessions WHERE token_hash=?',(token_hash(token),))
                return self.send_json({'ok':True})
            m=re.fullmatch(r'/api/projects/([^/]+)/fork',route)
            if m:
                u=self.require_user()
                if not u:return
                pid=urllib.parse.unquote(m.group(1))
                with connect_db() as db:source=db.execute("SELECT * FROM projects WHERE id=? AND (visibility='public' OR owner_id=?)",(pid,u['id'])).fetchone()
                if not source:return self.error('Project not found or not public.',404)
                new_id=secrets.token_urlsafe(15); timestamp=now(); name='Fork of '+source['name']; data=json.loads(source['data_json']); data['name']=name; data['forkedFrom']=pid; data_json=json.dumps(data,ensure_ascii=False)
                with connect_db() as db:
                    db.execute('INSERT INTO projects(id,owner_id,name,type,description,visibility,data_json,forked_from,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(new_id,u['id'],name,source['type'],source['description'],'private',data_json,pid,timestamp,timestamp))
                    db.execute('INSERT INTO revisions(project_id,author_id,name,data_json,created_at) VALUES(?,?,?,?,?)',(new_id,u['id'],'Forked snapshot',data_json,timestamp))
                    row=db.execute('SELECT * FROM projects WHERE id=?',(new_id,)).fetchone()
                return self.send_json({'project':project_json(row,True)},201)
            if route=='/api/projects':
                u=self.require_user()
                if not u:return
                name,kind,description,visibility,data_json=clean_project(body); pid=secrets.token_urlsafe(15); timestamp=now()
                with connect_db() as db:
                    db.execute('INSERT INTO projects(id,owner_id,name,type,description,visibility,data_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(pid,u['id'],name,kind,description,visibility,data_json,timestamp,timestamp))
                    db.execute('INSERT INTO revisions(project_id,author_id,name,data_json,created_at) VALUES(?,?,?,?,?)',(pid,u['id'],'Initial save',data_json,timestamp))
                    row=db.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
                return self.send_json({'project':project_json(row,True)},201)
            return self.error('Not found.',404)
        except ValueError as e:self.error(str(e))
        except Exception as e:print('POST error:',repr(e));self.error('Internal server error.',500)
    def do_PUT(self):
        route=urllib.parse.urlsplit(self.path).path; m=re.fullmatch(r'/api/projects/([^/]+)',route)
        if not m:return self.error('Not found.',404)
        try:body=self.read_json()
        except OverflowError:return self.error('Request is too large.',413)
        except ValueError as e:return self.error(str(e))
        u=self.require_user()
        if not u:return
        try:name,kind,description,visibility,data_json=clean_project(body)
        except ValueError as e:return self.error(str(e))
        pid=urllib.parse.unquote(m.group(1)); timestamp=now()
        with connect_db() as db:
            cur=db.execute('UPDATE projects SET name=?,type=?,description=?,visibility=?,data_json=?,updated_at=? WHERE id=? AND owner_id=?',(name,kind,description,visibility,data_json,timestamp,pid,u['id']))
            if not cur.rowcount:return self.error('Project not found.',404)
            db.execute('INSERT INTO revisions(project_id,author_id,name,data_json,created_at) VALUES(?,?,?,?,?)',(pid,u['id'],'Saved update',data_json,timestamp))
            row=db.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
        self.send_json({'project':project_json(row,True)})
    def do_DELETE(self):
        route=urllib.parse.urlsplit(self.path).path; m=re.fullmatch(r'/api/projects/([^/]+)',route)
        if not m:return self.error('Not found.',404)
        u=self.require_user()
        if not u:return
        with connect_db() as db:cur=db.execute('DELETE FROM projects WHERE id=? AND owner_id=?',(urllib.parse.unquote(m.group(1)),u['id']))
        if not cur.rowcount:return self.error('Project not found.',404)
        self.send_json({'ok':True})
    def do_OPTIONS(self): self.send_response(204); self.end_headers()

if __name__=='__main__':
    init_db()
    print(f'Forge backend listening on http://{HOST}:{PORT}')
    print(f'SQLite database: {DB_PATH}')
    ThreadingHTTPServer((HOST,PORT),ForgeHandler).serve_forever()
