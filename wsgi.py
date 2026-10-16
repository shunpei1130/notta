"""Production-server callable, not a deployment or a production approval.

Reuse the tested request dispatcher without http.server's socket server.
An operator must supply a production WSGI server, persistent disk and HTTPS.
"""
import io,os
from email.message import Message
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
import server

def create_application(db,public_origin=None,backend="sqlite",initialize=True):
 origin=urlparse(public_origin or '')
 if public_origin and not (origin.scheme=='https' and origin.hostname and origin.path in ['','/'] and not origin.query and not origin.fragment and not origin.username and not origin.password):raise ValueError('公開originはhttpsの単一オリジンを指定してください。')
 expected=public_origin.rstrip('/') if public_origin else None
 server.init_db(db,backend=backend,initialize=initialize)
 if expected:
  with server.connect() as c:
   if c.execute("SELECT 1 FROM users WHERE email IN ('creator@example.test','other@example.test','fan@example.test','fan2@example.test')").fetchone():raise ValueError('公開用DBに既知のデモ利用者があります。独立した本番DBを用意してください。')
 def application(environ,start_response):
  status=200;headers=[]
  def simple(code,message):
   body=message.encode();start_response(f'{code} {HTTPStatus(code).phrase}',[('Content-Type','text/plain; charset=utf-8'),('Cache-Control','no-store'),('Content-Length',str(len(body)))]);return [body]
  if expected and environ.get('wsgi.url_scheme')!='https':return simple(426,'HTTPSが必要です。信頼するプロキシ設定を確認してください。')
  if environ.get('REQUEST_METHOD') not in ['GET','POST']:return simple(405,'GET/POSTのみ対応しています。')
  try:n=int(environ.get('CONTENT_LENGTH') or 0)
  except ValueError:return simple(400,'Content-Lengthが不正です。')
  if n<0 or n>server.MAX_BODY:return simple(413,'本文が大きすぎます。')
  handler=server.Handler.__new__(server.Handler);handler.headers=Message()
  for key,value in environ.items():
   if key.startswith('HTTP_'):handler.headers[key[5:].replace('_','-')]=str(value)
  handler.headers['Content-Length']=str(n);handler.headers['Content-Type']=environ.get('CONTENT_TYPE','')
  handler.rfile=io.BytesIO(environ['wsgi.input'].read(n));handler.wfile=io.BytesIO();handler.server=SimpleNamespace(server_port=int(environ.get('SERVER_PORT','80')));handler.client_address=(environ.get('REMOTE_ADDR','local'),0)
  handler.path=environ.get('PATH_INFO','/')+('?' + environ['QUERY_STRING'] if environ.get('QUERY_STRING') else '')
  def send_response(code,*args):
   nonlocal status
   status=code
  def send_header(name,value):
   if expected and name.lower()=='set-cookie':value+='; Secure'
   headers.append((name,value))
  handler.send_response=send_response;handler.send_header=send_header;handler.end_headers=lambda:None
  if expected:
   def check_origin():
    server.require(handler.headers.get('Host','')==origin.netloc,403,'接続先が設定originと異なります。')
    sent=handler.headers.get('Origin');server.require(not sent or sent==expected,403,'別originからの操作を拒否しました。')
   handler.origin=check_origin
  handler.handle_request(environ['REQUEST_METHOD'])
  start_response(f'{status} {HTTPStatus(status).phrase}',headers)
  return [handler.wfile.getvalue()]
 return application

_application=None
def application(environ,start_response):
 global _application
 if _application is None:_application=create_application(os.environ.get('NOTTA_DB',str(Path(__file__).parent/'data/notta-v3.sqlite3')),os.environ.get('NOTTA_PUBLIC_ORIGIN'))
 return _application(environ,start_response)
