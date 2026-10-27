"""Explicit, additive migration. Never runs as part of a deployed request."""
import argparse
import os
from pathlib import Path
from urllib.parse import urlparse

from database import Postgres
import service_schema


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--validation', action='store_true', help='use the private validation branch configuration')
    parser.add_argument('--expected-host', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.validation:
        values = dict(line.split('=', 1) for line in (Path(__file__).parent/'.env.validation').read_text(encoding='utf-8').splitlines() if '=' in line)
        dsn = values.get('NOTTA_VALIDATION_DATABASE_URL', '')
    else:
        dsn = os.getenv('NOTTA_DATABASE_URL', '')
    if not dsn or urlparse(dsn).hostname != args.expected_host:
        raise SystemExit('Target mismatch; no database changes performed')
    tables = ['svc_accounts','svc_auth','svc_projects','svc_messages','svc_ai_requests','svc_budgets','svc_checkouts','svc_subscriptions','svc_payments','svc_refunds','svc_refund_requests','svc_webhooks','svc_events','svc_intakes','svc_intake_turns','svc_intake_ai_requests','svc_intake_notifications']
    with Postgres(dsn) as connection:
        existing = connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'").fetchall()
        before = len(existing)
        if not args.apply:
            print('Migration ready: additive service tables; existing tables:', before)
            return
        connection.execute('BEGIN IMMEDIATE')
        service_schema.migrate(connection)
        # Existing application role remains DML-only. It cannot create schema objects.
        connection.execute('GRANT SELECT,INSERT,UPDATE,DELETE ON ' + ','.join(tables) + ' TO notta_runtime')
        sequences = connection.execute("SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema='public'").fetchall()
        for row in sequences:
            if row[0].startswith('svc_'):
                connection.execute('GRANT USAGE,SELECT ON SEQUENCE ' + row[0] + ' TO notta_runtime')
        connection.commit()
        found = {r[0] for r in connection.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
        if not set(tables) <= found:
            raise RuntimeError('Migration verification failed')
        for table in tables:
            permissions = connection.execute("SELECT has_table_privilege('notta_runtime',?,'SELECT') AS can_read,has_table_privilege('notta_runtime',?,'INSERT') AS can_insert,has_table_privilege('notta_runtime',?,'UPDATE') AS can_update,has_table_privilege('notta_runtime',?,'DELETE') AS can_delete", (table,table,table,table)).fetchone()
            if not all(permissions[i] for i in range(4)):
                raise RuntimeError('Runtime permission missing for ' + table)
        print('Migration verified:', len(tables), 'service tables; runtime access verified; existing tables preserved:', before)


if __name__ == '__main__': main()
