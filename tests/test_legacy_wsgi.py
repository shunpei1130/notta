import io,json,sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
import wsgi,accounts
class WSGI(unittest.TestCase):
 def test_https_origin_real_login_cookie_and_http_rejection(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as t:
   db=Path(t)/'wsgi.db';app=wsgi.create_application(db,'https://notta.example.test')
   def call(path,data=None,scheme='https',host='notta.example.test'):
    body=json.dumps(data).encode() if data else b'';env={'REQUEST_METHOD':'POST' if data else 'GET','PATH_INFO':'/api/'+path,'QUERY_STRING':'','SERVER_PORT':'443','REMOTE_ADDR':'127.0.0.1','HTTP_HOST':host,'wsgi.url_scheme':scheme,'wsgi.input':io.BytesIO(body),'CONTENT_LENGTH':str(len(body)),'CONTENT_TYPE':'application/json'};captured=[]
    raw=b''.join(app(env,lambda status,headers:captured.append((status,dict(headers)))));return captured[0],raw
   self.assertTrue(call('health',scheme='http')[0][0].startswith('426'))
   self.assertTrue(call('health',host='evil.example')[0][0].startswith('403'))
   token=accounts.issue(db,'wsgi-user@example.test','invite','fan')
   self.assertTrue(call('account/activate',{'token':token,'name':'架空WSGI利用者','password':'Valid-Password-2026!'})[0][0].startswith('200'))
   result,_=call('login',{'email':'wsgi-user@example.test','password':'Valid-Password-2026!'});self.assertTrue(result[0].startswith('200'));self.assertIn('; Secure',result[1]['Set-Cookie'])
 def test_public_mode_refuses_known_demo_users_and_insecure_origin(self):
  with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as t:
   db=Path(t)/'demo.db';wsgi.server.init_db(db);wsgi.server.seed()
   with self.assertRaises(ValueError):wsgi.create_application(db,'https://notta.example.test')
   with self.assertRaises(ValueError):wsgi.create_application(db,'http://notta.example.test')
if __name__=='__main__':unittest.main(verbosity=2)
