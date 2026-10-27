import sys,unittest
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).parents[1]))
import database
class AdapterContract(unittest.TestCase):
 def test_compact_bindings_have_sql_token_boundaries(self):
  sql=database.translate('SELECT role FROM oc_choices WHERE owner=?AND project=?AND role=?')
  self.assertEqual(sql.count(' %s '),3);self.assertNotIn('%sAND',sql)
 def test_literal_question_and_percent_unchanged(self):
  sql=database.translate("SELECT '?' AS q,'100%' AS pct WHERE ?OR id=?")
  self.assertIn("'?'",sql);self.assertIn("'100%'",sql);self.assertNotIn('%sOR',sql)
 def test_driver_transactions_parameter_binding_and_secret_redaction(self):
  class Error(Exception):pass
  class Raw:
   def __init__(self):self.calls=[];self.committed=False;self.closed=False;self.rolled_back=False
   def execute(self,q,p=None):self.calls.append((q,p));return SimpleNamespace(rowcount=1,fetchone=lambda:None,fetchall=lambda:[])
   def commit(self):self.committed=True
   def rollback(self):self.rolled_back=True
   def close(self):self.closed=True
  raw=Raw();driver=SimpleNamespace(connect=lambda *a,**k:raw,IntegrityError=Error,OperationalError=Error)
  with database.Postgres('postgresql://fictional',driver=driver)as c:
   c.execute('BEGIN IMMEDIATE');c.execute('SELECT * FROM tasks WHERE id=?',("'; DROP TABLE users;--",));c.execute('PRAGMA table_info(tasks)')
  bound=next(x for x in raw.calls if x[0].startswith('SELECT * FROM tasks'))
  self.assertEqual(bound[1],("'; DROP TABLE users;--",));self.assertNotIn('DROP TABLE',bound[0]);self.assertTrue(any('pg_advisory_xact_lock'in q for q,_ in raw.calls));self.assertTrue(any('SET LOCAL statement_timeout'in q for q,_ in raw.calls));self.assertTrue(raw.committed and raw.closed)
  raw=Raw()
  with self.assertRaises(ValueError):
   with database.Postgres('postgresql://fictional',driver=driver):raise ValueError('test failure')
  self.assertTrue(raw.rolled_back and raw.closed)
  def fail(*a,**kw):raise Error('do not expose fictional secret')
  driver.connect=fail
  with self.assertRaises(database.ConfigurationError)as e:database.Postgres('postgresql://fictional-secret',driver=driver)
  self.assertNotIn('fictional-secret',str(e.exception))
 def test_row_factory_handles_schema_statements_and_index_access(self):
  self.assertEqual(database.row_factory(SimpleNamespace(description=None))(()),{})
  row=database.row_factory(SimpleNamespace(description=[SimpleNamespace(name='id'),SimpleNamespace(name='title')]))(('a','b'));self.assertEqual(row[0],'a');self.assertEqual(dict(row),{'id':'a','title':'b'})
if __name__=='__main__':unittest.main()
