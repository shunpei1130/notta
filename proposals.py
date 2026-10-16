"""Strict advisory result contract. No model, process, network or completion authority."""
import json
from urllib.parse import urlparse
ROLES={'coordinator':'question','decomposer':'children','analogy':'analogy','task':'draft'}
class InvalidProposal(Exception):pass
def require(ok):
 if not ok:raise InvalidProposal('提案の形式・範囲を確認してください。実行や完了の権限はありません。')
def text(v,n=2000):require(isinstance(v,str)and 0<len(v.strip())<=n);return v.strip()
def validate(role,value):
 require(role in ROLES and isinstance(value,dict));require(set(value)=={'kind','body','items'}and value['kind']==ROLES[role]);body=text(value['body']);items=value['items'];require(isinstance(items,list))
 if role in ['coordinator','task']:require(not items)
 elif role=='decomposer':
  require(2<=len(items)<=4)
  for child in items:require(isinstance(child,dict)and set(child)=={'title','acceptance'});text(child['title'],80);text(child['acceptance'],500)
 elif role=='analogy':
  require(len(items)<=3)
  for item in items:
   require(isinstance(item,dict)and set(item)=={'title','url','basis','similarity','difference','application','verified'})
   for k in ['title','basis','similarity','difference','application']:text(item[k])
   u=urlparse(text(item['url'],1000));require(u.scheme=='https'and u.hostname and not u.username and not u.password and item['verified']is False)
 require(len(json.dumps(value,ensure_ascii=False))<=16000)
 return {'kind':value['kind'],'body':body,'items':items}
def mock(role):
 value={'kind':ROLES[role],'body':'模擬提案です。AI/検索/CLIは実行していません。本人が内容を確認し、既存の対話・分解・出典フォームへ手動で反映してください。','items':[]}
 if role=='coordinator':value['body']='模擬の質問：最初に本人が確認できる成果物と、その合格条件を1つずつ挙げてください。AIの回答ではありません。'
 if role=='decomposer':value['items']=[{'title':'対象と意図を確認する（模擬）','acceptance':'本人が対象と意図を確認する'},{'title':'小さな成果物を比較する（模擬）','acceptance':'本人が比較根拠と残る疑問を記録する'}]
 if role=='analogy':value['body']='模擬：実検索はしていないため出典0件です。手元の出典URL・根拠・類似・相違・適用限界を入力し、本人が検証してください。'
 return validate(role,value)
