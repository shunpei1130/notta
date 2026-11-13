"""Isolated auth regression tests; no real email, users or provider calls."""
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as s
import service_core as core
import service_providers as providers
import password_auth as auth
import wsgi
import notta_service

PASS = 'a secure password 12345'


class PasswordTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'NOTTA_SERVICE_ENABLED':'yes', 'NOTTA_AUTH_SECRET':'x'*48, 'NOTTA_PUBLIC_ORIGIN':'https://notta.example'})
        self.env.start()
        s.init_db(Path(self.tmp.name) / 'auth.sqlite3')
        self.c = s.connect()
        self.uid = s.ident()
        self.c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)', (self.uid, 'owner@example.test', '既存利用者', 'creator', '00'*16, 'disabled-password-login'))
        self.c.execute('INSERT INTO svc_accounts(user_id,verified_at,created) VALUES(?,?,?)', (self.uid, s.stamp(), s.stamp()))
        self.c.execute('INSERT INTO projects(id,owner,title,goal,output,conditions,days,created) VALUES(?,?,?,?,?,?,?,?)', ('p', self.uid, '保存済み企画', 'goal', '', '', 30, s.stamp()))
        self.c.commit()
        self.mail = patch.object(providers, 'send_intake_notification', return_value={'id':'mail-test'}).start()
        self.ready = patch.object(providers, 'mail_ready', return_value=True).start()

    def tearDown(self):
        patch.stopall()
        self.env.stop()
        self.c.close()
        self.tmp.cleanup()

    def problem(self, status, call):
        with self.assertRaises(s.Problem) as error:
            call()
        self.assertEqual(status, error.exception.status)
        self.c.rollback()

    def link(self):
        auth.send_link(self.c, self.uid)
        return re.search(r'#token=([^\s]+)', self.mail.call_args.args[0]['text'])[1]

    def setup_password(self):
        token = self.link()
        auth.complete(self.c, {'token':token, 'password':PASS}, 'ip')
        return token

    def test_existing_identity_and_data_preserved(self):
        token = self.setup_password()
        result, _ = auth.login(self.c, {'email':'OWNER@example.test', 'password':PASS}, 'ip')
        self.assertEqual(self.uid, result['user']['id'])
        self.assertEqual(self.uid, self.c.execute('SELECT owner FROM projects WHERE id=?', ('p',)).fetchone()[0])
        stored = self.c.execute('SELECT password_hash FROM svc_passwords').fetchone()[0]
        self.assertTrue(stored.startswith('$argon2id$'))
        self.assertNotIn(PASS, stored)
        row = self.c.execute('SELECT token_hash,used FROM svc_password_tokens').fetchone()
        self.assertEqual(hashlib.sha256(token.encode()).hexdigest(), row[0])
        self.assertEqual(1, row[1])

    def test_single_use_expired_and_superseded_links(self):
        old = self.link(); token = self.link()
        self.problem(403, lambda:auth.complete(self.c, {'token':old, 'password':PASS}, 'ip'))
        auth.complete(self.c, {'token':token, 'password':PASS}, 'ip')
        self.problem(403, lambda:auth.complete(self.c, {'token':token, 'password':PASS}, 'ip'))
        token = self.link()
        self.c.execute('UPDATE svc_password_tokens SET expires=0'); self.c.commit()
        self.problem(403, lambda:auth.complete(self.c, {'token':token, 'password':PASS}, 'ip'))

    def test_reset_revokes_all_sessions(self):
        self.setup_password()
        for _ in range(2): auth.login(self.c, {'email':'owner@example.test', 'password':PASS}, 'ip')
        self.assertEqual(2, self.c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])
        auth.complete(self.c, {'token':self.link(), 'password':PASS+'new'}, 'ip')
        self.assertEqual(0, self.c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])
        self.problem(403, lambda:auth.login(self.c, {'email':'owner@example.test', 'password':PASS}, 'ip'))

    def test_login_limit_and_generic_errors(self):
        errors = []
        for email in ('owner@example.test', 'absent@example.test'):
            for _ in range(5):
                try: auth.login(self.c, {'email':email, 'password':'incorrect'}, 'ip')
                except s.Problem as error: errors.append(error.message); self.c.rollback()
            self.problem(429, lambda:auth.login(self.c, {'email':email, 'password':PASS}, 'ip'))
        self.assertEqual(1, len(set(errors)))

    def test_mail_generic_and_rate_limited(self):
        a = auth.request_link(self.c, {'email':'owner@example.test'}, 'ip')
        b = auth.request_link(self.c, {'email':'absent@example.test'}, 'ip')
        self.assertEqual(a, b)
        self.assertEqual(1, self.mail.call_count)
        for _ in range(2): auth.request_link(self.c, {'email':'owner@example.test'}, 'ip')
        self.problem(429, lambda:auth.request_link(self.c, {'email':'owner@example.test'}, 'ip'))

    def test_campaign_dedup_and_uncertain_delivery(self):
        self.mail.side_effect = providers.ProviderError('timeout', uncertain=True)
        self.assertEqual({'unknown':1}, auth.invite_existing(self.c))
        self.assertEqual({'skipped':1}, auth.invite_existing(self.c))
        self.assertEqual(1, self.mail.call_count)
        payload = self.mail.call_args.args[0]
        self.assertEqual(['owner@example.test'], payload['to'])
        self.assertNotIn('cc', payload)

    def test_operator_invitation_api_requires_operator_and_csrf(self):
        result = {}
        handler = SimpleNamespace(headers={'Sec-Fetch-Site':'same-origin','X-CSRF-Token':'csrf'}, input=lambda:{}, session=lambda c:{'id':self.uid,'csrf':'csrf'}, respond=lambda status,body,*args:result.update(status=status,body=body))
        path = '/api/service/admin/password-invitations'
        self.problem(403, lambda:notta_service.dispatch(handler, 'GET', path))
        self.assertEqual(0, self.mail.call_count)
        self.c.execute('UPDATE users SET email=? WHERE id=?', ('shumpei.hasegawa@maora.co.jp', self.uid)); self.c.commit()
        notta_service.dispatch(handler, 'GET', path)
        self.assertEqual(1, result['body']['eligible']); self.assertEqual(0, self.mail.call_count)
        handler.headers['X-CSRF-Token'] = 'wrong'
        self.problem(403, lambda:notta_service.dispatch(handler, 'POST', path))
        self.assertEqual(0, self.mail.call_count)
        handler.headers['X-CSRF-Token'] = 'csrf'
        notta_service.dispatch(handler, 'POST', path)
        self.assertEqual({'eligible':0,'statuses':{'sent':1}}, result['body'])
        notta_service.dispatch(handler, 'POST', path)
        self.assertEqual(1, self.mail.call_count)

    def test_password_length_and_no_trimming(self):
        token = self.link()
        for value in ('x'*14, 'x'*129, None):
            self.problem(400, lambda:auth.complete(self.c, {'token':token, 'password':value}, 'ip'))
        auth.complete(self.c, {'token':token, 'password':' '+PASS+' '}, 'ip')
        self.problem(403, lambda:auth.login(self.c, {'email':'owner@example.test', 'password':PASS}, 'ip'))
        auth.login(self.c, {'email':'owner@example.test', 'password':' '+PASS+' '}, 'ip')

    def test_signup_and_no_otp_login_after_password_set(self):
        with patch.object(providers, 'send_code') as send:
            start = core.auth_start(self.c, {'email':'new@example.test', 'name':'新規', 'consent':True}, 'ip')
        result, token = core.auth_verify(self.c, {'challenge':start['challenge'], 'code':send.call_args.args[1], 'password':PASS})
        self.assertTrue(token)
        auth.login(self.c, {'email':'new@example.test', 'password':PASS}, 'ip')
        with patch.object(s, 'stamp', return_value=s.stamp()+61), patch.object(providers, 'send_code') as send:
            start = core.auth_start(self.c, {'email':'new@example.test', 'name':'新規', 'consent':True}, 'ip')
        self.problem(403, lambda:core.auth_verify(self.c, {'challenge':start['challenge'], 'code':send.call_args.args[1], 'password':PASS+'wrong'}))

    def test_http_cookie_persistence_logout_and_get_link(self):
        self.setup_password()
        app = wsgi.create_application(s.DB, 'https://notta.example', initialize=False)
        def request(path, data=None, cookie='', csrf=''):
            captured = {}
            raw = json.dumps(data).encode() if data is not None else b''
            env = {'REQUEST_METHOD':'POST' if data is not None else 'GET', 'PATH_INFO':path, 'wsgi.url_scheme':'https', 'wsgi.input':io.BytesIO(raw), 'CONTENT_LENGTH':str(len(raw)), 'CONTENT_TYPE':'application/json', 'HTTP_HOST':'notta.example', 'HTTP_ORIGIN':'https://notta.example', 'HTTP_COOKIE':cookie, 'HTTP_X_CSRF_TOKEN':csrf, 'SERVER_PORT':'443'}
            body = b''.join(app(env, lambda status, headers:captured.update(status=status, headers=dict(headers))))
            return captured, body
        result, body = request('/api/service/auth/login', {'email':'owner@example.test', 'password':PASS})
        self.assertTrue(result['status'].startswith('200'))
        cookie = result['headers']['Set-Cookie']
        self.assertIn('Secure', cookie); self.assertIn('HttpOnly', cookie); self.assertIn('Max-Age=34560000', cookie)
        csrf = json.loads(body)['csrf']
        with patch.object(s, 'stamp', return_value=s.stamp()+86400):
            result, _ = request('/api/service/me', cookie=cookie)
        self.assertTrue(result['status'].startswith('200'))
        self.assertIn('Set-Cookie', result['headers'])
        request('/api/service/logout', {}, cookie, csrf)
        result, _ = request('/api/service/me', cookie=cookie)
        self.assertTrue(result['status'].startswith('401'))
        fresh_token = self.link()
        result, _ = request('/password')
        self.assertTrue(result['status'].startswith('200'))
        self.assertEqual(0, self.c.execute('SELECT used FROM svc_password_tokens WHERE token_hash=?', (hashlib.sha256(fresh_token.encode()).hexdigest(),)).fetchone()[0])


if __name__ == '__main__': unittest.main()
