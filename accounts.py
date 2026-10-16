"""Local administrator CLI. No signup, email, external credentials or network calls."""
import argparse,hashlib,re,secrets,sqlite3,time
from pathlib import Path
from contextlib import closing
def issue(db,email,kind,role='fan',ttl=1800):
 email=email.strip().lower()
 if not re.fullmatch(r'[^@\s]{1,100}@[^@\s.]+(?:\.[^@\s.]+)+',email) or len(email)>200:raise ValueError('メール形式を確認してください。')
 if kind not in ['invite','reset'] or role not in ['creator','fan'] or not 60<=ttl<=1800:raise ValueError('招待/再設定・役割・有効期間が不正です。')
 path=Path(db).resolve()
 if not path.is_file():raise ValueError('既存DBが必要です。新しいDBや実在アカウントを自動作成しません。')
 token=secrets.token_urlsafe(32)
 with closing(sqlite3.connect(path)) as c:
  c.execute('BEGIN IMMEDIATE')
  user=c.execute('SELECT role FROM users WHERE email=?',(email,)).fetchone()
  if kind=='invite' and user:raise ValueError('登録済みです。再設定を使ってください。')
  if kind=='reset' and not user:raise ValueError('登録済み利用者が見つかりません。')
  c.execute('DELETE FROM account_tokens WHERE email=? OR expires<=?',(email,int(time.time())))
  c.execute('INSERT INTO account_tokens VALUES(?,?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),email,user[0] if user else role,kind,int(time.time())+ttl));c.commit()
 return token
if __name__=='__main__':
 ap=argparse.ArgumentParser(description='本人確認と対象別の許可後に管理者が実行。コードの自動送信はしません。');ap.add_argument('kind',choices=['invite','reset']);ap.add_argument('--db',default=str(Path(__file__).parent/'data/notta-v3.sqlite3'));ap.add_argument('--email',required=True);ap.add_argument('--role',choices=['creator','fan'],default='fan');a=ap.parse_args()
 try:print(issue(a.db,a.email,a.kind,a.role));print('有効30分・一回限り。対象本人を確認した経路で手動提供。ログ/スクリーンショット/公開URLへ残さないでください。')
 except (ValueError,sqlite3.Error) as e:ap.exit(1,str(e)+'\n')
