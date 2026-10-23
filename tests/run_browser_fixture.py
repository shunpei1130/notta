"""Local-only browser verification. All provider replacements are confined to tests."""
import json
import os
import secrets
import sys
from pathlib import Path
from http.server import ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s
import service_providers as providers
from test_service import draft

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'/'browser-verification'
DATA.mkdir(parents=True,exist_ok=True)
os.environ['NOTTA_SERVICE_ENABLED']='yes'
os.environ['NOTTA_AUTH_SECRET']=secrets.token_urlsafe(40)
providers.mail_ready=lambda: True
providers.ai_ready=lambda: True
providers.search_ready=lambda: False
providers.Square.ready=lambda: False


def send_code(email,code,challenge):
    # Fictional local inbox; never sends or exposes codes through an HTTP endpoint.
    (DATA/'inbox.json').write_text(json.dumps({'email':email,'code':code,'challenge':challenge}),encoding='utf-8')


def generate(current,messages,prompt,search=None):
    value=draft(current['goal'])
    if '映像' in current['goal']:
        value.update(output='90秒の映像の試作',success='企画者が構成と初稿を確認',recruitment='短い映像を一緒につくる方を募集します。')
    return {'reply':'【動作確認用の応答】目的に合わせた下書きを保存しました。日程や権利の条件を確認してください。','draft':value,'cost_yen':3,'sources':[]}


providers.send_code=send_code
providers.generate=generate
s.init_db(DATA/'browser.sqlite3')
httpd=ThreadingHTTPServer(('127.0.0.1',8876),s.Handler)
print('Local browser verification on port 8876. Provider boundary stubs; no real email, AI or payment.',flush=True)
httpd.serve_forever()
