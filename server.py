"""Notta v3: local multi-session integration build. No cloud services or payments."""
import argparse, base64, hashlib, hmac, json, mimetypes, os, re, secrets, sqlite3, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE=Path(__file__).resolve().parent
DB=None
DB_BACKEND="sqlite"
MAX_BODY=1600000
class Problem(Exception):
 def __init__(self,status,message):self.status=status;self.message=message
def require(value,status=400,message='入力を確認してください。'):
 if not value:raise Problem(status,message)
def text(v,maxlen=500,blank=False):
 require(isinstance(v,str) and len(v)<=maxlen and (blank or bool(v.strip())))
 return v.strip()
def ident():return secrets.token_hex(12)
def stamp():return int(time.time())
def password_hash(password,salt):return hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),310000).hex()
class ClosingConnection(sqlite3.Connection):
 def __exit__(self,*args):
  try:return super().__exit__(*args)
  finally:self.close()
def connect():
 if DB_BACKEND=='postgres':
  from database import Postgres
  return Postgres(DB)
 c=sqlite3.connect(DB,timeout=10,factory=ClosingConnection);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');return c
def init_db(path,backend='sqlite',initialize=True):
 global DB,DB_BACKEND
 if backend not in ['sqlite','postgres']:raise ValueError('Unsupported database backend')
 DB_BACKEND=backend;DB=str(path)if path else None
 if backend=='sqlite':Path(path).parent.mkdir(parents=True,exist_ok=True)
 elif not DB:raise ValueError('Explicit PostgreSQL DSN is required')
 if not initialize:return
 with connect() as c:
  if DB_BACKEND=='postgres':c.execute('BEGIN IMMEDIATE')
  if DB_BACKEND=='sqlite':c.execute('PRAGMA journal_mode=WAL')
  c.executescript('''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('creator','fan')), salt TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),csrf TEXT NOT NULL,expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY, owner TEXT REFERENCES users(id),title TEXT NOT NULL,goal TEXT NOT NULL,output TEXT NOT NULL,conditions TEXT NOT NULL,days INTEGER NOT NULL,launched INTEGER DEFAULT 0,version INTEGER DEFAULT 1,unresolved INTEGER DEFAULT 0,created INTEGER NOT NULL,started INTEGER);
CREATE TABLE IF NOT EXISTS plans(id INTEGER PRIMARY KEY,project TEXT REFERENCES projects(id),snapshot TEXT NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,project TEXT REFERENCES projects(id),parent TEXT REFERENCES tasks(id),depth INTEGER NOT NULL,title TEXT NOT NULL,role TEXT NOT NULL,output TEXT NOT NULL,reference TEXT);
CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY,project TEXT REFERENCES projects(id),task TEXT REFERENCES tasks(id),fan TEXT REFERENCES users(id),experience TEXT NOT NULL,status TEXT NOT NULL,version INTEGER DEFAULT 1,submission TEXT DEFAULT '',feedback TEXT DEFAULT '',confirmation TEXT DEFAULT '',confirmed_at INTEGER,UNIQUE(task,fan));
CREATE TABLE IF NOT EXISTS revisions(id INTEGER PRIMARY KEY,application TEXT REFERENCES applications(id),body TEXT NOT NULL,file_name TEXT,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS attachments(application TEXT PRIMARY KEY REFERENCES applications(id),name TEXT NOT NULL,mime TEXT NOT NULL,data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS next_projects(project TEXT REFERENCES projects(id),source TEXT REFERENCES applications(id),PRIMARY KEY(project,source));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor TEXT REFERENCES users(id),project TEXT REFERENCES projects(id),action TEXT NOT NULL,target TEXT NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts(key TEXT PRIMARY KEY,count INTEGER NOT NULL,expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS account_tokens(token TEXT PRIMARY KEY,email TEXT NOT NULL,role TEXT NOT NULL,kind TEXT NOT NULL CHECK(kind IN ('invite','reset')),expires INTEGER NOT NULL);
''')
  c.execute('DELETE FROM sessions WHERE expires<?',(stamp(),))
  if 'started' not in [r[1] for r in c.execute('PRAGMA table_info(projects)')]:c.execute('ALTER TABLE projects ADD COLUMN started INTEGER')
  import loop_engine,agents
  loop_engine.schema(c);agents.schema(c)
  import tool_connections
  tool_connections.schema(c)
  import payment_ledger
  payment_ledger.schema(c)
def seed():
 """Fictional accounts only, explicit CLI flag; never automatic external registration."""
 if DB_BACKEND!='sqlite':raise ValueError('Demo seed forbidden on PostgreSQL')
 with connect() as c:
  for uid,email,name,role in [('creator','creator@example.test','架空の発信者 はる','creator'),('other','other@example.test','架空の発信者 そら','creator'),('fan','fan@example.test','架空のファン りん','fan'),('fan2','fan2@example.test','架空のファン あお','fan')]:
   salt=secrets.token_hex(16);c.execute('INSERT OR IGNORE INTO users VALUES(?,?,?,?,?,?)',(uid,email,name,role,salt,password_hash('Notta-Demo-2026!',salt)))
def audit(c,user,p,action,target):c.execute('INSERT INTO audit(actor,project,action,target,created) VALUES(?,?,?,?,?)',(user['id'],p,action,target,stamp()))
def get_project(c,pid):
 p=c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone();require(p,404,'企画がありません。');return p
def owner(c,pid,u):
 p=get_project(c,pid);require(p['owner']==u['id'],403,'この企画を編集する権限がありません。');return p
def version(row,d):require(type(d.get('version')) is int and row['version']==d['version'],409,'他の操作で更新されました。再読込して確認してください。')
def editable(c,p):require(not c.execute('SELECT 1 FROM applications WHERE project=?',(p['id'],)).fetchone(),409,'応募後の役割変更は固定されています。次の企画で変更してください。')
def in_period(p):require(not p['started'] or stamp()<p['started']+p['days']*86400,409,'試行期間が終了しました。閲覧と取消、次企画への参照はできます。')
def leaf(c,tid):return not c.execute('SELECT 1 FROM tasks WHERE parent=?',(tid,)).fetchone()
def safe_ref(d):
 if not d:return None
 require(isinstance(d,dict));url=text(d.get('url'),1000);u=urlparse(url)
 require(u.scheme=='https' and u.hostname and not u.username and not u.password,400,'出典URLはhttpsの公開URLを記入してください。')
 status=d.get('status');require(status in ['未確認','本文確認済','対象外'])
 return {'title':text(d.get('title'),150),'url':url,'status':status,'similarity':text(d.get('similarity')),'difference':text(d.get('difference')),'checkedAt':stamp() if status=='本文確認済' else None}
def task_values(d):return (text(d.get('title'),80),text(d.get('role'),40),text(d.get('output')),json.dumps(safe_ref(d.get('reference')),ensure_ascii=False) if d.get('reference') else None)

class Handler(BaseHTTPRequestHandler):
 server_version='NottaLocal/3'
 def log_message(self,*args):pass # no credentials or personal text in logs
 def respond(self,status,data,cookie=None):
  raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.headers_common('application/json; charset=utf-8')
  if cookie:self.send_header('Set-Cookie',cookie)
  self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def headers_common(self,mime):
  self.send_header('Content-Type',mime);self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.send_header('X-Frame-Options','DENY')
  self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
 def session(self,c):
  cookies=dict(x.strip().split('=',1) for x in self.headers.get('Cookie','').split(';') if '=' in x);token=cookies.get('notta_session','')
  s=c.execute('SELECT u.*,s.csrf,s.expires FROM users u JOIN sessions s ON s.user_id=u.id WHERE s.token=? AND s.expires>?',(hashlib.sha256(token.encode()).hexdigest(),stamp())).fetchone()
  require(s,401,'ログインしてください。');return dict(s)
 def input(self):
  require(self.headers.get('Content-Type','').split(';')[0]=='application/json',415,'JSON形式が必要です。')
  try:n=int(self.headers.get('Content-Length','0'))
  except ValueError:raise Problem(400,'本文サイズが不正です。')
  require(0<n<=MAX_BODY,413,'本文が大きすぎます。')
  try:d=json.loads(self.rfile.read(n))
  except (ValueError,UnicodeDecodeError):raise Problem(400,'JSONを読み取れません。')
  require(isinstance(d,dict));return d
 def origin(self):
  host=self.headers.get('Host','');require(host in [f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'],403,'許可されていない接続先です。')
  origin=self.headers.get('Origin');require(not origin or origin in [f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}'],403,'外部サイトからの操作を拒否しました。')
 def do_GET(self):self.handle_request('GET')
 def do_POST(self):self.handle_request('POST')
 def handle_request(self,method):
  try:
   self.origin();path=urlparse(self.path).path
   if not path.startswith('/api/'):
    require(method=='GET',405)
    files={'/':'index.html','/app.js':'app.js','/style.css':'style.css','/loop.js':'loop.js','/connections.js':'connections.js'};require(path in files,404,'ページがありません。')
    p=HERE/'static'/files[path];data=p.read_bytes();
    if DB_BACKEND=='postgres' and path=='/app.js':data=re.sub(rb'<details class="demo-only">.*?</details>',b'',data,flags=re.S)
    self.send_response(200);self.headers_common(mimetypes.guess_type(p)[0] or 'application/octet-stream');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data);return
   with connect() as c:
    if method=='GET':self.read_api(c,path);return
    d=self.input()
    if path=='/api/login':self.login(c,d);return
    if path in ['/api/account/activate','/api/account/reset']:self.account(c,path,d);return
    u=self.session(c);require(hmac.compare_digest(self.headers.get('X-CSRF-Token',''),u['csrf']),403,'操作確認が切れました。再ログインしてください。')
    c.execute('BEGIN IMMEDIATE')
    result=self.write_api(c,path,d,u);c.commit();self.respond(200,result)
  except Problem as e:self.respond(e.status,{'error':e.message})
  except sqlite3.IntegrityError:self.respond(409,{'error':'重複または参照の競合があります。再読込してください。'})
  except sqlite3.OperationalError:self.respond(503,{'error':'保存できませんでした。内容を控えて再試行してください。'})
  except (KeyError,TypeError,ValueError):self.respond(400,{'error':'入力の型や必須項目を確認してください。'})
  except (BrokenPipeError,ConnectionResetError):pass
  except Exception:self.respond(500,{'error':'処理できませんでした。保存内容を確認して再試行してください。'})
 def account(self,c,path,d):
  # No email provider is contacted. Administrator-issued one-time token only.
  key=hashlib.sha256((self.client_address[0]+'account-token').encode()).hexdigest()
  c.execute('BEGIN IMMEDIATE')
  attempt=c.execute('SELECT * FROM login_attempts WHERE key=?',(key,)).fetchone()
  require(not attempt or attempt['expires']<stamp() or attempt['count']<8,429,'試行回数の上限です。15分後に試してください。')
  token=d.get('token','');require(isinstance(token,str) and len(token)<=200)
  row=c.execute('SELECT * FROM account_tokens WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
  expected='invite' if path.endswith('activate') else 'reset'
  if not row or row['expires']<=stamp() or row['kind']!=expected:
   count=(attempt['count'] if attempt and attempt['expires']>stamp() else 0)+1
   c.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)',(key,count,stamp()+900));c.commit();raise Problem(403,'コードが無効・期限切れ・使用済みです。管理者に確認してください。')
  password=d.get('password');require(isinstance(password,str) and 12<=len(password)<=128 and not password.isspace(),400,'パスワードは12〜128文字で設定してください。')
  salt=secrets.token_hex(16);hashed=password_hash(password,salt)
  if expected=='invite':
   require(not c.execute('SELECT 1 FROM users WHERE email=?',(row['email'],)).fetchone(),409,'登録済みです。再設定を確認してください。')
   c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',(ident(),row['email'],text(d.get('name'),40),row['role'],salt,hashed))
  else:
   u=c.execute('SELECT id FROM users WHERE email=?',(row['email'],)).fetchone();require(u,403,'再設定対象がありません。')
   c.execute('UPDATE users SET salt=?,hash=? WHERE id=?',(salt,hashed,u['id']));c.execute('DELETE FROM sessions WHERE user_id=?',(u['id'],))
  c.execute('DELETE FROM account_tokens WHERE email=?',(row['email'],));c.execute('DELETE FROM login_attempts WHERE key=?',(key,));c.commit()
  self.respond(200,{'ok':True,'message':'設定しました。メールアドレスと新しいパスワードでログインしてください。'})
 def login(self,c,d):
  email=text(d.get('email'),200).lower();password=text(d.get('password'),200)
  key=hashlib.sha256((self.client_address[0]+email).encode()).hexdigest();attempt=c.execute('SELECT * FROM login_attempts WHERE key=?',(key,)).fetchone()
  require(not attempt or attempt['expires']<stamp() or attempt['count']<8,429,'試行回数の上限です。15分後に試してください。')
  u=c.execute('SELECT * FROM users WHERE email=?',(email,)).fetchone()
  valid= hmac.compare_digest(password_hash(password,u['salt'] if u else '00'*16),u['hash'] if u else '00'*32)
  if not u or not valid:
   count=(attempt['count'] if attempt and attempt['expires']>stamp() else 0)+1
   c.execute('INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)',(key,count,stamp()+900));c.commit();raise Problem(401,'メールアドレスまたはパスワードが違います。')
  c.execute('DELETE FROM login_attempts WHERE key=?',(key,));token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
  c.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),u['id'],csrf,stamp()+8*3600));c.commit()
  self.respond(200,{'user':{'id':u['id'],'name':u['name'],'role':u['role']},'csrf':csrf},'notta_session='+token+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')
 def read_api(self,c,path):
  if path=='/api/health':self.respond(200,{'ok':True,'version':3,'mode':'local','ai':'unconnected','search':'unconnected','payment':'unconnected'});return
  u=self.session(c)
  if path=='/api/me':self.respond(200,{'user':{k:u[k] for k in ['id','name','role']},'csrf':u['csrf']});return
  if path=='/api/connections':
   import tool_connections
   self.respond(200,tool_connections.read(c,u['id']));return
  if path=='/api/projects':
   rows=c.execute('SELECT p.*,u.name creator FROM projects p JOIN users u ON p.owner=u.id WHERE p.launched=1 OR p.owner=? ORDER BY p.created DESC',(u['id'],)).fetchall();self.respond(200,{'projects':[dict(r) for r in rows]});return
  m=re.fullmatch(r'/api/projects/([a-f0-9]{24})',path)
  if m:
   pid=m[1];p=get_project(c,pid);is_owner=p['owner']==u['id'];require(p['launched'] or is_owner,404,'企画がありません。')
   tasks=[dict(t) for t in c.execute('SELECT * FROM tasks WHERE project=? ORDER BY rowid',(pid,))]
   for t in tasks:t['reference']=json.loads(t['reference']) if t['reference'] else None
   # Creators see all applicants of their own project, fans only themselves.
   apps=c.execute('SELECT a.*,u.name fan_name FROM applications a JOIN users u ON a.fan=u.id WHERE a.project=? AND (? OR a.fan=?)',(pid,is_owner,u['id'])).fetchall()
   result=[]
   for a in apps:
    item=dict(a);f=c.execute('SELECT name,mime FROM attachments WHERE application=?',(a['id'],)).fetchone();item['file']=dict(f) if f else None
    item['revisions']=[dict(r) for r in c.execute('SELECT body,file_name,created FROM revisions WHERE application=? ORDER BY id',(a['id'],))];result.append(item)
   # Only creator-confirmed contributions are visible across users; no private experience or files.
   cards=[dict(r) for r in c.execute("SELECT a.id,u.name,a.task,t.role,a.submission,a.confirmation,a.confirmed_at FROM applications a JOIN users u ON a.fan=u.id JOIN tasks t ON t.id=a.task WHERE a.project=? AND a.status='確認済'",(pid,))]
   provenance=[dict(r) for r in c.execute('SELECT n.source,a.status,a.confirmation FROM next_projects n JOIN applications a ON a.id=n.source WHERE n.project=?',(pid,))]
   self.respond(200,{'project':dict(p),'tasks':tasks,'applications':result,'contributions':cards,'provenance':provenance,'history':[dict(r) for r in c.execute('SELECT snapshot,created FROM plans WHERE project=? ORDER BY id',(pid,))] if is_owner else []});return
  m=re.fullmatch(r'/api/projects/([a-f0-9]{24})/loop',path)
  if m:
   p=owner(c,m[1],u);import loop_engine,agents
   result=loop_engine.read(c,p);agents.sync(c,p);result['agents']=agents.read(c,p['id']);self.respond(200,result);return
  m=re.fullmatch(r'/api/files/([a-f0-9]{24})',path)
  if m:
   a=c.execute('SELECT a.*,p.owner FROM applications a JOIN projects p ON p.id=a.project WHERE a.id=?',(m[1],)).fetchone();require(a,404);require(u['id'] in [a['fan'],a['owner']],403,'この成果物を閲覧できません。')
   f=c.execute('SELECT * FROM attachments WHERE application=?',(m[1],)).fetchone();require(f,404,'ファイルがありません。')
   self.send_response(200);self.headers_common('application/octet-stream');self.send_header('Content-Disposition','attachment; filename="submission'+Path(f['name']).suffix+'"');self.send_header('Content-Length',str(len(f['data'])));self.end_headers();self.wfile.write(f['data']);return
  raise Problem(404,'APIがありません。')
 def write_api(self,c,path,d,u):
  connection=re.fullmatch(r'/api/connections/(register|assign|mock|disconnect)',path)
  if connection:
   import tool_connections
   try:return tool_connections.act(c,u['id'],connection[1],d)
   except tool_connections.ToolError as e:raise Problem(e.status,e.message)
  m=re.fullmatch(r'/api/projects/([a-f0-9]{24})/(loop|agents)/([a-z_]+)',path)
  if m:
   pid,kind,action=m.groups();p=owner(c,pid,u);import loop_engine,agents
   try:
    if kind=='loop':
     result=loop_engine.act(c,p,d,u,action)
     if action=='pause':agents.cancel_all(c,pid,'ユーザー停止')
     if action=='search' and result.get('ok'):agents.enqueue(c,p,'analogy',d.get('task'),d.get('event')or ident(),'検索依頼')
     if action in ['answer','define','split','verify']and result.get('ok'):
      try:
       agents.enqueue(c,p,'coordinator',d.get('task'),ident(),'対話回答または依存解消・本人確認イベント');agents.tick(c,pid)
      except agents.AgentError as error:loop_engine.note(c,pid,d.get('task'),'agent-wait',error.message+'。手動の本人判断は保持しました。')
     current=c.execute('SELECT status FROM goal_loops WHERE project=?',(pid,)).fetchone()
     if current and current['status']=='停止中':agents.cancel_all(c,pid,'安全上限による停止')
     agents.sync(c,p);return result
    loop_engine.sync(c,p);run=c.execute('SELECT status FROM goal_loops WHERE project=?',(pid,)).fetchone()
    if action=='request':
     require(run['status'] not in ['停止中','目的達成'],409,'停止中・達成済みです。再開を確認してください。');return agents.enqueue(c,p,d.get('role'),d.get('task'),d.get('event'),'ユーザー依頼')
    if action=='tick':return agents.tick(c,pid)
    if action=='cancel':return agents.cancel(c,pid,d.get('job'))
    raise Problem(404,'エージェント操作がありません。')
   except (loop_engine.LoopError,agents.AgentError)as e:raise Problem(e.status,e.message)
  if path=='/api/logout':
   c.execute('DELETE FROM sessions WHERE user_id=? AND csrf=?',(u['id'],u['csrf']));return {'ok':True}
  if path=='/api/projects':
   require(u['role']=='creator',403,'発信者のみ企画を作成できます。')
   days=d.get('days');require(type(days) is int and 1<=days<=14)
   require(c.execute('SELECT COUNT(*) FROM projects WHERE owner=?',(u['id'],)).fetchone()[0]<20,400,'企画は20件までです。')
   pid=ident();c.execute('INSERT INTO projects(id,owner,title,goal,output,conditions,days,created) VALUES(?,?,?,?,?,?,?,?)',(pid,u['id'],text(d.get('title'),80),text(d.get('goal')),text(d.get('output')),text(d.get('conditions')),days,stamp()))
   for title,role,out in [('伝えたいことを組み立てる','構成役','見出しと順序の案1枚'),('受け手の視点で確かめる','試読役','分かりにくい点と質問3件以内'),('成果を確認して記録する','記録役','本人確認用の成果・貢献一覧')]:c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',(ident(),pid,None,0,title,role,out,None))
   # The initial plan is a rule-based starting point, not an AI result.
   audit(c,u,pid,'create',pid);return {'id':pid}
  m=re.fullmatch(r'/api/projects/([a-f0-9]{24})/(plan|task|split|launch|next)',path)
  if m:
   pid,action=m.groups();p=owner(c,pid,u);version(p,d)
   if action!='next':in_period(p)
   if action in ['plan','task','split']:editable(c,p)
   if action=='plan':
    days=d.get('days');require(type(days) is int and 1<=days<=14)
    snap={k:p[k] for k in ['title','goal','output','conditions','days']};c.execute('INSERT INTO plans(project,snapshot,created) VALUES(?,?,?)',(pid,json.dumps(snap,ensure_ascii=False),stamp()))
    c.execute('DELETE FROM plans WHERE project=? AND id NOT IN (SELECT id FROM plans WHERE project=? ORDER BY id DESC LIMIT 5)',(pid,pid))
    c.execute('UPDATE projects SET title=?,goal=?,output=?,conditions=?,days=?,launched=0,version=version+1 WHERE id=?',(text(d.get('title'),80),text(d.get('goal')),text(d.get('output')),text(d.get('conditions')),days,pid))
   if action=='task':
    t=c.execute('SELECT * FROM tasks WHERE id=? AND project=?',(d.get('task'),pid)).fetchone();require(t,404)
    c.execute('UPDATE tasks SET title=?,role=?,output=?,reference=? WHERE id=?',(*task_values(d),t['id']));c.execute('UPDATE projects SET launched=0,version=version+1 WHERE id=?',(pid,))
   if action=='split':
    t=c.execute('SELECT * FROM tasks WHERE id=? AND project=?',(d.get('task'),pid)).fetchone();require(t,404)
    count=c.execute('SELECT COUNT(*) FROM tasks WHERE project=?',(pid,)).fetchone()[0]
    require(t['depth']<2 and count+2<=11 and leaf(c,t['id']),400,'深さ2・追加分解4回が上限です。未確認を残して本人が判断してください。')
    ref=json.loads(t['reference']) if t['reference'] else None
    if d.get('fromSource'):
     require(ref,400,'先に出典・類似点・相違点を保存してください。')
     options=[('事例との差を確かめる',('類似点：'+ref['similarity']+'／相違点：'+ref['difference']+'。今回の条件に適用できるか本人が確認。')[:500]),('成果へ最小限に応用する',('目標：'+p['goal']+'／成果：'+p['output']+'。出典を手掛かりに最小案を1件作り、違いと未確認点を記録。')[:500])]
     ref=dict(ref,status='未確認',checkedAt=None)
    else:options=[('材料を1つ確かめる','使える材料と不足を1件記録'),('最小の形で試す','小さな試作品と未確認点')]
    for suffix,out in options:c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',(ident(),pid,t['id'],t['depth']+1,(t['title']+'：'+suffix)[:80],t['role'],out,json.dumps(ref,ensure_ascii=False) if d.get('fromSource') else None))
    c.execute('UPDATE projects SET launched=0,version=version+1 WHERE id=?',(pid,))
   if action=='launch':
    require(d.get('unresolvedConsent') is True,400,'出典の限界と未確認点を確認してください。')
    c.execute('UPDATE projects SET launched=1,unresolved=1,started=COALESCE(started,?),version=version+1 WHERE id=?',(stamp(),pid))
   if action=='next':
    apps=c.execute("SELECT * FROM applications WHERE project=? AND status='確認済'",(pid,)).fetchall();require(apps,400,'本人確認済みの貢献が必要です。')
    nid=ident();c.execute('INSERT INTO projects(id,owner,title,goal,output,conditions,days,created) VALUES(?,?,?,?,?,?,?,?)',(nid,u['id'],('次の企画：'+p['title'])[:80],p['goal'],p['output'],p['conditions'],p['days'],stamp()))
    for a in apps:c.execute('INSERT INTO next_projects VALUES(?,?)',(nid,a['id']))
    for t in c.execute('SELECT * FROM tasks WHERE project=? AND parent IS NULL',(pid,)).fetchall():c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',(ident(),nid,None,0,t['title'],t['role'],t['output'],t['reference']))
    audit(c,u,nid,'next',pid);return {'id':nid}
   audit(c,u,pid,action,str(d.get('task',pid)));return {'ok':True}
  if path=='/api/apply':
   require(u['role']=='fan',403,'ファンアカウントで応募してください。');p=get_project(c,d.get('project'));require(p['launched'],409,'まだ募集を開始していません。');in_period(p)
   t=c.execute('SELECT * FROM tasks WHERE id=? AND project=?',(d.get('task'),p['id'])).fetchone();require(t and leaf(c,t['id']),400,'応募できる役割を選んでください。')
   require(d.get('consent') is True,400,'企画条件と記録範囲への同意が必要です。')
   old=c.execute('SELECT * FROM applications WHERE task=? AND fan=?',(t['id'],u['id'])).fetchone()
   if old:
    require(old['status']=='取消',409,'この役割には応募済みです。')
    c.execute("UPDATE applications SET status='応募',experience=?,submission='',feedback='',confirmation='',confirmed_at=NULL,version=version+1 WHERE id=?",(text(d.get('experience')),old['id']));c.execute('DELETE FROM attachments WHERE application=?',(old['id'],));aid=old['id']
   else:
    aid=ident();c.execute('INSERT INTO applications(id,project,task,fan,experience,status) VALUES(?,?,?,?,?,?)',(aid,p['id'],t['id'],u['id'],text(d.get('experience')),'応募'))
   audit(c,u,p['id'],'apply',aid);return {'id':aid}
  m=re.fullmatch(r'/api/applications/([a-f0-9]{24})/(status|submit|cancel)',path)
  if m:
   aid,action=m.groups();a=c.execute('SELECT * FROM applications WHERE id=?',(aid,)).fetchone();require(a,404);p=get_project(c,a['project'])
   require(u['id']==(p['owner'] if action=='status' else a['fan']),403,'この操作の権限がありません。');version(a,d)
   if action!='cancel':in_period(p)
   if action=='cancel':
    require(a['status']!='取消',409,'取消済みです。');c.execute("UPDATE applications SET status='取消',confirmation='',confirmed_at=NULL,version=version+1 WHERE id=?",(aid,))
    import loop_engine
    loop_engine.invalidate_application(c,aid)
   elif action=='status':
    target=d.get('status');allowed={'応募':['候補','担当'],'候補':['担当'],'担当':[],'提出':['確認済','差戻し'],'差戻し':[],'確認済':[],'取消':[]}
    require(target in allowed[a['status']],409,'現在の状態からは変更できません。')
    comment=text(d.get('comment',''),500,blank=target not in ['差戻し','確認済'])
    c.execute('UPDATE applications SET status=?,feedback=?,confirmation=?,confirmed_at=?,version=version+1 WHERE id=?',(target,comment,comment if target=='確認済' else '',stamp() if target=='確認済' else None,aid))
   elif action=='submit':
    require(a['status'] in ['担当','差戻し'],409,'担当決定後または差戻し後に提出してください。');require(c.execute('SELECT COUNT(*) FROM revisions WHERE application=?',(aid,)).fetchone()[0]<20,400,'提出は20版までです。');body=text(d.get('body'),2000);f=d.get('file')
    if f:
     require(isinstance(f,dict));name=text(f.get('name'),120);require(name==Path(name).name and '/' not in name and '\\' not in name)
     ext=Path(name).suffix.lower();require(ext in ['.txt','.md','.png','.jpg','.jpeg','.pdf'],400,'提出できる形式はtxt/md/png/jpg/pdfです。')
     try:data=base64.b64decode(f.get('data',''),validate=True)
     except Exception:raise Problem(400,'ファイルの形式が不正です。')
     require(0<len(data)<=1024*1024,413,'成果物は1MB以下にしてください。')
     require((ext in ['.txt','.md'] and b'\x00' not in data) or (ext=='.png' and data.startswith(b'\x89PNG\r\n\x1a\n')) or (ext in ['.jpg','.jpeg'] and data.startswith(b'\xff\xd8\xff')) or (ext=='.pdf' and data.startswith(b'%PDF-')),400,'拡張子と内容が一致しません。')
     c.execute('INSERT OR REPLACE INTO attachments VALUES(?,?,?,?)',(aid,name,mimetypes.guess_type(name)[0] or 'application/octet-stream',data))
    c.execute('INSERT INTO revisions(application,body,file_name,created) VALUES(?,?,?,?)',(aid,body,f['name'] if f else None,stamp()))
    c.execute("UPDATE applications SET status='提出',submission=?,confirmation='',confirmed_at=NULL,version=version+1 WHERE id=?",(body,aid))
   audit(c,u,p['id'],action,aid);return {'ok':True}
  raise Problem(404,'APIがありません。')

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',default=str(HERE/'data/notta-v3.sqlite3'));ap.add_argument('--port',type=int,default=8765);ap.add_argument('--seed-demo',action='store_true');args=ap.parse_args()
 init_db(args.db)
 if args.seed_demo:seed()
 httpd=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
 print(f'Notta v3 local: http://127.0.0.1:{args.port} (loopback only; no external integrations)',flush=True)
 try:httpd.serve_forever()
 except KeyboardInterrupt:pass
 finally:httpd.server_close()
if __name__=='__main__':main()
