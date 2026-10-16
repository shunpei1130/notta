"""Saved role model choices from sanitized local inventory; no credentials or inference."""
from pathlib import Path
import json,time
import proposals
FREE_IDS={'deepseek-v4.1-flash-free','glm-5.3-flash-free'}
class Invalid(ValueError):pass
def need(ok):
 if not ok:raise Invalid('OpenCode model/selection rejected; no paid fallback')
def schema(c):c.executescript('CREATE TABLE IF NOT EXISTS oc_choices(owner TEXT NOT NULL,project TEXT NOT NULL,role TEXT NOT NULL,model TEXT NOT NULL,version INTEGER NOT NULL,PRIMARY KEY(owner,project,role));')
def inventory():
 p=Path(__file__).with_name('opencode_inventory.json');d=json.loads(p.read_text(encoding='utf-8'));return {'version':d['version'],'observedUTC':d['observedUTC'],'authenticatedProviders':d['authenticatedProviders'],'models':[{**m,'connected':False,'state':m.get('verification','カタログ確認済み・ノッタ実接続未検証')}for m in d['models']],'fallbackAllowed':False,'engineReachable':False,'realCalls':0,'lastProbe':d.get('lastProbe')}
def selection(c,u,pid,role):
 r=c.execute('SELECT role,model,version FROM oc_choices WHERE owner=?AND project=?AND role=?',(u,pid,role)).fetchone();return dict(r)if r else None
def select(c,u,pid,d):
 need(set(d)=={'role','model','version'}and d['role']in proposals.ROLES and type(d['version'])is int);need(c.execute('SELECT 1 FROM projects WHERE id=?AND owner=?',(pid,u)).fetchone());need(d['model']in FREE_IDS);old=selection(c,u,pid,d['role']);need(d['version']==(old['version']if old else 0));need(any(m['id']==d['model']and m['cost']['input']==0 and m['cost']['output']==0 for m in inventory()['models']))
 c.execute('INSERT INTO oc_choices(owner,project,role,model,version)VALUES(?,?,?,?,1)ON CONFLICT(owner,project,role)DO UPDATE SET model=excluded.model,version=oc_choices.version+1',(u,pid,d['role'],d['model']));return {'ok':True,'selection':selection(c,u,pid,d['role']),'executed':False}

def reconcile_offline(c,u,pid,now=None):
 need(c.execute('SELECT 1 FROM projects WHERE id=?AND owner=?',(pid,u)).fetchone())
 now=int(time.time())if now is None else now
 goal=c.execute('SELECT version,status FROM goal_loops WHERE project=?',(pid,)).fetchone();project=c.execute('SELECT version FROM projects WHERE id=?',(pid,)).fetchone()
 for row in c.execute("SELECT id,input,created FROM agent_jobs WHERE project=?AND status='offline_saved'",(pid,)).fetchall():
  d=json.loads(row['input']);expires=d.get('offline_expires',row['created']+86400);state=None
  if expires<=now:state='offline_expired'
  elif not goal or goal['status']in ['停止中','目的達成']or d.get('basis_versions')!={'project':project['version'],'goal':goal['version']}or d.get('model_selection')!=selection(c,u,pid,d.get('role')):state='offline_stale'
  if state:
   c.execute('UPDATE agent_jobs SET status=?,result=?WHERE id=?AND status=?',(state,json.dumps({'status':state,'message':'期限または前提変更で失効。自動実行はありません。','expires':expires},ensure_ascii=False),row['id'],'offline_saved'))
