"""HTTP integration tests use disposable SQLite and fictional accounts only."""
import base64, importlib.util, json, sqlite3, tempfile, threading, unittest, urllib.request, urllib.error, http.cookiejar
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
spec=importlib.util.spec_from_file_location('notta_server',Path(__file__).parents[1]/'server.py');server=importlib.util.module_from_spec(spec);spec.loader.exec_module(server)
class Client:
 def __init__(self,base):self.base=base;self.csrf='';self.jar=http.cookiejar.CookieJar();self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
 def call(self,path,data=None,headers=None):
  h={'Content-Type':'application/json','X-CSRF-Token':self.csrf};h.update(headers or {})
  req=urllib.request.Request(self.base+'/api/'+path,data=json.dumps(data).encode() if data is not None else None,headers=h)
  try:r=self.opener.open(req)
  except urllib.error.HTTPError as e:r=e
  raw=r.read()
  try:body=json.loads(raw)
  except ValueError:body=raw
  return r.status,body,r.headers
 def login(self,email):
  status,d,h=self.call('login',{'email':email+'@example.test','password':'Notta-Demo-2026!'});assert status==200,(status,d);self.csrf=d['csrf'];return h
class Integration(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent);cls.db=Path(cls.tmp.name)/'test.sqlite3';server.init_db(cls.db);server.seed();cls.httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler);threading.Thread(target=cls.httpd.serve_forever,daemon=True).start();cls.base='http://127.0.0.1:'+str(cls.httpd.server_port)
 @classmethod
 def tearDownClass(cls):cls.httpd.shutdown();cls.httpd.server_close();cls.tmp.cleanup()
 def setUp(self):
  self.c=Client(self.base);self.c.login('creator');self.f=Client(self.base);self.f.login('fan');self.o=Client(self.base);self.o.login('other');self.f2=Client(self.base);self.f2.login('fan2')
 def project(self):
  d={'title':'架空の紹介文を一緒に改善','goal':'読者の視点で活動を伝える','output':'紹介文1枚','conditions':'架空データのみ。確認済み本文と表示名の履歴表示に同意。取消可。','days':14}
  status,r,_=self.c.call('projects',d);self.assertEqual(status,200,r);return r['id']
 def detail(self,c,p):
  code,d,_=c.call('projects/'+p);self.assertEqual(code,200,d);return d
 def launch(self,p):
  d=self.detail(self.c,p);self.assertEqual(self.c.call('projects/'+p+'/launch',{'version':d['project']['version'],'unresolvedConsent':True})[0],200)
 def apply(self,p):
  t=self.detail(self.c,p)['tasks'][0]['id'];r=self.f.call('apply',{'project':p,'task':t,'experience':'架空の構成経験','consent':True});self.assertEqual(r[0],200,r);return r[1]['id'],t
 def app(self,c,p):return self.detail(c,p)['applications'][0]
 def status(self,p,target,comment=''):
  a=self.app(self.c,p);return self.c.call('applications/'+a['id']+'/status',{'version':a['version'],'status':target,'comment':comment})
 def submit(self,p,body='見出しを先にする提案',file=True):
  a=self.app(self.f,p);d={'version':a['version'],'body':body}
  if file:d['file']={'name':'proposal.txt','data':base64.b64encode(b'fictional contribution').decode()}
  return self.f.call('applications/'+a['id']+'/submit',d)
 def test_authentication_csrf_origin_logout(self):
  self.assertEqual(Client(self.base).call('projects')[0],401)
  h=self.c.login('creator');self.assertIn('HttpOnly',h['Set-Cookie']);self.assertIn('SameSite=Strict',h['Set-Cookie'])
  self.assertEqual(self.c.call('projects',{}, {'X-CSRF-Token':''})[0],403)
  self.assertEqual(self.c.call('projects',{}, {'Origin':'https://evil.example'})[0],403)
  self.assertEqual(self.c.call('me',headers={'Host':'evil.example'})[0],403)
  self.assertEqual(self.c.call('logout',{})[0],200);self.assertEqual(self.c.call('me')[0],401)
 def test_wrong_password_rate_limit_and_expiry(self):
  x=Client(self.base)
  for _ in range(8):self.assertEqual(x.call('login',{'email':'none@example.test','password':'wrong'})[0],401)
  self.assertEqual(x.call('login',{'email':'none@example.test','password':'wrong'})[0],429)
  with server.connect() as c:c.execute('UPDATE sessions SET expires=0 WHERE csrf=?',(self.f.csrf,))
  self.assertEqual(self.f.call('me')[0],401)
 def test_owner_separation_and_hidden_drafts(self):
  p=self.project();self.assertEqual(self.f.call('projects/'+p)[0],404)
  self.assertNotIn(p,[r['id'] for r in self.o.call('projects')[1]['projects']])
  self.assertEqual(self.o.call('projects/'+p+'/launch',{'version':1,'unresolvedConsent':True})[0],403)
  self.assertEqual(self.f.call('projects',{})[0],403)
 def test_plan_history_version_sources_and_split(self):
  p=self.project();d=self.detail(self.c,p);v=d['project']['version'];payload={k:d['project'][k] for k in ['title','goal','output','conditions','days']};payload['title']='調整した企画';payload['version']=v
  self.assertEqual(self.c.call('projects/'+p+'/plan',payload)[0],200)
  self.assertEqual(self.c.call('projects/'+p+'/plan',payload)[0],409)
  d=self.detail(self.c,p);self.assertEqual(len(d['history']),1)
  t=d['tasks'][0];ref={'title':'公式一般例','url':'javascript:alert(1)','status':'本文確認済','similarity':'案を募る','difference':'作品と人数は異なる'}
  body={k:t[k] for k in ['title','role','output']};body.update(task=t['id'],version=d['project']['version'],reference=ref)
  self.assertEqual(self.c.call('projects/'+p+'/task',body)[0],400)
  ref['url']='https://example.test/source';self.assertEqual(self.c.call('projects/'+p+'/task',body)[0],200)
  d=self.detail(self.c,p);self.assertEqual(d['tasks'][0]['reference']['difference'],'作品と人数は異なる')
  self.assertEqual(self.c.call('projects/'+p+'/split',{'task':t['id'],'version':d['project']['version'],'fromSource':True})[0],200)
  d=self.detail(self.c,p);self.assertEqual(len(d['tasks']),5)
  children=[x for x in d['tasks'] if x['parent']==t['id']];self.assertTrue(all(x['reference']['status']=='未確認' for x in children));self.assertTrue(any(d['project']['output'] in x['output'] for x in children))
  self.assertEqual(self.c.call('projects/'+p+'/split',{'task':t['id'],'version':d['project']['version']})[0],400)
 def test_end_to_end_contribution_reuse_and_withdrawal(self):
  p=self.project();self.launch(p);aid,t=self.apply(p)
  self.assertEqual(self.f.call('apply',{'project':p,'task':t,'experience':'二重','consent':True})[0],409)
  self.assertEqual(self.submit(p)[0],409)
  self.assertEqual(self.status(p,'候補')[0],200);self.assertEqual(self.status(p,'担当')[0],200)
  self.assertEqual(self.submit(p)[0],200)
  self.assertEqual(self.status(p,'差戻し','対象読者を明記してください')[0],200)
  self.assertEqual(self.submit(p,'初見読者向けの見出し案')[0],200)
  self.assertEqual(self.status(p,'確認済','この見出しで初見の人に伝わりました')[0],200)
  self.assertEqual(len(self.app(self.f,p)['revisions']),2)
  cards=self.detail(self.f2,p)['contributions'];self.assertEqual(len(cards),1);self.assertNotIn('experience',cards[0]);self.assertEqual(self.detail(self.f2,p)['applications'],[])
  d=self.detail(self.c,p);code,n,_=self.c.call('projects/'+p+'/next',{'version':d['project']['version']});self.assertEqual(code,200);self.assertEqual(len(self.detail(self.c,n['id'])['provenance']),1)
  self.assertEqual(self.f2.call('projects/'+n['id'])[0],404)
  a=self.app(self.f,p);self.assertEqual(self.f.call('applications/'+aid+'/cancel',{'version':a['version']})[0],200)
  self.assertEqual(self.detail(self.c,p)['contributions'],[]);self.assertEqual(self.detail(self.c,n['id'])['provenance'][0]['status'],'取消')
  self.assertEqual(self.f.call('apply',{'project':p,'task':t,'experience':'改めて応募','consent':True})[0],200)
 def test_files_and_application_access_control(self):
  p=self.project();self.launch(p);aid,t=self.apply(p);self.status(p,'担当');self.submit(p)
  self.assertEqual(self.c.call('files/'+aid)[0],200);self.assertEqual(self.f.call('files/'+aid)[1],b'fictional contribution')
  self.assertEqual(self.o.call('files/'+aid)[0],403);self.assertEqual(self.f2.call('files/'+aid)[0],403)
  a=self.app(self.c,p);self.assertEqual(self.o.call('applications/'+aid+'/status',{'version':a['version'],'status':'確認済','comment':'偽承認'})[0],403)
  self.assertEqual(self.f2.call('applications/'+aid+'/cancel',{'version':a['version']})[0],403)
 def test_validation_and_no_false_payment(self):
  self.assertEqual(self.c.call('projects',{'title':' ','goal':'a','output':'b','conditions':'c','days':14})[0],400)
  p=self.project();self.launch(p);aid,t=self.apply(p);self.status(p,'担当');a=self.app(self.f,p)
  for file in [{'name':'../bad.txt','data':'eA=='},{'name':'x.html','data':'eA=='},{'name':'x.png','data':'eA=='}]:self.assertEqual(self.f.call('applications/'+aid+'/submit',{'version':a['version'],'body':'x','file':file})[0],400)
  self.assertEqual(self.f.call('applications/'+aid+'/submit',{'version':a['version'],'body':'x','file':{'name':'big.txt','data':base64.b64encode(b'x'*1048577).decode()}})[0],413)
  self.assertEqual(self.c.call('payments',{})[0],404)
  self.assertEqual(self.c.call('health')[1]['payment'],'unconnected')
 def test_frozen_tasks_stale_application_and_database_backup(self):
  p=self.project();self.launch(p);aid,t=self.apply(p);d=self.detail(self.c,p)
  self.assertEqual(self.c.call('projects/'+p+'/split',{'task':t,'version':d['project']['version']})[0],409)
  a=self.app(self.c,p);self.status(p,'担当')
  self.assertEqual(self.c.call('applications/'+aid+'/status',{'version':a['version'],'status':'担当'})[0],409)
  backup=Path(self.tmp.name)/'backup.sqlite3'
  with server.connect() as c,closing(sqlite3.connect(backup)) as target:c.backup(target)
  with closing(sqlite3.connect(backup)) as c:self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertTrue(c.execute('SELECT 1 FROM projects WHERE id=?',(p,)).fetchone())
 def test_period_expiry_view_and_cancellation(self):
  p=self.project();self.launch(p);aid,t=self.apply(p);self.status(p,'担当')
  with server.connect() as c:c.execute('UPDATE projects SET started=1 WHERE id=?',(p,))
  self.assertEqual(self.submit(p)[0],409)
  self.assertEqual(self.f2.call('apply',{'project':p,'task':t,'experience':'架空','consent':True})[0],409)
  a=self.app(self.f,p);self.assertEqual(self.f.call('applications/'+aid+'/cancel',{'version':a['version']})[0],200)
 def test_backup_restore_command_preserves_and_refuses_overwrite(self):
  spec=importlib.util.spec_from_file_location('storage',Path(__file__).parents[1]/'storage.py');storage=importlib.util.module_from_spec(spec);spec.loader.exec_module(storage)
  p=self.project();backup=Path(self.tmp.name)/'snapshot.db';restored=Path(self.tmp.name)/'restored.db'
  storage.snapshot(self.db,backup);storage.snapshot(backup,restored)
  with closing(sqlite3.connect(restored)) as c:self.assertIsNotNone(c.execute('SELECT id FROM projects WHERE id=?',(p,)).fetchone())
  with self.assertRaises(ValueError):storage.snapshot(backup,restored)
 def test_simultaneous_project_updates_only_one_commits(self):
  p=self.project();d=self.detail(self.c,p);payload={k:d['project'][k] for k in ['title','goal','output','conditions','days','version']}
  second=Client(self.base);second.login('creator')
  with ThreadPoolExecutor(max_workers=2) as pool:
   futures=[pool.submit(client.call,'projects/'+p+'/plan',{**payload,'title':title}) for client,title in [(self.c,'編集A'),(second,'編集B')]]
   codes=sorted(f.result()[0] for f in futures)
  self.assertEqual(codes,[200,409]);self.assertEqual(self.detail(self.c,p)['project']['version'],2)
 def test_storage_failure_returns_error_not_success(self):
  old=server.DB
  try:
   server.DB=str(Path(self.tmp.name)/'missing'/'cannot.db');self.assertEqual(self.c.call('projects')[0],503)
  finally:server.DB=old
 def test_invitation_registration_role_binding_and_replay(self):
  spec=importlib.util.spec_from_file_location('accounts',Path(__file__).parents[1]/'accounts.py');accounts=importlib.util.module_from_spec(spec);spec.loader.exec_module(accounts)
  token=accounts.issue(self.db,'invited@example.test','invite','fan');client=Client(self.base)
  with server.connect() as c:self.assertNotEqual(c.execute('SELECT token FROM account_tokens WHERE email=?',('invited@example.test',)).fetchone()[0],token)
  self.assertEqual(client.call('account/activate',{'token':token,'name':'架空の招待利用者','password':'New-Password-2026!','role':'creator'})[0],200)
  self.assertEqual(client.call('account/activate',{'token':token,'name':'再利用','password':'New-Password-2026!'})[0],403)
  code,data,_=client.call('login',{'email':'invited@example.test','password':'New-Password-2026!'});self.assertEqual(code,200);self.assertEqual(data['user']['role'],'fan')
 def test_recovery_revokes_all_sessions_and_old_password(self):
  spec=importlib.util.spec_from_file_location('accounts',Path(__file__).parents[1]/'accounts.py');accounts=importlib.util.module_from_spec(spec);spec.loader.exec_module(accounts)
  # Use a new fixture user so seeded test account passwords remain unchanged.
  token=accounts.issue(self.db,'recover@example.test','invite','creator');client=Client(self.base)
  self.assertEqual(client.call('account/activate',{'token':token,'name':'架空の復旧対象','password':'Before-Recovery-2026!'})[0],200)
  code,d,_=client.call('login',{'email':'recover@example.test','password':'Before-Recovery-2026!'});self.assertEqual(code,200);client.csrf=d['csrf']
  reset=accounts.issue(self.db,'recover@example.test','reset')
  self.assertEqual(Client(self.base).call('account/reset',{'token':reset,'password':'After-Recovery-2026!'})[0],200)
  self.assertEqual(client.call('me')[0],401)
  self.assertEqual(client.call('login',{'email':'recover@example.test','password':'Before-Recovery-2026!'})[0],401)
  self.assertEqual(client.call('login',{'email':'recover@example.test','password':'After-Recovery-2026!'})[0],200)
 def test_invite_expiry_wrong_kind_and_weak_password(self):
  spec=importlib.util.spec_from_file_location('accounts',Path(__file__).parents[1]/'accounts.py');accounts=importlib.util.module_from_spec(spec);spec.loader.exec_module(accounts)
  token=accounts.issue(self.db,'expire@example.test','invite')
  client=Client(self.base);self.assertEqual(client.call('account/reset',{'token':token,'password':'Valid-Password-2026!'})[0],403)
  self.assertEqual(client.call('account/activate',{'token':token,'name':'架空','password':'short'})[0],400)
  with server.connect() as c:c.execute('UPDATE account_tokens SET expires=1 WHERE email=?',('expire@example.test',))
  self.assertEqual(client.call('account/activate',{'token':token,'name':'架空','password':'Valid-Password-2026!'})[0],403)
 def test_account_tokens_reject_foreign_origin_and_cli_unknown_reset(self):
  self.assertEqual(Client(self.base).call('account/activate',{'token':'bad','password':'Valid-Password-2026!'},headers={'Origin':'https://evil.example'})[0],403)
  spec=importlib.util.spec_from_file_location('accounts',Path(__file__).parents[1]/'accounts.py');accounts=importlib.util.module_from_spec(spec);spec.loader.exec_module(accounts)
  with self.assertRaises(ValueError):accounts.issue(self.db,'unknown@example.test','reset')
if __name__=='__main__':unittest.main(verbosity=2)
