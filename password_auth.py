"""Password authentication and one-use mail links for verified service accounts."""
import hashlib
import os
import secrets
from urllib.parse import urlparse

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerificationError, InvalidHashError
import server as s
import service_core as core
import service_providers as providers

HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, type=Type.ID)
DUMMY = HASHER.hash(secrets.token_urlsafe(32))
PERSISTENT = 253402300799  # Explicit revocation, rather than an inactivity timeout.
COOKIE_AGE = 34560000  # Browsers cap persistent cookies at 400 days; renew on use.
GENERIC = {"ok": True, "message": "対象のメールアドレスに設定方法をお送りしました。届かない場合は入力内容をご確認ください。"}


def cookie(token):
    return 'notta_session=' + token + '; HttpOnly; SameSite=Lax; Path=/; Max-Age=' + str(COOKIE_AGE)


def password(value):
    s.require(isinstance(value, str) and 15 <= len(value) <= 128, 400, 'パスワードは15〜128文字で入力してください。')
    return value


def limited(c, kind, email, ip, limit=5):
    """Call inside the transaction; commit rejected attempts too."""
    now = s.stamp()
    eh, ih = core.digest('email:' + email), core.digest('ip:' + ip)
    count = c.execute('SELECT COUNT(*) FROM svc_auth_attempts WHERE kind=? AND email_hash=? AND created>?', (kind, eh, now - 900)).fetchone()[0]
    ips = c.execute('SELECT COUNT(*) FROM svc_auth_attempts WHERE kind=? AND ip_hash=? AND created>?', (kind, ih, now - 900)).fetchone()[0]
    s.require(count < limit and ips < 30, 429, '操作回数の上限です。15分後にお試しください。')
    c.execute('INSERT INTO svc_auth_attempts VALUES(?,?,?,?,?)', (s.ident(), kind, eh, ih, now))


def new_session(c, uid):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    c.execute('INSERT INTO sessions VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), uid, csrf, PERSISTENT))
    user = c.execute('SELECT id,name,email FROM users WHERE id=?', (uid,)).fetchone()
    return {'user': {k: user[k] for k in ('id', 'name', 'email')}, 'csrf': csrf}, token


def login(c, data, ip):
    email = core.email_value(data.get('email'))
    value = data.get('password')
    s.require(isinstance(value, str) and len(value) <= 128, 400, 'メールアドレスとパスワードを確認してください。')
    c.execute('BEGIN IMMEDIATE')
    limited(c, 'login', email, ip)
    c.commit()  # Persist failed attempts even when authentication fails.
    row = c.execute('SELECT p.* FROM svc_passwords p JOIN users u ON u.id=p.user_id JOIN svc_accounts a ON a.user_id=u.id WHERE u.email=?', (email,)).fetchone()
    try:
        valid = HASHER.verify(row['password_hash'] if row else DUMMY, value)
    except (VerificationError, InvalidHashError):
        valid = False
    s.require(row and valid, 403, 'メールアドレスとパスワードを確認してください。未設定の方は「パスワードの設定・再設定」から設定してください。')
    c.execute('BEGIN IMMEDIATE')
    # Recheck after the expensive hash, so a concurrent reset cannot resurrect an old password.
    current = c.execute('SELECT password_hash FROM svc_passwords WHERE user_id=?', (row['user_id'],)).fetchone()
    s.require(current and current[0] == row['password_hash'], 403, 'もう一度ログインしてください。')
    result = new_session(c, row['user_id'])
    core.event(c, row['user_id'], 'password_login')
    c.commit()
    return result


def origin():
    value = os.getenv('NOTTA_PUBLIC_ORIGIN', '').rstrip('/')
    parsed = urlparse(value)
    s.require(parsed.scheme == 'https' and parsed.netloc and not parsed.username and not parsed.password and parsed.path in ('', '/') and not parsed.query and not parsed.fragment, 503, '公開URLの設定を確認してください。')
    return value


def send_link(c, uid, campaign=None):
    """Reserve before sending. Never automatically retry uncertain sends."""
    base = origin()
    c.execute('BEGIN IMMEDIATE')
    user = c.execute('SELECT u.id,u.email FROM users u JOIN svc_accounts a ON a.user_id=u.id WHERE u.id=?', (uid,)).fetchone()
    s.require(user, 404)
    if campaign and c.execute('SELECT 1 FROM svc_password_mail WHERE user_id=? AND campaign=?', (uid, campaign)).fetchone():
        c.commit()
        return 'skipped'
    purpose = 'reset' if c.execute('SELECT 1 FROM svc_passwords WHERE user_id=?', (uid,)).fetchone() else 'setup'
    token, mid = secrets.token_urlsafe(32), s.ident()
    now = s.stamp()
    c.execute('UPDATE svc_password_tokens SET used=1 WHERE user_id=? AND used=0', (uid,))
    c.execute('INSERT INTO svc_password_mail(id,user_id,campaign,status,created,updated) VALUES(?,?,?,?,?,?)', (mid, uid, campaign or mid, 'sending', now, now))
    c.execute('INSERT INTO svc_password_tokens VALUES(?,?,?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), uid, purpose, now + 86400, 0, now, mid))
    c.commit()
    link = base + '/password#token=' + token
    payload = {'from': 'ノッタ <shumpei.hasegawa@maora.co.jp>', 'to': [user['email']], 'reply_to': 'shumpei.hasegawa@maora.co.jp',
               'subject': '【ノッタ】パスワード設定のご案内' if purpose == 'setup' else '【ノッタ】パスワードの再設定',
               'text': 'ノッタはメールアドレスとパスワードでログインできるようになりました。企画や回答はそのまま引き継がれます。\n\n以下からパスワードを設定してください。リンクは24時間有効で、一度だけ使用できます。\n' + link + '\n\nリンクを開くだけでは設定は完了しません。期限切れの場合は、ログイン画面の「パスワードの設定・再設定」から再発行できます。\nご自身で再設定を依頼していない場合は、このメールを破棄してください。\n\nMAORA株式会社\nshumpei.hasegawa@maora.co.jp'}
    try:
        response = providers.send_intake_notification(payload, 'notta-password-' + mid)
        provider_id = response.get('id', '')
        status = 'sent' if provider_id else 'unknown'
    except providers.ProviderError as error:
        status, provider_id = ('unknown' if error.uncertain else 'failed'), ''
    c.execute('UPDATE svc_password_mail SET status=?,provider_id=?,updated=? WHERE id=?', (status, provider_id, s.stamp(), mid))
    c.commit()
    return status


def request_link(c, data, ip):
    s.require(providers.mail_ready(), 503, 'メール送信の準備中です。')
    email = core.email_value(data.get('email'))
    c.execute('BEGIN IMMEDIATE')
    limited(c, 'password-mail', email, ip, 3)
    c.commit()
    user = c.execute('SELECT u.id FROM users u JOIN svc_accounts a ON a.user_id=u.id WHERE u.email=?', (email,)).fetchone()
    if user:
        send_link(c, user[0])
    return dict(GENERIC)


def complete(c, data, ip):
    value = password(data.get('password'))
    raw = data.get('token')
    s.require(isinstance(raw, str) and 40 <= len(raw) <= 100, 403, 'リンクが無効・期限切れです。設定メールを再発行してください。')
    c.execute('BEGIN IMMEDIATE')
    limited(c, 'password-complete', ip, ip, 20)
    c.commit()
    hashed = HASHER.hash(value)
    c.execute('BEGIN IMMEDIATE')
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    row = c.execute('SELECT * FROM svc_password_tokens WHERE token_hash=?', (token_hash,)).fetchone()
    s.require(row and not row['used'] and row['expires'] > s.stamp(), 403, 'リンクが無効・期限切れです。設定メールを再発行してください。')
    changed = c.execute('UPDATE svc_password_tokens SET used=1 WHERE token_hash=? AND used=0', (token_hash,))
    s.require(changed.rowcount == 1, 403, '使用済みのリンクです。')
    c.execute('INSERT INTO svc_passwords(user_id,password_hash,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET password_hash=excluded.password_hash,updated=excluded.updated', (row['user_id'], hashed, s.stamp()))
    c.execute('UPDATE svc_password_tokens SET used=1 WHERE user_id=?', (row['user_id'],))
    c.execute('DELETE FROM sessions WHERE user_id=?', (row['user_id'],))
    core.event(c, row['user_id'], 'password_set')
    c.commit()
    return {'ok': True}


def invite_existing(c, campaign='password-login-v1'):
    users = c.execute('SELECT a.user_id FROM svc_accounts a WHERE NOT EXISTS(SELECT 1 FROM svc_passwords p WHERE p.user_id=a.user_id)').fetchall()
    counts = {}
    for user in users:
        status = send_link(c, user[0], campaign)
        counts[status] = counts.get(status, 0) + 1
    return counts


def invitation_summary(c):
    eligible = c.execute("SELECT COUNT(*) FROM svc_accounts a WHERE NOT EXISTS(SELECT 1 FROM svc_passwords p WHERE p.user_id=a.user_id) AND NOT EXISTS(SELECT 1 FROM svc_password_mail m WHERE m.user_id=a.user_id AND m.campaign='password-login-v1')").fetchone()[0]
    statuses = {r['status']: r['total'] for r in c.execute("SELECT status,COUNT(*) AS total FROM svc_password_mail WHERE campaign='password-login-v1' GROUP BY status")}
    return {'eligible': eligible, 'statuses': statuses}


def invite_next(c, operator):
    # One delivery per request stays within the serverless request time budget.
    row = c.execute("SELECT a.user_id FROM svc_accounts a WHERE NOT EXISTS(SELECT 1 FROM svc_passwords p WHERE p.user_id=a.user_id) AND NOT EXISTS(SELECT 1 FROM svc_password_mail m WHERE m.user_id=a.user_id AND m.campaign='password-login-v1') ORDER BY a.created,a.user_id LIMIT 1").fetchone()
    if row:
        status = send_link(c, row[0], 'password-login-v1')
        core.event(c, operator, 'password_invitation_' + status)
        c.commit()
    return invitation_summary(c)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Verified-account password setup notices. Dry run unless --send is supplied.')
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--expected-host', required=True)
    parser.add_argument('--expected-origin', required=True)
    args = parser.parse_args()
    dsn = os.getenv('NOTTA_DATABASE_URL', '')
    s.require(urlparse(dsn).hostname == args.expected_host and origin() == args.expected_origin, 400, '接続先が一致しません。')
    s.init_db(dsn, backend='postgres', initialize=False)
    with s.connect() as connection:
        if args.send:
            print(invite_existing(connection))
        else:
            row = connection.execute("SELECT COUNT(*) FROM svc_accounts a WHERE NOT EXISTS(SELECT 1 FROM svc_passwords p WHERE p.user_id=a.user_id) AND NOT EXISTS(SELECT 1 FROM svc_password_mail m WHERE m.user_id=a.user_id AND m.campaign='password-login-v1')").fetchone()
            print({'eligible': row[0], 'sent': 0})
