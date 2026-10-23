"""Read-only launch readiness, unresolved operations and acquisition metrics."""
import argparse
import json
import os

import server
import service_providers as providers


def report(connection):
    counts = {row[0]: row[1] for row in connection.execute('SELECT event,COUNT(*) FROM svc_events GROUP BY event')}
    payments = connection.execute('SELECT COUNT(*),COALESCE(SUM(amount),0),COALESCE(SUM(CASE WHEN refunded=1 THEN amount ELSE 0 END),0) FROM svc_payments').fetchone()
    return {'readiness': {'mail': providers.mail_ready(), 'ai': providers.ai_ready(), 'search': providers.search_ready(), 'billing': providers.Square.ready()},
            'events': counts, 'payments': {'count':payments[0],'receivedYen':payments[1],
                'refundedYen':connection.execute("SELECT COALESCE(SUM(amount),0) FROM svc_refunds WHERE status='COMPLETED'").fetchone()[0],
                'refundPendingYen':connection.execute("SELECT COALESCE(SUM(amount),0) FROM svc_refunds WHERE status='PENDING'").fetchone()[0]},
            'paidCustomers':connection.execute("SELECT COUNT(DISTINCT user_id) FROM svc_payments WHERE amount=3000 AND currency='JPY' AND refunded=0").fetchone()[0],
            'refundRequests':[dict(r) for r in connection.execute("SELECT id,user_id,payment_id,requested,status FROM svc_refund_requests WHERE status!='completed'")],
            'unresolvedAI':[dict(r) for r in connection.execute("SELECT id,user_id,project,status,reserved_yen,created FROM svc_ai_requests WHERE status IN ('unknown','running')")],
            'unresolvedCheckouts':[dict(r) for r in connection.execute("SELECT id,user_id,status,created FROM svc_checkouts WHERE status IN ('unknown','reserved','pending')")],
            'budgets':[dict(r) for r in connection.execute('SELECT * FROM svc_budgets ORDER BY month DESC')]}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--db'); args=parser.parse_args()
    backend=os.getenv('NOTTA_DATABASE_BACKEND','sqlite')
    server.init_db(os.getenv('NOTTA_DATABASE_URL') if backend=='postgres' else args.db,backend=backend,initialize=False)
    with server.connect() as connection: print(json.dumps(report(connection),ensure_ascii=False,indent=2))
