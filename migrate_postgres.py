"""Offline schema/export and explicit empty-target migration. Never reads env keys.

Do not run apply until the dedicated target and credentials are approved.
Database URL is supplied by environment only; never printed or passed via CLI.
"""
import argparse,json,sqlite3,os
from pathlib import Path
from contextlib import closing
from database import Postgres,translate,ConfigurationError
def snapshot(path,allow_demo=False):
 uri=Path(path).resolve().as_uri()+'?mode=ro'
 with closing(sqlite3.connect(uri,uri=True))as c:
  if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok'or c.execute('PRAGMA foreign_key_check').fetchone():raise ConfigurationError('Source integrity failed')
  if not allow_demo and c.execute("SELECT 1 FROM users WHERE email LIKE '%@example.test'").fetchone():raise ConfigurationError('Demo data cannot enter production')
  tables=c.execute("SELECT name,sql FROM sqlite_master WHERE type='table'AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall();data=[]
  for table,ddl in tables:
   if not table.replace('_','').isalnum():raise ConfigurationError('Invalid table')
   cols=[r[1]for r in c.execute('PRAGMA table_info('+table+')')];rows=c.execute('SELECT *FROM '+table+' ORDER BY rowid').fetchall();data.append((table,ddl,cols,rows))
  return data
def migrate(source,destination,*,approved=False):
 if not approved:raise ConfigurationError('Dedicated empty target and credential approval required')
 data=snapshot(source)
 with Postgres(destination)as c:
  c.execute('BEGIN IMMEDIATE')
  if c.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=current_schema() AND table_type='BASE TABLE'").fetchone():raise ConfigurationError('Target must be empty; no existing project may be changed')
  # SQLite defers some parent references; source order creates all referenced tables first.
  for table,ddl,cols,rows in data:c.execute(ddl)
  for table,ddl,cols,rows in data:
   # tasks can reference another row in the same table; defer its FK for this transaction.
   if table=='tasks':
    c.execute('ALTER TABLE tasks ALTER CONSTRAINT tasks_parent_fkey DEFERRABLE INITIALLY DEFERRED')
   query='INSERT INTO '+table+'('+','.join(cols)+')VALUES('+','.join('?'for _ in cols)+')'
   for row in rows:c.execute(query,row)
   actual=[tuple(r[k]for k in cols)for r in c.execute('SELECT '+','.join(cols)+' FROM '+table+' ORDER BY _ordinal')]
   if actual!=rows:raise ConfigurationError('Migration content mismatch')
   for col in cols:
    if col=='id'and 'id INTEGER PRIMARY KEY'in ddl:
     c.execute("SELECT setval(pg_get_serial_sequence(?,?),GREATEST(COALESCE((SELECT MAX(id)FROM "+table+"),0),1),EXISTS(SELECT 1 FROM "+table+"))",(table,col))
 return {'tables':len(data),'rows':sum(len(t[3])for t in data),'verified':True}
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('--schema',type=Path);p.add_argument('--apply-approved-empty-target',action='store_true');a=p.parse_args()
 if a.schema:
  if a.schema.exists():p.error('Output already exists')
  a.schema.write_text(';\n'.join(translate(t[1])for t in snapshot(a.source,allow_demo=True))+';\n',encoding='utf-8')
 elif a.apply_approved_empty_target:print(json.dumps(migrate(a.source,os.environ.get('NOTTA_DATABASE_URL'),approved=True)))
 else:p.error('Choose offline schema output or explicit approved target application')
