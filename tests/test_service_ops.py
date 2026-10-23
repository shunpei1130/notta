"""Operator totals keep SQLite/Postgres row and aggregate types compatible."""
import json
import sqlite3
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from database import row_factory
import service_ops as ops


class PostgresRows:
    """Execute real SQL locally, then reproduce Psycopg labels and SUM types."""
    def __init__(self, connection):
        self.connection = connection

    def execute(self, query):
        cursor = self.connection.execute(query)
        # PostgreSQL names unaliased COALESCE expressions identically, unlike
        # SQLite. Its SUM(BIGINT) is numeric/Decimal rather than an integer.
        names = [column[0].lower().split('(', 1)[0] for column in cursor.description]
        factory = row_factory(SimpleNamespace(description=[SimpleNamespace(name=name) for name in names]))
        rows = [factory(tuple(Decimal(value) if 'SUM(' in query.upper() and isinstance(value, int) else value
                              for value in row)) for row in cursor.fetchall()]
        return Rows(rows)


class Rows(list):
    def fetchone(self):
        return self[0] if self else None


class ServiceOpsTest(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(':memory:')
        self.connection.executescript('''
            CREATE TABLE svc_events(event TEXT);
            CREATE TABLE svc_payments(user_id TEXT,amount INTEGER,currency TEXT,refunded INTEGER);
            CREATE TABLE svc_refunds(amount INTEGER,status TEXT);
            CREATE TABLE svc_refund_requests(id TEXT,user_id TEXT,payment_id TEXT,requested INTEGER,status TEXT);
            CREATE TABLE svc_ai_requests(id TEXT,user_id TEXT,project TEXT,status TEXT,reserved_yen INTEGER,created INTEGER);
            CREATE TABLE svc_checkouts(id TEXT,user_id TEXT,status TEXT,created INTEGER);
            CREATE TABLE svc_budgets(month TEXT,spent_yen INTEGER,reserved_yen INTEGER);
            INSERT INTO svc_events VALUES ('payment-completed');
            INSERT INTO svc_payments VALUES ('customer',3000,'JPY',0);
        ''')
        self.addCleanup(self.connection.close)

    def report(self, connection):
        with patch.object(ops.providers, 'mail_ready', return_value=False), \
             patch.object(ops.providers, 'ai_ready', return_value=False), \
             patch.object(ops.providers, 'search_ready', return_value=False), \
             patch.object(ops.billing, 'ready', return_value=False):
            return ops.report(connection)

    def test_hybrid_rows_preserve_receipts_and_serialize_decimal_sums(self):
        result = self.report(PostgresRows(self.connection))
        self.assertEqual(result['payments'], {'count': 1, 'receivedYen': 3000, 'refundedYen': 0, 'refundPendingYen': 0})
        self.assertEqual(result['paidCustomers'], 1)
        self.assertEqual(result['events'], {'payment-completed': 1})
        self.assertEqual(json.loads(json.dumps(result))['payments']['receivedYen'], 3000)

    def test_refunds_are_separate_from_gross_receipts_on_both_backends(self):
        self.connection.executemany('INSERT INTO svc_refunds VALUES (?,?)', [(1000, 'COMPLETED'), (500, 'PENDING')])
        postgres = self.report(PostgresRows(self.connection))
        self.connection.row_factory = sqlite3.Row
        sqlite = self.report(self.connection)
        self.assertEqual(postgres, sqlite)
        self.assertEqual(postgres['payments'], {'count': 1, 'receivedYen': 3000, 'refundedYen': 1000, 'refundPendingYen': 500})
        json.dumps(postgres)


if __name__ == '__main__':
    unittest.main()
