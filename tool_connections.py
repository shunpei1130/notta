"""User-owned catalog/configuration. No executable paths, credentials or real I/O."""
import json,ntpath,re,time
from pathlib import PureWindowsPath
NAMES=[('grok-build','Grok Build CLI','cli'),('muse-code','Muse Code CLI','cli'),('deepseek-harness','DeepSeek Harness','cli'),('claude-cli','Claude Code CLI','cli'),('claude-extension','Claude Code 拡張','extension'),('codex-cli','Codex CLI','cli'),('codex-extension','Codex 拡張','extension'),('opencode','OpenCode CLI','cli'),('command-code','Command Code CLI','cli'),('pi','Pi CLI','cli'),('devin','Devin CLI','cli'),('copilot','Copilot CLI','cli'),('cursor','Cursor CLI','cli'),('antigravity','Antigravity CLI','cli')]
OFFICIAL=[('grok -p', 'https://docs.x.ai/build/overview'), ('muse exec / serve', 'https://dev.meta.ai/docs/muse-code'), ('dsh --profile headless (developer preview)', 'https://github.com/deepseek-ai/deepseek-harness'), ('claude -p / Agent SDK', 'https://code.claude.com/docs/en/headless'), ('IDEレビュー。外部Webから直接操作する公開APIは未確認', 'https://code.claude.com/docs/en/vs-code'), ('codex exec / SDK / app-server', 'https://developers.openai.com/blog/codex-as-a-platform'), ('IDE。自動化はapp-server / SDKを別に使用', 'https://learn.chatgpt.com/docs/codex/ide'), ('opencode serve / HTTP / SDK', 'https://docs.opencode.ai/docs/server/'), ('Windows: cmdc。Windows cmdシェルは使用禁止', 'https://commandcode.ai/docs/reference/cli'), ('pi -p / RPC / SDK', 'https://pi.dev/'), ('devin -p / acp', 'https://docs.devin.ai/cli'), ('copilot -p / SDK / ACP', 'https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference'), ('agent -p / agent acp。バージョン検出が必要', 'https://cursor.com/docs/cli/overview'), ('agy -p。GEMINI_API_KEY単独では不可', 'https://www.antigravity.google/docs/cli/headless/')]
CATALOG=[{'id':id,'name':name,'surface':surface,'recommended':id=='claude-cli','official':'公式方式確認済み・実機未検証','officialVerified':True,'headless':OFFICIAL[i][0],'source':OFFICIAL[i][1],'command':None,'restriction':'Claudeアカウントの第三者製品利用は事前許可または適切なAPI契約が必要。ローカル橋渡しは規約回避になりません。' if id in ['claude-cli','claude-extension','opencode'] else '本人の正式な導入・認証・権限確認が必要。実接続未検証。'}for i,(id,name,surface)in enumerate(NAMES)]
ROLES=['coordinator','decomposer','analogy','task']
class ToolError(Exception):
 def __init__(self,message,status=400):self.message=message;self.status=status
def check(ok,message,status=400):
 if not ok:raise ToolError(message,status)
def workspace(v):
 check(isinstance(v,str)and 1<=len(v)<=500 and '\x00'not in v,'workspaceの宣言を確認してください。')
 p=PureWindowsPath(v);check(p.is_absolute()and re.fullmatch('[A-Za-z]:',p.drive)and '..'not in p.parts,'絶対ローカルworkspaceのみ。UNC・相対・親参照は使えません。')
 return ntpath.normcase(ntpath.normpath(v))
def schema(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS tool_settings(owner TEXT REFERENCES users(id),tool TEXT NOT NULL,workspace TEXT NOT NULL,state TEXT NOT NULL,mock_state TEXT DEFAULT '',failure TEXT DEFAULT '',version INTEGER DEFAULT 1,PRIMARY KEY(owner,tool));
CREATE TABLE IF NOT EXISTS role_tools(owner TEXT REFERENCES users(id),role TEXT NOT NULL,tool TEXT NOT NULL,PRIMARY KEY(owner,role),FOREIGN KEY(owner,tool)REFERENCES tool_settings(owner,tool));
CREATE TABLE IF NOT EXISTS tool_events(id INTEGER PRIMARY KEY,owner TEXT REFERENCES users(id),tool TEXT,action TEXT NOT NULL,message TEXT NOT NULL,created INTEGER NOT NULL);''')
def event(c,u,t,action,message):c.execute('INSERT INTO tool_events(owner,tool,action,message,created)VALUES(?,?,?,?,?)',(u,t,action,message,int(time.time())))
def read(c,u):return {'catalog':CATALOG,'settings':[dict(r)for r in c.execute('SELECT * FROM tool_settings WHERE owner=?',(u,))],'assignments':[dict(r)for r in c.execute('SELECT role,tool FROM role_tools WHERE owner=?',(u,))],'events':[dict(r)for r in c.execute('SELECT tool,action,message,created FROM tool_events WHERE owner=? ORDER BY id',(u,))],'realVerifiedConnections':0,'boundary':'インストール・ログイン・実ペアリング・認証情報保存・常駐・コマンド実行は行いません。モック結果は実接続の証明ではありません。'}
def assigned(c,u,role):
 r=c.execute('SELECT s.*FROM role_tools r JOIN tool_settings s ON s.owner=r.owner AND s.tool=r.tool WHERE r.owner=? AND r.role=?',(u,role)).fetchone()
 return {'tool':r['tool'],'state':r['state'],'realVerified':False,'officialVerified':True}if r else None
def act(c,u,action,d):
 check(isinstance(d,dict),'JSONが必要です。')
 allowed={'register':{'tool','workspace','version'},'assign':{'tool','role'},'mock':{'tool','version','simulateFailure'},'disconnect':{'tool','version'}}
 check(action in allowed,'未対応操作です。',404);check(set(d)<=allowed[action],'任意コマンド・鍵・追加権限の入力は受け付けません。')
 tool=d.get('tool');check(tool in [x['id']for x in CATALOG],'候補カタログから選んでください。')
 r=c.execute('SELECT *FROM tool_settings WHERE owner=?AND tool=?',(u,tool)).fetchone()
 if action=='register':
  check(type(d.get('version'))is int and d['version']==(r['version']if r else 0),'別の操作で更新されました。再読込してください。',409)
  ws=workspace(d.get('workspace'));c.execute("INSERT INTO tool_settings(owner,tool,workspace,state,version)VALUES(?,?,?,'未接続',1)ON CONFLICT(owner,tool)DO UPDATE SET workspace=excluded.workspace,state='未接続',mock_state='',failure='',version=tool_settings.version+1",(u,tool,ws));event(c,u,tool,'register','workspaceは宣言だけ。アクセス許可/ペアリング未実施。');return {'ok':True}
 check(r,'先に本人所有の設定を登録してください。',404)
 if action=='assign':
  role=d.get('role');check(role in ROLES,'役割を確認してください。');check(r['state']!='失効','失効した接続は再登録してください。',409)
  c.execute('INSERT INTO role_tools VALUES(?,?,?)ON CONFLICT(owner,role)DO UPDATE SET tool=excluded.tool',(u,role,tool));event(c,u,tool,'assign',role+'へ候補割当。能力・接続・実行の許可ではない。');return {'ok':True}
 check(type(d.get('version'))is int and d['version']==r['version'],'別の操作で更新されました。',409)
 if action=='disconnect':
  c.execute("UPDATE tool_settings SET state='失効',mock_state='',failure='',version=version+1 WHERE owner=?AND tool=?",(u,tool));c.execute('DELETE FROM role_tools WHERE owner=?AND tool=?',(u,tool))
  # Jobs owned by this user's project can never keep a candidate grant after revocation.
  for j in c.execute("SELECT j.id FROM agent_jobs j JOIN projects p ON p.id=j.project WHERE p.owner=?AND j.status IN('queued','running','blocked_connector')",(u,)).fetchall():
   import agents
   project=c.execute('SELECT project FROM agent_jobs WHERE id=?',(j['id'],)).fetchone()[0];agents.cancel(c,project,j['id'])
  event(c,u,tool,'disconnect','設定を失効・役割割当を解除。待機ジョブ取消。実ブリッジの権限は未作成。');return {'ok':True}
 check(r['state']!='失効','再登録してから模擬検証してください。',409)
 if d.get('simulateFailure')is True:
  c.execute("UPDATE tool_settings SET mock_state='モック失敗',failure='模擬：ブリッジ応答なし。実ツール未起動。',version=version+1 WHERE owner=?AND tool=?",(u,tool));event(c,u,tool,'mock-failure','模擬の応答失敗。実接続状態を変更しない。');return {'ok':False,'message':'模擬：応答失敗。実ツールは起動していません。'}
 import bridge_protocol
 checks=bridge_protocol.self_test()
 c.execute("UPDATE tool_settings SET mock_state='モック境界検証済み（実接続未検証）',failure='',version=version+1 WHERE owner=?AND tool=?",(u,tool));event(c,u,tool,'mock-verified','Origin/明示承認/範囲/重複/失効の模擬検証。実接続へ昇格しない。');return {'ok':True,'checks':checks,'realVerified':False}
