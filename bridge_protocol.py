"""In-memory protocol simulator. No ports, processes, files or real tool tokens."""
import hashlib,hmac,json,secrets,time
from tool_connections import workspace
OPERATIONS={'plan','decompose','find_analogy','task_propose'}
REAL_COMMANDS={} # Remains empty until official verification and explicit permission.
class Denied(Exception):pass
class MockBridge:
 def __init__(self,origin):self.origin=origin;self.nonces={};self.grants={};self.audit=[]
 def challenge(self,origin):
  if origin!=self.origin:raise Denied('Origin拒否')
  n=secrets.token_urlsafe(24);self.nonces[n]=int(time.time())+60;return n
 def pair(self,origin,nonce,tool,root,permissions,confirmed):
  if origin!=self.origin or not confirmed or self.nonces.pop(nonce,0)<=time.time():raise Denied('明示ペアリング/nonce拒否')
  if not set(permissions)<={'read','propose'}or not permissions:raise Denied('権限拒否')
  gid=secrets.token_hex(12);key=secrets.token_bytes(32);self.grants[gid]={'origin':origin,'tool':tool,'workspace':workspace(root),'permissions':set(permissions),'key':key,'expires':int(time.time())+300,'seen':set(),'revoked':False};self.audit.append({'action':'mock-pair','grant':gid});return gid,key
 @staticmethod
 def sign(key,payload):return hmac.new(key,json.dumps(payload,sort_keys=True,separators=(',',':')).encode(),hashlib.sha256).hexdigest()
 def request(self,origin,gid,payload,signature):
  g=self.grants.get(gid)
  if not g or g['revoked']or g['expires']<=time.time()or origin!=g['origin']:raise Denied('失効/Origin拒否')
  if not hmac.compare_digest(signature,self.sign(g['key'],payload)):raise Denied('署名拒否')
  if set(payload)!={'request','operation','workspace','task'}:raise Denied('任意shell/追加項目拒否')
  if payload['operation']not in OPERATIONS or workspace(payload['workspace'])!=g['workspace']:raise Denied('固定操作/workspace拒否')
  if 'propose'not in g['permissions']:raise Denied('操作権限不足')
  if not isinstance(payload['request'],str)or not 1<=len(payload['request'])<=100 or payload['request']in g['seen']:raise Denied('重複/要求ID拒否')
  if not isinstance(payload['task'],str)or not 1<=len(payload['task'])<=100:raise Denied('タスクID拒否')
  g['seen'].add(payload['request']);self.audit.append({'action':'mock-request','grant':gid,'task':payload['task'],'operation':payload['operation']});return {'status':'mock_only','executed':False,'message':'模擬プロトコルのみ。実CLI/拡張/AIは未稼働。'}
 def revoke(self,gid):
  if gid in self.grants:self.grants[gid]['revoked']=True;self.grants[gid]['key']=b'';self.audit.append({'action':'mock-revoke','grant':gid})
def execute_real(tool,operation):raise Denied('公式対応コマンド未登録。実行・インストール・ログインは行いません。')
def self_test():
 b=MockBridge('https://notta.example.test');n=b.challenge(b.origin);gid,key=b.pair(b.origin,n,'claude-cli','C:/mock/notta',['read','propose'],True);d={'request':'one','operation':'plan','workspace':'C:/mock/notta','task':'mock-task'};r=b.request(b.origin,gid,d,b.sign(key,d));assert not r['executed']
 results=['明示ペアリング','固定操作の模擬要求・実行なし']
 for title,fn in [('異Origin拒否',lambda:b.challenge('https://evil.example')),('再送拒否',lambda:b.request(b.origin,gid,d,b.sign(key,d))),('越境拒否',lambda:b.request(b.origin,gid,{**d,'request':'two','workspace':'C:/other'},b.sign(key,{**d,'request':'two','workspace':'C:/other'})))]:
  try:fn();raise AssertionError(title)
  except Denied:results.append(title)
 b.revoke(gid)
 try:b.request(b.origin,gid,{**d,'request':'three'},b.sign(key,{**d,'request':'three'}));raise AssertionError('失効')
 except Denied:results.append('失効拒否')
 return results
