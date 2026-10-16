"""Durable assignments and bounded event jobs. No LLM/provider connection."""
import json,secrets,time
ROLES={'coordinator':'統括・ユーザー対話','decomposer':'タスク分解','analogy':'アナロジー探索・適用説明','task':'タスク担当'}
class AgentError(Exception):
 def __init__(self,message,status=400):self.message=message;self.status=status
def check(ok,message,status=400):
 if not ok:raise AgentError(message,status)
def schema(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY,project TEXT REFERENCES projects(id),task TEXT,role TEXT NOT NULL,parent_task TEXT,state TEXT NOT NULL,calls INTEGER DEFAULT 0,version INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS agent_jobs(id TEXT PRIMARY KEY,agent TEXT REFERENCES agents(id),project TEXT REFERENCES projects(id),task TEXT,event_key TEXT NOT NULL,status TEXT NOT NULL,input TEXT NOT NULL,result TEXT DEFAULT '',attempts INTEGER DEFAULT 0,lease TEXT,lease_until INTEGER DEFAULT 0,created INTEGER NOT NULL,UNIQUE(project,event_key));
CREATE TABLE IF NOT EXISTS agent_events(id INTEGER PRIMARY KEY,job TEXT REFERENCES agent_jobs(id),action TEXT NOT NULL,message TEXT NOT NULL,created INTEGER NOT NULL);''')
def event(c,j,action,message):c.execute('INSERT INTO agent_events(job,action,message,created)VALUES(?,?,?,?)',(j,action,message,int(time.time())))
def sync(c,p):
 for role in ['coordinator','decomposer','analogy']:c.execute('INSERT OR IGNORE INTO agents(id,project,task,role,parent_task,state)VALUES(?,?,?,?,?,?)',(p['id']+':'+role,p['id'],None,role,None,'待機・AI未接続'))
 for t in c.execute('SELECT id,parent FROM tasks WHERE project=?',(p['id'],)).fetchall():c.execute('INSERT OR IGNORE INTO agents(id,project,task,role,parent_task,state)VALUES(?,?,?,?,?,?)',(p['id']+':task:'+t['id'],p['id'],t['id'],'task',t['parent'],'待機・AI未接続'))
def read(c,pid):return {'assignments':[dict(r)for r in c.execute('SELECT * FROM agents WHERE project=? ORDER BY rowid',(pid,))],'jobs':[dict(r)for r in c.execute('SELECT * FROM agent_jobs WHERE project=? ORDER BY rowid DESC',(pid,))],'events':[dict(r)for r in c.execute('SELECT e.* FROM agent_events e JOIN agent_jobs j ON e.job=j.id WHERE j.project=? ORDER BY e.id',(pid,))],'limits':{'parallel':2,'queued':20,'total_jobs':60,'actual_model_calls':20,'attempts':3},'provider':'未接続・実AI呼出0。役割とジョブの永続化のみ'}
def enqueue(c,p,role,task,key,reason):
 sync(c,p);check(role in ROLES,'未定義の役割です。');check(isinstance(key,str)and 1<=len(key)<=100,'重複防止用のイベントIDが必要です。')
 old=c.execute('SELECT id,status FROM agent_jobs WHERE project=? AND event_key=?',(p['id'],key)).fetchone()
 if old:return {'id':old['id'],'status':old['status'],'duplicate':True}
 if task:check(c.execute('SELECT 1 FROM tasks WHERE id=? AND project=?',(task,p['id'])).fetchone(),'別企画のタスクには担当を置けません。',403)
 check(role!='task' or task,'タスク担当にはタスクIDが必要です。')
 check(c.execute('SELECT COUNT(*)FROM agent_jobs WHERE project=?',(p['id'],)).fetchone()[0]<60,'ジョブ総数60の上限です。方針を見直してください。',409)
 check(c.execute("SELECT COUNT(*)FROM agent_jobs WHERE project=? AND status IN ('queued','running')",(p['id'],)).fetchone()[0]<20,'待機ジョブ20件の上限です。',409)
 aid=p['id']+':'+role+(':'+task if role=='task'else'');jid=secrets.token_hex(12)
 t=c.execute('SELECT t.id,t.parent,t.title,l.acceptance,l.state,l.analogy FROM tasks t LEFT JOIN task_loops l ON l.task=t.id WHERE t.id=?',(task,)).fetchone()if task else None
 goal=c.execute('SELECT purpose,acceptance,version FROM goal_loops WHERE project=?',(p['id'],)).fetchone()
 payload={'role':role,'project':p['id'],'task':dict(t)if t else None,'goal':goal['purpose']if goal else p['goal'],'output':goal['acceptance']if goal else p['output'],'reason':reason,'authority':'提案・質問のみ。完了/権限/外部送信/支払を自動決定しない'}
 scope=None if role=='coordinator'else task
 history=c.execute('SELECT task,kind,body,created FROM loop_dialogue WHERE project=? AND (? OR task IS ? OR task IS NULL) ORDER BY rowid DESC LIMIT 20',(p['id'],role=='coordinator',scope)).fetchall()
 import tool_connections
 payload['basis_versions']={'project':c.execute('SELECT version FROM projects WHERE id=?',(p['id'],)).fetchone()[0],'goal':goal['version']if goal else None}
 payload['tool_connection']=tool_connections.assigned(c,p['owner'],role)
 payload['dialogue']=[{**dict(r),'body':r['body'][:1000]}for r in reversed(history)]
 payload['task_tree']=[dict(r)for r in c.execute('SELECT t.id,t.parent,l.state,l.acceptance FROM tasks t LEFT JOIN task_loops l ON l.task=t.id WHERE t.project=? AND (? OR t.id=? OR t.parent=?)',(p['id'],role in ['coordinator','decomposer'],task,task))]
 c.execute('INSERT INTO agent_jobs(id,agent,project,task,event_key,status,input,created)VALUES(?,?,?,?,?,?,?,?)',(jid,aid,p['id'],task,key,'queued',json.dumps(payload,ensure_ascii=False),int(time.time())));event(c,jid,'queued','担当と入力を保存。AI処理はまだ実行していない。');return {'id':jid,'status':'queued','duplicate':False}
def claim(c,pid):
 now=int(time.time())
 for j in c.execute("SELECT * FROM agent_jobs WHERE project=? AND status='running' AND lease_until<=?",(pid,now)).fetchall():
  status='queued'if j['attempts']<3 else'failed';c.execute('UPDATE agent_jobs SET status=?,lease=NULL,lease_until=0 WHERE id=?',(status,j['id']));event(c,j['id'],'lease-expired',status)
 run=c.execute('SELECT status FROM goal_loops WHERE project=?',(pid,)).fetchone()
 if run and run['status'] in ['停止中','目的達成']:return None
 if c.execute("SELECT COUNT(*)FROM agent_jobs WHERE project=? AND status='running'",(pid,)).fetchone()[0]>=2:return None
 if c.execute('SELECT COALESCE(SUM(calls),0)FROM agents WHERE project=?',(pid,)).fetchone()[0]>=20:return None
 j=c.execute("SELECT * FROM agent_jobs WHERE project=? AND status='queued' ORDER BY rowid LIMIT 1",(pid,)).fetchone()
 if not j:return None
 lease=secrets.token_hex(16);changed=c.execute("UPDATE agent_jobs SET status='running',attempts=attempts+1,lease=?,lease_until=? WHERE id=? AND status='queued'",(lease,now+60,j['id'])).rowcount
 if not changed:return None
 c.execute("UPDATE agents SET state='起動準備',version=version+1 WHERE id=?",(j['agent'],));event(c,j['id'],'claimed','60秒のリース取得');return dict(c.execute('SELECT * FROM agent_jobs WHERE id=?',(j['id'],)).fetchone())
class UnconnectedProvider:
 def invoke(self,job):return {'status':'blocked_connector','message':'AI/検索プロバイダ未接続。推論・検索・タスク遂行は実施していません。'}
def tick(c,pid):
 processed=[]
 for _ in range(2):
  j=claim(c,pid)
  if not j:break
  result=UnconnectedProvider().invoke(j)
  c.execute("UPDATE agent_jobs SET status='blocked_connector',result=?,lease=NULL,lease_until=0 WHERE id=? AND status='running' AND lease=?",(json.dumps(result,ensure_ascii=False),j['id'],j['lease']));c.execute("UPDATE agents SET state='接続待ち・実AI未稼働',version=version+1 WHERE id=?",(j['agent'],));event(c,j['id'],'blocked_connector',result['message']);processed.append(j['id'])
 return {'processed':processed,'model_calls':0,'provider':'未接続'}
def cancel(c,pid,jid):
 j=c.execute('SELECT * FROM agent_jobs WHERE id=? AND project=?',(jid,pid)).fetchone();check(j,'ジョブがありません。',404)
 c.execute("UPDATE agent_jobs SET status='cancelled',lease=NULL,lease_until=0 WHERE id=?",(jid,));c.execute("UPDATE agents SET state='待機・ジョブ取消済み',version=version+1 WHERE id=?",(j['agent'],));event(c,jid,'cancelled','ユーザー取消');return {'ok':True}
def cancel_all(c,pid,reason):
 for j in c.execute("SELECT id FROM agent_jobs WHERE project=? AND status IN ('queued','running','blocked_connector')",(pid,)).fetchall():cancel(c,pid,j['id'])

def mock_result(c,p,jid):
 """Advisory fixture only: no real provider callback or grant is accepted."""
 import proposals
 check(isinstance(jid,str)and len(jid)==24 and all(x in '0123456789abcdef'for x in jid),'担当ジョブIDを確認してください。')
 job=c.execute('SELECT j.*,a.role FROM agent_jobs j JOIN agents a ON a.id=j.agent WHERE j.id=?AND j.project=?',(jid,p['id'])).fetchone();check(job,'担当ジョブがありません。',404)
 check(job['status']in ['queued','blocked_connector'],'取消・実行中・模擬済みのジョブは変更できません。',409)
 run=c.execute('SELECT version,status FROM goal_loops WHERE project=?',(p['id'],)).fetchone();check(run and run['status']not in ['停止中','目的達成'],'停止中・達成済みでは提案を作りません。',409)
 payload=json.loads(job['input']);basis=payload.get('basis_versions');check(basis=={'project':p['version'],'goal':run['version']},'元の目的・タスクが更新されています。新しい担当依頼を作ってください。',409)
 import tool_connections
 check(payload.get('tool_connection')==tool_connections.assigned(c,p['owner'],job['role']),'担当の接続候補・設定が更新されています。新しい担当依頼を作ってください。',409)
 value=proposals.mock(job['role']);result={'mode':'mock_only','realModelCalls':0,'executed':False,'proposal':value,'basis_versions':basis,'requiresHumanReview':True}
 c.execute("UPDATE agent_jobs SET status='mock_result',result=?,lease=NULL,lease_until=0 WHERE id=?",(json.dumps(result,ensure_ascii=False),jid));event(c,jid,'mock-result','形式検証済みの模擬提案。実AI/検索/CLI未稼働。タスク・親・目的の完了状態は変えない。')
 return result
