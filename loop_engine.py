"""Persistent human-in-the-loop goal engine. No autonomous external execution."""
import json,secrets,time
STATES=['進行中','根拠不足','ユーザー回答待ち','実行待ち','結果確認待ち','確認済み完了']
class LoopError(Exception):
 def __init__(self,message,status=400):self.message=message;self.status=status
def check(ok,message,status=400):
 if not ok:raise LoopError(message,status)
def txt(v,n=2000):check(isinstance(v,str)and v.strip()and len(v)<=n,'必須の内容・文字数を確認してください。');return v.strip()
def uid():return secrets.token_hex(12)
def now():return int(time.time())
def schema(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS goal_loops(project TEXT PRIMARY KEY REFERENCES projects(id),purpose TEXT NOT NULL,acceptance TEXT NOT NULL,status TEXT NOT NULL,version INTEGER DEFAULT 1,cycles INTEGER DEFAULT 0,stalls INTEGER DEFAULT 0,pause_reason TEXT DEFAULT '',result TEXT DEFAULT '',evidence TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS task_loops(task TEXT PRIMARY KEY REFERENCES tasks(id),project TEXT REFERENCES projects(id),acceptance TEXT NOT NULL,state TEXT NOT NULL,analogy TEXT,result TEXT DEFAULT '',evidence TEXT DEFAULT '',source_application TEXT REFERENCES applications(id));
CREATE TABLE IF NOT EXISTS loop_dialogue(id TEXT PRIMARY KEY,project TEXT REFERENCES projects(id),task TEXT,kind TEXT NOT NULL,body TEXT NOT NULL,answered_by TEXT,created INTEGER NOT NULL);
''')
 if 'source_application'not in [x[1]for x in c.execute('PRAGMA table_info(task_loops)')]:c.execute('ALTER TABLE task_loops ADD COLUMN source_application TEXT REFERENCES applications(id)')
def note(c,pid,task,kind,body):
 id=uid();c.execute('INSERT INTO loop_dialogue VALUES(?,?,?,?,?,?,?)',(id,pid,task,kind,body,None,now()));return id
def sync(c,p):
 c.execute('INSERT OR IGNORE INTO goal_loops(project,purpose,acceptance,status) VALUES(?,?,?,?)',(p['id'],p['goal'],p['output'],'定義中'))
 for t in c.execute('SELECT * FROM tasks WHERE project=?',(p['id'],)).fetchall():
  if not c.execute('SELECT 1 FROM task_loops WHERE task=?',(t['id'],)).fetchone():
   c.execute('INSERT INTO task_loops(task,project,acceptance,state) VALUES(?,?,?,?)',(t['id'],p['id'],t['output'],'ユーザー回答待ち'))
   note(c,p['id'],t['id'],'question',f'「{t["title"]}」は何が確認できたら達成ですか。必要な条件や制約を教えてください。')
def read(c,p):
 sync(c,p)
 return {'run':dict(c.execute('SELECT * FROM goal_loops WHERE project=?',(p['id'],)).fetchone()),'tasks':[dict(r)for r in c.execute('SELECT l.*,t.title,t.parent,t.depth,t.role FROM task_loops l JOIN tasks t ON t.id=l.task WHERE l.project=? ORDER BY t.rowid',(p['id'],))],'dialogue':[dict(r)for r in c.execute('SELECT * FROM loop_dialogue WHERE project=? ORDER BY rowid',(p['id'],))],'connections':{'ai':'未接続','search':'未接続','execution':'ユーザー/担当者の手動実行。自動送信・支払なし'},'limits':{'cycles':60,'depth':4,'nodes':31}}
def parent_uncomplete(c,pid,tid):
 while tid:
  c.execute("UPDATE task_loops SET state='結果確認待ち',result='',evidence='' WHERE task=? AND state='確認済み完了'",(tid,))
  t=c.execute('SELECT parent FROM tasks WHERE id=?',(tid,)).fetchone();tid=t['parent'] if t else None
 c.execute("UPDATE goal_loops SET status=CASE WHEN status='目的達成' THEN '進行中' ELSE status END,result='',evidence='' WHERE project=?",(pid,))
def no_answers(c,pid,tid):return not c.execute("SELECT 1 FROM loop_dialogue WHERE project=? AND task IS ? AND kind='question' AND answered_by IS NULL",(pid,tid)).fetchone()
def invalidate_application(c,aid):
 for t in c.execute('SELECT task,project FROM task_loops WHERE source_application=?',(aid,)).fetchall():
  parent_uncomplete(c,t['project'],t['task']);c.execute("UPDATE task_loops SET state='結果確認待ち',result='',evidence='',source_application=NULL WHERE task=?",(t['task'],));c.execute('UPDATE goal_loops SET version=version+1 WHERE project=?',(t['project'],));note(c,t['project'],t['task'],'source-withdrawn','参照したファン成果が取消されました。代替証拠を本人が確認するまで未完了です。')
def act(c,p,d,u,action):
 pid=p['id'];sync(c,p);r=c.execute('SELECT * FROM goal_loops WHERE project=?',(pid,)).fetchone()
 check(type(d.get('version'))is int and d['version']==r['version'],'別の操作で進捗が更新されました。再読込してください。',409)
 if action=='pause':
  reason=txt(d.get('reason'));c.execute("UPDATE goal_loops SET status='停止中',pause_reason=?,version=version+1 WHERE project=?",(reason,pid));note(c,pid,None,'pause',reason);return {'ok':True}
 if action=='resume':
  check(r['status']=='停止中','停止中の企画ではありません。',409);reason=txt(d.get('reason'));c.execute("UPDATE goal_loops SET status='進行中',pause_reason='',cycles=CASE WHEN cycles>=60 THEN 0 ELSE cycles END,stalls=0,version=version+1 WHERE project=?",(pid,));note(c,pid,None,'policy','再開・次の判断枠：'+reason);return {'ok':True}
 if action=='policy':
  note(c,pid,d.get('task'),'policy',txt(d.get('reason')));c.execute('UPDATE goal_loops SET stalls=0,version=version+1 WHERE project=?',(pid,));return {'ok':True}
 if action in ['answer','ask']:
  tid=d.get('task');check(tid is None or c.execute('SELECT 1 FROM tasks WHERE id=? AND project=?',(tid,pid)).fetchone(),'タスクがありません。',404)
  body=txt(d.get('body'))
  if action=='ask':
   check(r['status']!='目的達成','達成済みです。再オープンしてから質問してください。',409);note(c,pid,tid,'question',body)
   if tid:c.execute("UPDATE task_loops SET state='ユーザー回答待ち' WHERE task=?",(tid,))
  else:
   q=c.execute("SELECT * FROM loop_dialogue WHERE id=? AND project=? AND task IS ? AND kind='question' AND answered_by IS NULL",(d.get('question'),pid,tid)).fetchone();check(q,'未回答の質問を選んでください。',409)
   answer=note(c,pid,tid,'answer',body);c.execute('UPDATE loop_dialogue SET answered_by=? WHERE id=?',(answer,q['id']))
   if tid and no_answers(c,pid,tid):c.execute("UPDATE task_loops SET state='進行中' WHERE task=?",(tid,))
  if tid:parent_uncomplete(c,pid,tid)
  c.execute('UPDATE goal_loops SET version=version+1 WHERE project=?',(pid,));return {'ok':True}
 check(r['status']!='停止中','停止中です。理由と方針を記録して再開してください。',409)
 if r['cycles']>=60:
  c.execute("UPDATE goal_loops SET status='停止中',pause_reason='60判断サイクルの上限',version=version+1 WHERE project=?",(pid,));note(c,pid,None,'pause','60判断サイクルで停止。方針を見直して再開してください。');return {'ok':False,'stopped':True,'message':'判断サイクル上限で停止しました。'}
 check(r['status']!='目的達成' or action=='reopen','目的達成済みです。再オープンしてから変更してください。',409)
 if action=='define':
  purpose,acceptance=txt(d.get('purpose'),500),txt(d.get('acceptance'),500)
  check(not c.execute('SELECT 1 FROM applications WHERE project=?',(pid,)).fetchone()or(purpose==p['goal']and acceptance==p['output']),'応募後の目的/成果条件の変更は別企画で行ってください。',409)
  c.execute("UPDATE goal_loops SET purpose=?,acceptance=?,status='進行中',result='',evidence='' WHERE project=?",(purpose,acceptance,pid));c.execute('UPDATE projects SET goal=?,output=?,version=version+1 WHERE id=?',(purpose,acceptance,pid));note(c,pid,None,'definition','目的：'+purpose+'／達成条件：'+acceptance)
 elif action=='goal_verify':
  check(r['status']=='進行中','まず目的と達成条件を対話で定義してください。',409)
  check(no_answers(c,pid,None),'目的について未回答の質問があります。',409)
  check(not c.execute("SELECT 1 FROM task_loops WHERE project=? AND state!='確認済み完了'",(pid,)).fetchone(),'未完了のタスクがあります。親も独立して確認してください。',409)
  check(d.get('confirmed') is True,'目的達成条件を本人が確認してください。');result,evidence=txt(d.get('result')),txt(d.get('evidence'));c.execute("UPDATE goal_loops SET status='目的達成',result=?,evidence=? WHERE project=?",(result,evidence,pid));note(c,pid,None,'goal-confirmed','本人確認：'+result+'／証拠：'+evidence)
 else:
  tid=d.get('task');t=c.execute('SELECT t.*,l.state,l.acceptance,l.analogy,l.result,l.evidence FROM tasks t JOIN task_loops l ON l.task=t.id WHERE t.id=? AND t.project=?',(tid,pid)).fetchone();check(t,'タスクがありません。',404)
  parent_uncomplete(c,pid,t['parent'])
  check(r['status']!='定義中','まず目的と最終達成条件を定義してください。',409)
  if action=='criteria':
   acceptance=txt(d.get('acceptance'));c.execute("UPDATE task_loops SET acceptance=?,state='進行中',result='',evidence='' WHERE task=?",(acceptance,tid));note(c,pid,tid,'criteria',acceptance)
   # An explicit criterion definition answers the initial criterion question only.
   for q in c.execute("SELECT id,body FROM loop_dialogue WHERE task=? AND kind='question' AND answered_by IS NULL",(tid,)).fetchall():
    if q['body'].endswith('必要な条件や制約を教えてください。'):c.execute('UPDATE loop_dialogue SET answered_by=? WHERE id=?',(note(c,pid,tid,'answer',acceptance),q['id']))
  elif action=='search':
   # Provider boundary: no network, billing, invented result, or automatic execution.
   note(c,pid,tid,'search-unconnected','検索/AIは未接続。外部探索は実施していません。');note(c,pid,tid,'question','手元に使える事例はありますか。なければ条件変更または分解する仕事を教えてください。');c.execute("UPDATE task_loops SET state='ユーザー回答待ち' WHERE task=?",(tid,))
  elif action=='analogy':
   check(no_answers(c,pid,tid),'未回答の質問に先に答えてください。',409)
   ref=d.get('analogy');check(isinstance(ref,dict),'アナロジーの検討内容が必要です。')
   from urllib.parse import urlparse
   url=txt(ref.get('url'),1000);parsed=urlparse(url);check(parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password,'出典はhttpsのURLを使ってください。')
   data={k:txt(ref.get(k))for k in ['title','basis','similarity','difference','application']};data['url']=url;data['usable']=d.get('usable') is True
   c.execute('UPDATE task_loops SET analogy=?,state=?,result=\'\',evidence=\'\' WHERE task=?',(json.dumps(data,ensure_ascii=False),'実行待ち'if data['usable']else'根拠不足',tid));note(c,pid,tid,'analogy',json.dumps(data,ensure_ascii=False))
   c.execute('UPDATE goal_loops SET stalls=CASE WHEN ? THEN 0 ELSE stalls+1 END WHERE project=?',(data['usable'],pid))
  elif action=='insufficient':
   reason=txt(d.get('reason'));c.execute("UPDATE task_loops SET state='根拠不足',analogy=NULL,result='',evidence='' WHERE task=?",(tid,));note(c,pid,tid,'insufficient',reason);c.execute('UPDATE goal_loops SET stalls=stalls+1 WHERE project=?',(pid,))
  elif action=='split':
   check(t['state']=='根拠不足','アナロジーが使えない理由を記録してから分解します。',409)
   check(not c.execute('SELECT 1 FROM tasks WHERE parent=?',(tid,)).fetchone(),'分解済みです。子タスクを検討してください。',409)
   children=d.get('children');check(isinstance(children,list)and 2<=len(children)<=4,'2〜4件の具体的な子タスクを定義してください。')
   total=c.execute('SELECT COUNT(*) FROM tasks WHERE project=?',(pid,)).fetchone()[0]
   if t['depth']>=4 or total+len(children)>31:
    c.execute("UPDATE goal_loops SET status='停止中',pause_reason='分解の深さ/全ノード上限' WHERE project=?",(pid,));note(c,pid,tid,'question','分解上限です。目的・条件の変更または別企画への切り分けを検討してください。')
   else:
    for child in children:
     check(isinstance(child,dict),'子タスクの形式を確認してください。');id=uid();title,acceptance=txt(child.get('title'),80),txt(child.get('acceptance'),500);c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)',(id,pid,tid,t['depth']+1,title,t['role'],acceptance,None));c.execute('INSERT INTO task_loops(task,project,acceptance,state) VALUES(?,?,?,?)',(id,pid,acceptance,'進行中'));note(c,pid,id,'criteria',acceptance)
    c.execute("UPDATE task_loops SET state='進行中' WHERE task=?",(tid,));c.execute('UPDATE projects SET launched=0,version=version+1 WHERE id=?',(pid,));note(c,pid,tid,'split','子タスクを定義。募集は再確認待ち。子完了だけで親は完了しません。')
  elif action=='execute':
   children=c.execute('SELECT id FROM tasks WHERE parent=?',(tid,)).fetchall()
   if children:check(all(c.execute('SELECT state FROM task_loops WHERE task=?',(x['id'],)).fetchone()[0]=='確認済み完了'for x in children),'子タスクが未完了です。',409)
   else:check(t['state']=='実行待ち' and t['analogy'] and json.loads(t['analogy'])['usable'],'使えるアナロジーを検討してから実行結果を記録してください。',409)
   check(no_answers(c,pid,tid),'未回答の質問があります。',409)
   result,evidence=txt(d.get('result')),txt(d.get('evidence'))
   aid=d.get('application')
   if aid:check(c.execute("SELECT 1 FROM applications WHERE id=? AND project=? AND task=? AND status='確認済'",(aid,pid,tid)).fetchone(),'対応タスクの本人確認済み成果だけ参照できます。',409);evidence+='／確認済みファン成果ID：'+aid
   c.execute("UPDATE task_loops SET state='結果確認待ち',result=?,evidence=?,source_application=? WHERE task=?",(result,evidence,aid or None,tid));note(c,pid,tid,'execution','手動実行の申告：'+result+'／証拠：'+evidence)
  elif action=='verify':
   check(t['state']=='結果確認待ち' and t['result'] and t['evidence'],'結果と証拠を記録してから確認してください。',409);check(no_answers(c,pid,tid),'未回答の質問があります。',409);check(d.get('confirmed')is True,'達成条件を本人が確認してください。')
   check(not c.execute("SELECT 1 FROM tasks t JOIN task_loops l ON l.task=t.id WHERE t.parent=? AND l.state!='確認済み完了'",(tid,)).fetchone(),'未完了の子タスクがあります。',409)
   note(c,pid,tid,'confirmed','達成条件への本人確認：'+txt(d.get('comment')));c.execute("UPDATE task_loops SET state='確認済み完了' WHERE task=?",(tid,));c.execute('UPDATE goal_loops SET stalls=0 WHERE project=?',(pid,))
   if t['parent']:
    pending=c.execute("SELECT 1 FROM tasks t JOIN task_loops l ON l.task=t.id WHERE t.parent=? AND l.state!='確認済み完了'",(t['parent'],)).fetchone()
    if not pending:c.execute("UPDATE task_loops SET state='結果確認待ち',result='',evidence='' WHERE task=?",(t['parent'],));note(c,pid,t['parent'],'note','子は完了。親の達成条件の証拠を独立して確認してください。')
  elif action=='reopen':
   reason=txt(d.get('reason'));parent_uncomplete(c,pid,tid);c.execute("UPDATE task_loops SET state='進行中',result='',evidence='' WHERE task=?",(tid,));note(c,pid,tid,'reopened',reason)
  else:raise LoopError('未対応の操作です。',404)
 c.execute('UPDATE goal_loops SET version=version+1,cycles=cycles+1 WHERE project=?',(pid,))
 current=c.execute('SELECT * FROM goal_loops WHERE project=?',(pid,)).fetchone()
 if current['stalls']>=3:
  c.execute("UPDATE goal_loops SET status='停止中',pause_reason='3回進展なし・方針確認待ち' WHERE project=?",(pid,));note(c,pid,None,'question','3回進展していません。条件変更・別の事例・さらに分解・専門家への確認のどれで進めますか。')
 return {'ok':True}
