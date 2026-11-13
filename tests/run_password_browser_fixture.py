"""Local browser fixture; all mail stays in a fictional on-disk inbox."""
import json
import os
import secrets
import sys
from pathlib import Path
from http.server import ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as s
import service_providers as providers
import password_auth

root = Path(__file__).resolve().parents[1] / 'data' / 'password-browser'
root.mkdir(parents=True, exist_ok=True)
os.environ.update(NOTTA_SERVICE_ENABLED='yes', NOTTA_INTAKE_ENABLED='yes', NOTTA_AUTH_SECRET=secrets.token_urlsafe(40), NOTTA_PUBLIC_ORIGIN='https://notta.example')
providers.mail_ready = lambda: True
providers.interview_ready = lambda: False
providers.send_code = lambda email, code, challenge: (root / 'inbox.json').write_text(json.dumps({'email':email, 'code':code}), encoding='utf-8')
def send(payload, key):
    (root / 'inbox.json').write_text(json.dumps(payload), encoding='utf-8')
    return {'id':'local-fictional-mail'}
providers.send_intake_notification = send
s.init_db(root / (s.ident() + '.sqlite3'))
with s.connect() as c:
    uid = s.ident()
    c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)', (uid, 'existing@example.test', '既存利用者', 'creator', '00'*16, 'disabled-password-login'))
    c.execute('INSERT INTO svc_accounts(user_id,verified_at,created) VALUES(?,?,?)', (uid, s.stamp(), s.stamp()))
    operator = s.ident()
    c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)', (operator, 'shumpei.hasegawa@maora.co.jp', '検証用運営', 'creator', '00'*16, 'disabled-password-login'))
    c.execute('INSERT INTO svc_accounts(user_id,verified_at,created) VALUES(?,?,?)', (operator, s.stamp(), s.stamp()))
    c.execute('INSERT INTO svc_passwords VALUES(?,?,?)', (operator, password_auth.HASHER.hash('a secure password 12345'), s.stamp()))
    invitee = s.ident()
    c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)', (invitee, 'invitee@example.test', '案内対象の架空利用者', 'creator', '00'*16, 'disabled-password-login'))
    c.execute('INSERT INTO svc_accounts(user_id,verified_at,created) VALUES(?,?,?)', (invitee, s.stamp(), s.stamp()))
    c.commit()
print('Fictional local password fixture on 8896; no external email or AI.', flush=True)
ThreadingHTTPServer(('127.0.0.1',8896), s.Handler).serve_forever()
