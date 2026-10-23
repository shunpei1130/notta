"""Read-only launch readiness, unresolved operations and acquisition metrics."""
import argparse
import json
import os

import server
import service_providers as providers
import service_stripe as billing


def report(connection):
    counts = {row['event']: int(row['event_count']) for row in connection.execute('SELECT event,COUNT(*) AS event_count FROM svc_events GROUP BY event')}
    payments = connection.execute('SELECT COUNT(*) AS payment_count,COALESCE(SUM(amount),0) AS received_yen FROM svc_payments').fetchone()
    return {'readiness': {'mail': providers.mail_ready(), 'ai': providers.ai_ready(), 'search': providers.search_ready(), 'billing': billing.ready()},
            'billingEnvironment': os.getenv('STRIPE_ENVIRONMENT', 'sandbox'),
            'acquisitionEligible': os.getenv('STRIPE_ENVIRONMENT') == 'production',
            'events': counts, 'payments': {'count':int(payments['payment_count']),'receivedYen':int(payments['received_yen']),
                'refundedYen':int(connection.execute("SELECT COALESCE(SUM(amount),0) AS refunded_yen FROM svc_refunds WHERE status='COMPLETED'").fetchone()['refunded_yen']),
                'refundPendingYen':int(connection.execute("SELECT COALESCE(SUM(amount),0) AS pending_yen FROM svc_refunds WHERE status='PENDING'").fetchone()['pending_yen'])},
            'paidCustomers':int(connection.execute("SELECT COUNT(DISTINCT user_id) AS customer_count FROM svc_payments WHERE amount=3000 AND currency='JPY' AND refunded=0").fetchone()['customer_count']),
            'refundRequests':[dict(r) for r in connection.execute("SELECT id,user_id,payment_id,requested,status FROM svc_refund_requests WHERE status!='completed'")],
            'unresolvedAI':[dict(r) for r in connection.execute("SELECT id,user_id,project,status,reserved_yen,created FROM svc_ai_requests WHERE status IN ('unknown','running')")],
            'unresolvedCheckouts':[dict(r) for r in connection.execute("SELECT id,user_id,status,created FROM svc_checkouts WHERE status IN ('unknown','reserved','pending')")],
            'budgets':[dict(r) for r in connection.execute('SELECT * FROM svc_budgets ORDER BY month DESC')]}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--db'); args=parser.parse_args()
    backend=os.getenv('NOTTA_DATABASE_BACKEND','sqlite')
    server.init_db(os.getenv('NOTTA_DATABASE_URL') if backend=='postgres' else args.db,backend=backend,initialize=False)
    with server.connect() as connection: print(json.dumps(report(connection),ensure_ascii=False,indent=2))
