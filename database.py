"""Explicit SQLite/Postgres selection. Never fall back from a missing Postgres DSN.

Postgres adapter covers this repository's fixed SQL dialect, not arbitrary SQL.
One transaction advisory lock serializes writes to preserve local MVP semantics.
"""
import re,sqlite3
class ConfigurationError(Exception):pass
class HybridRow(dict):
 def __getitem__(self,key):return list(self.values())[key]if isinstance(key,int)else super().__getitem__(key)
def row_factory(cursor):
 names=[c.name for c in (cursor.description or [])]
 return lambda values:HybridRow(zip(names,values))
def translate(sql):
 # Replace bind markers only outside SQL literals; literal question marks survive.
 parts=re.split("('(?:''|[^'])*')",sql)
 for i in range(0,len(parts),2):
  # Psycopg binds become $1 etc.; without boundaries ?AND becomes invalid $1AND.
  parts[i]=parts[i].replace('%','%%').replace('?',' %s ')
  parts[i]=re.sub(r'\bIS\s+%s', 'IS NOT DISTINCT FROM %s',parts[i],flags=re.I)
  parts[i]=re.sub(r'\browid\b','_ordinal',parts[i],flags=re.I)
 sql=''.join(parts)
 if re.match(r'\s*CREATE TABLE',sql,re.I):
  sql=re.sub(r'\bINTEGER PRIMARY KEY\b','BIGSERIAL PRIMARY KEY',sql,flags=re.I)
  sql=re.sub(r'\bBLOB\b','BYTEA',sql,flags=re.I)
  sql=re.sub(r'\bINTEGER\b','BIGINT',sql,flags=re.I)
  end=sql.rfind(')');sql=sql[:end]+', _ordinal BIGSERIAL UNIQUE'+sql[end:]
 if re.match(r'\s*INSERT OR IGNORE',sql,re.I):
  sql=re.sub('INSERT OR IGNORE','INSERT',sql,count=1,flags=re.I).rstrip().rstrip(';')+' ON CONFLICT DO NOTHING'
 if re.match(r'\s*INSERT OR REPLACE',sql,re.I):
  table=re.search(r'INTO\s+(\w+)',sql,re.I)[1]
  known={'login_attempts':('key,count,expires','key','count=excluded.count,expires=excluded.expires'),'attachments':('application,name,mime,data','application','name=excluded.name,mime=excluded.mime,data=excluded.data')}
  if table not in known:raise ConfigurationError('Unmapped replace operation')
  cols,key,updates=known[table];sql=re.sub('INSERT OR REPLACE','INSERT',sql,count=1,flags=re.I);sql=re.sub(r'INTO\s+'+table+r'\s+VALUES','INTO '+table+'('+cols+') VALUES',sql,flags=re.I).rstrip().rstrip(';')+' ON CONFLICT('+key+') DO UPDATE SET '+updates
 return sql
class Cursor:
 def __init__(self,cursor):self.cursor=cursor
 @property
 def rowcount(self):return self.cursor.rowcount
 def fetchone(self):return self.cursor.fetchone()
 def fetchall(self):return self.cursor.fetchall()
 def __iter__(self):return iter(self.cursor)
class Postgres:
 dialect='postgres'
 def __init__(self,dsn,driver=None):
  if not dsn or not dsn.startswith(('postgres://','postgresql://')):raise ConfigurationError('Explicit PostgreSQL DSN is required')
  if driver is None:
   try:import psycopg as driver
   except ImportError:raise ConfigurationError('Install the declared psycopg dependency before PostgreSQL startup')from None
  self.driver=driver
  try:
   self.raw=driver.connect(dsn,row_factory=row_factory,connect_timeout=10)
   self.raw.execute("SET LOCAL statement_timeout = '15s'")
   self.raw.execute("SET LOCAL lock_timeout = '10s'")
  except Exception as error:
   if getattr(self,'raw',None):self.raw.close()
   category=type(error).__name__
   raise ConfigurationError('PostgreSQL unavailable ('+category+'); connection details suppressed')from None
 def __enter__(self):return self
 def __exit__(self,kind,value,tb):
  try:self.raw.rollback()if kind else self.raw.commit()
  finally:self.raw.close()
 def execute(self,sql,params=()):
  try:
   if sql.strip().upper()=='BEGIN IMMEDIATE':return Cursor(self.raw.execute('SELECT pg_advisory_xact_lock(72843154029)'))
   if sql.upper().startswith('PRAGMA TABLE_INFO('):
    table=re.fullmatch(r'PRAGMA table_info\((\w+)\)',sql,re.I)
    if not table:raise ConfigurationError('Invalid schema inspection')
    return Cursor(self.raw.execute("SELECT ordinal_position-1 AS cid,column_name AS name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position",(table[1],)))
   if sql.upper().startswith('PRAGMA '):raise ConfigurationError('SQLite-only operation on PostgreSQL')
   return Cursor(self.raw.execute(translate(sql),params if params else None))
  except self.driver.IntegrityError:raise sqlite3.IntegrityError('Database constraint rejected')from None
  except self.driver.OperationalError:raise sqlite3.OperationalError('Database unavailable')from None
 def executescript(self,script):
  for sql in script.split(';'):
   if sql.strip():self.execute(sql.strip())
 def commit(self):self.raw.commit()
 def rollback(self):self.raw.rollback()
 def close(self):self.raw.close()
