"""One-job owner grants and worker APIs. Feature disabled unless operator enables it.
No provider connections, local processes or secret files. All callers use one DB transaction.
"""
import hashlib,hmac,json,secrets,time,re
import proposals,tool_connections
class Denied(ValueError):
 def __init__(self,message='CLI request rejected',status=409):self.message=message;self.status=status
def need(ok,status=409):
 if not ok:raise Denied(status=status)
def digest(v):return hashlib.sha256(v.encode()).hexdigest()
def schema(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS cli_challenges(id TEXT PRIMARY KEY,owner TEXT NOT NULL,project TEXT NOT NULL,job TEXT NOT NULL,nonce_hash TEXT NOT NULL,origin TEXT NOT NULL,workspace TEXT NOT NULL,expires INTEGER NOT NULL,used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cli_grants(id TEXT PRIMARY KEY,owner TEXT NOT NULL,project TEXT NOT NULL,job TEXT NOT NULL,secret_hash TEXT NOT NULL,origin TEXT NOT NULL,workspace TEXT NOT NULL,expires INTEGER NOT NULL,revoked INTEGER DEFAULT 0,claimed INTEGER DEFAULT 0,fixture INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS cli_runs(job TEXT PRIMARY KEY,grant_id TEXT NOT NULL,owner TEXT NOT NULL,lease_hash TEXT NOT NULL,state TEXT NOT NULL,basis TEXT NOT NULL,context_hash TEXT NOT NULL,started INTEGER NOT NULL,deadline INTEGER NOT NULL,input_tokens INTEGER,output_tokens INTEGER,usage_source TEXT NOT NULL,reply_hash TEXT);
CREATE TABLE IF NOT EXISTS cli_audit(id INTEGER PRIMARY KEY,job TEXT NOT NULL,owner TEXT NOT NULL,action TEXT NOT NULL,created INTEGER NOT NULL);''')
def audit(c,j,u,a):c.execute('INSERT INTO cli_audit(job,owner,action,created)VALUES(?,?,?,?)',(j,u,a,int(time.time())))
def owned(c,owner,pid,jid):
 p=c.execute('SELECT *FROM projects WHERE id=?AND owner=?',(pid,owner)).fetchone();need(p,403)
 j=c.execute('SELECT j.*,a.role FROM agent_jobs j JOIN agents a ON a.id=j.agent WHERE j.id=?AND j.project=?',(jid,pid)).fetchone();need(j,404)
 return p,j
def fresh(c,p,j):
 r=c.execute('SELECT version,status FROM goal_loops WHERE project=?',(p['id'],)).fetchone();need(r and r['status']not in ['停止中','目的達成']);payload=json.loads(j['input']);v={'project':p['version'],'goal':r['version']};need(payload.get('basis_versions')==v);need(j['role']in proposals.ROLES)
 tool=tool_connections.assigned(c,p['owner'],j['role']);need(payload.get('tool_connection')==tool)
 if payload.get('engine')=='opencode':
  import opencode_models
  need(payload.get('model_selection')==opencode_models.selection(c,p['owner'],p['id'],j['role'])and payload.get('model_selection'))
 else:need(tool and tool['tool']=='codex-cli'and tool['state']!='取消')
 return v,payload

def prepare(c,u,pid,jid,origin,root,now=None):
 now=int(time.time())if now is None else now;p,j=owned(c,u,pid,jid);fresh(c,p,j);need(j['status']in ['queued','blocked_connector']);need(isinstance(origin,str)and origin.startswith(('https://','http://127.0.0.1:'))and len(origin)<300,400);root=tool_connections.workspace(root)
 ident=secrets.token_hex(12);nonce=secrets.token_urlsafe(32);c.execute('INSERT INTO cli_challenges(id,owner,project,job,nonce_hash,origin,workspace,expires)VALUES(?,?,?,?,?,?,?,?)',(ident,u,pid,jid,digest(nonce),origin,root,now+60));audit(c,jid,u,'challenge');return {'challenge':ident,'nonce':nonce,'expires':now+60,'scope':'one coordinator proposal, no file/command/search authority'}
def pair(c,u,pid,d,fixture=False,now=None):
 now=int(time.time())if now is None else now;need(set(d)=={'challenge','nonce','confirmed'},400);need(d['confirmed']is True,403)
 r=c.execute('SELECT *FROM cli_challenges WHERE id=?AND owner=?AND project=?',(d['challenge'],u,pid)).fetchone();need(r and not r['used']and r['expires']>now,403);need(type(d['nonce'])is str and hmac.compare_digest(r['nonce_hash'],digest(d['nonce'])),403);p,j=owned(c,u,pid,r['job']);fresh(c,p,j);need(j['status']in ['queued','blocked_connector']);need(not c.execute('SELECT 1 FROM cli_grants WHERE job=?AND revoked=0 AND expires>?',(j['id'],now)).fetchone())
 c.execute('UPDATE cli_challenges SET used=1 WHERE id=?',(r['id'],));gid=secrets.token_hex(12);secret=secrets.token_urlsafe(32);c.execute('INSERT INTO cli_grants(id,owner,project,job,secret_hash,origin,workspace,expires,fixture)VALUES(?,?,?,?,?,?,?,?,?)',(gid,u,pid,j['id'],digest(secret),r['origin'],r['workspace'],now+300,int(fixture)));audit(c,j['id'],u,'paired-fixture'if fixture else'paired');return {'grant':gid,'secret':secret,'expires':now+300,'job':j['id'],'workspace':r['workspace'],'maxDispatches':1}
def auth(c,token,origin,now):
 need(type(token)is str and 40<=len(token)<=100,403);g=c.execute('SELECT *FROM cli_grants WHERE secret_hash=?',(digest(token),)).fetchone();need(g and not g['revoked']and g['expires']>now and g['origin']==origin,403);return g
def revoke(c,u,pid,gid):
 g=c.execute('SELECT *FROM cli_grants WHERE id=?AND owner=?AND project=?',(gid,u,pid)).fetchone();need(g,404);c.execute('UPDATE cli_grants SET revoked=1 WHERE id=?',(gid,));c.execute("UPDATE cli_runs SET state='cancelled'WHERE job=?AND state='running'",(g['job'],));c.execute("UPDATE agent_jobs SET status='cancelled',lease=NULL,lease_until=0 WHERE id=?AND status='cli_running'",(g['job'],));audit(c,g['job'],u,'revoked');return {'ok':True}
def context(payload):
 d={'goal':payload['goal'],'acceptance':payload['output']};t=payload.get('task')
 if t:d['task']={'title':t['title'],'acceptance':t.get('acceptance')or''}
 return d
def worker(c,token,origin,action,d,now=None):
 now=int(time.time())if now is None else now;g=auth(c,token,origin,now);p,j=owned(c,g['owner'],g['project'],g['job'])
 try:v,payload=fresh(c,p,j)
 except Denied:
  if action!='poll':raise
  c.execute("UPDATE cli_runs SET state='cancelled'WHERE job=?AND state='running'",(j['id'],));c.execute("UPDATE agent_jobs SET status='cli_failed',result=?WHERE id=?AND status='cli_running'",(json.dumps({'status':'stale','message':'Input or model changed; stopped without applying proposal'}),j['id']));audit(c,j['id'],g['owner'],'stale-stopped');return {'cancelled':True,'reason':'stale'}
 if action=='claim':
  need(d=={},400);need(not g['claimed']and j['status']in ['queued','blocked_connector']);need(not c.execute('SELECT 1 FROM cli_runs WHERE job=?',(j['id'],)).fetchone());lease=secrets.token_urlsafe(32);ctx=context(payload);ctxhash=digest(json.dumps(ctx,sort_keys=True,ensure_ascii=False));basis={'versions':v,'tool':payload.get('tool_connection'),'model':payload.get('model_selection')};deadline=min(g['expires'],now+120)
  c.execute('UPDATE cli_grants SET claimed=1 WHERE id=?',(g['id'],));c.execute('INSERT INTO cli_runs(job,grant_id,owner,lease_hash,state,basis,context_hash,started,deadline,usage_source)VALUES(?,?,?,?,?,?,?,?,?,?)',(j['id'],g['id'],g['owner'],digest(lease),'running',json.dumps(basis,sort_keys=True),ctxhash,now,deadline,'fixture'if g['fixture']else'CLI-reported-unverified'));c.execute("UPDATE agent_jobs SET status='cli_running'WHERE id=?",(j['id'],));audit(c,j['id'],g['owner'],'dispatch-reserved');return {'job':j['id'],'lease':lease,'role':j['role'],'context':ctx,'contextHash':ctxhash,'basis':basis,'deadline':deadline,'fixture':bool(g['fixture']),'workspace':g['workspace'],'engine':payload.get('engine','codex-cli'),'model':payload.get('model_selection')}
 r=c.execute('SELECT *FROM cli_runs WHERE job=?AND grant_id=?',(j['id'],g['id'])).fetchone();need(r,404)
 if action=='poll':
  need(d=={},400);stop=r['state']!='running'or j['status']!='cli_running'or r['deadline']<=now
  if stop and r['state']=='running':
   state='timeout'if r['deadline']<=now else'cancelled';c.execute('UPDATE cli_runs SET state=?WHERE job=?',(state,j['id']));c.execute("UPDATE agent_jobs SET status='cli_failed'WHERE id=?AND status='cli_running'",(j['id'],));audit(c,j['id'],g['owner'],state)
  return {'cancelled':stop,'deadline':r['deadline']}
 need(action=='result'and set(d)=={'job','lease','status','proposal','usage'},400);need(d['job']==j['id'],403);need(type(d['lease'])is str and hmac.compare_digest(r['lease_hash'],digest(d['lease'])),403)
 reply=digest(json.dumps(d,sort_keys=True,ensure_ascii=False))
 if r['state']in ['accepted','failed']:
  need(r['reply_hash']==reply);return {'duplicate':True,'state':r['state']}
 need(r['state']=='running'and j['status']=='cli_running'and r['deadline']>now)
 need(json.loads(r['basis'])=={'versions':v,'tool':payload.get('tool_connection'),'model':payload.get('model_selection')});need(r['context_hash']==digest(json.dumps(context(payload),sort_keys=True,ensure_ascii=False)))
 need(d['status']in ['ok','cancelled','timeout','output_limit','process_error','free_tier_denied','model_unavailable','engine_unavailable','engine_http_error','auth_required','rate_limited','not_approved','duplicate'],400)
 usage=d['usage'];need(type(usage)is dict and set(usage)=={'input_tokens','output_tokens'}and all(x is None or type(x)is int and 0<=x<=10000000 for x in usage.values()),400)
 if d['status']=='ok':
  need(all(type(x)is int for x in usage.values()),400);value=proposals.validate(j['role'],d['proposal']);result={'mode':'fixture-process'if g['fixture']else'cli-reported','proposal':value,'requiresHumanReview':True,'realModelCalls':0 if g['fixture']else 1,'usage':usage};state='accepted';jobstate='cli_result'
 else:need(d['proposal']is None,400);result={'mode':'fixture-process'if g['fixture']else'cli-reported','status':d['status'],'message':'CLI stopped; no proposal applied'};state='failed';jobstate='cli_failed'
 c.execute('UPDATE cli_runs SET state=?,input_tokens=?,output_tokens=?,reply_hash=?WHERE job=?',(state,usage['input_tokens'],usage['output_tokens'],reply,j['id']));c.execute('UPDATE agent_jobs SET status=?,result=?WHERE id=?',(jobstate,json.dumps(result,ensure_ascii=False),j['id']));audit(c,j['id'],g['owner'],state);return {'state':state,'taskStateChanged':False,'usageSource':r['usage_source']}
def summary(c,u,pid):
 need(c.execute('SELECT 1 FROM projects WHERE id=?AND owner=?',(pid,u)).fetchone(),403)
 for r in c.execute("SELECT r.job FROM cli_runs r JOIN agent_jobs j ON j.id=r.job WHERE j.project=?AND r.owner=?AND r.state='running'AND r.deadline<=?",(pid,u,int(time.time()))).fetchall():
  c.execute("UPDATE cli_runs SET state='timeout'WHERE job=?AND state='running'",(r['job'],));c.execute("UPDATE agent_jobs SET status='cli_failed',result=?WHERE id=?AND status='cli_running'",(json.dumps({'status':'timeout','message':'Deadline exceeded; usage unknown'}),r['job']));audit(c,r['job'],u,'deadline-reaped')
 rows=c.execute('SELECT r.state,r.input_tokens,r.output_tokens,r.usage_source,r.started,r.deadline FROM cli_runs r JOIN agent_jobs j ON j.id=r.job WHERE j.project=?AND r.owner=?',(pid,u)).fetchall();return {'runs':[dict(r)for r in rows],'dispatches':len(rows),'reportedModelCalls':sum(r['state']=='accepted'and r['usage_source']!='fixture'for r in rows),'fixtureRuns':sum(r['usage_source']=='fixture'for r in rows),'usageIsProviderAttested':False}
