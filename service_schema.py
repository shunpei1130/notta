"""Additive schema for the public Notta service. No production startup DDL."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS svc_accounts(user_id TEXT PRIMARY KEY REFERENCES users(id), verified_at INTEGER NOT NULL, trial_allocated INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS svc_auth(id TEXT PRIMARY KEY,email TEXT NOT NULL,name TEXT NOT NULL,ip_hash TEXT NOT NULL,code_hash TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,expires INTEGER NOT NULL,created INTEGER NOT NULL,used INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS svc_auth_email ON svc_auth(email,created);
CREATE INDEX IF NOT EXISTS svc_auth_ip ON svc_auth(ip_hash,created);
CREATE TABLE IF NOT EXISTS svc_projects(project TEXT PRIMARY KEY REFERENCES projects(id),slug TEXT UNIQUE NOT NULL,state TEXT NOT NULL,draft TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,created INTEGER NOT NULL,updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS svc_messages(id TEXT PRIMARY KEY,project TEXT NOT NULL REFERENCES projects(id),role TEXT NOT NULL,body TEXT NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS svc_ai_requests(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),project TEXT NOT NULL REFERENCES projects(id),fingerprint TEXT NOT NULL,mode TEXT NOT NULL,period TEXT NOT NULL,status TEXT NOT NULL,reserved_yen INTEGER NOT NULL,cost_yen INTEGER,search_used INTEGER NOT NULL,created INTEGER NOT NULL,result TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS svc_ai_owner ON svc_ai_requests(user_id,mode,period,status);
CREATE TABLE IF NOT EXISTS svc_budgets(month TEXT PRIMARY KEY,spent_yen INTEGER NOT NULL DEFAULT 0,reserved_yen INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS svc_checkouts(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),status TEXT NOT NULL,link_id TEXT,order_id TEXT UNIQUE,url TEXT,created INTEGER NOT NULL,month TEXT NOT NULL,terms TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS svc_subscriptions(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),customer_id TEXT NOT NULL,status TEXT NOT NULL,paid_until INTEGER NOT NULL DEFAULT 0,cancel_at INTEGER,first_paid INTEGER NOT NULL DEFAULT 0,refunded INTEGER NOT NULL DEFAULT 0,checked_at INTEGER NOT NULL,period TEXT NOT NULL DEFAULT '',provider_version INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS svc_sub_owner ON svc_subscriptions(user_id,status);
CREATE TABLE IF NOT EXISTS svc_payments(id TEXT PRIMARY KEY,subscription_id TEXT NOT NULL REFERENCES svc_subscriptions(id),user_id TEXT NOT NULL REFERENCES users(id),invoice_id TEXT UNIQUE NOT NULL,amount INTEGER NOT NULL,currency TEXT NOT NULL,completed INTEGER NOT NULL,period_end INTEGER NOT NULL,refunded INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS svc_refunds(id TEXT PRIMARY KEY,payment_id TEXT NOT NULL REFERENCES svc_payments(id),amount INTEGER NOT NULL,status TEXT NOT NULL,updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS svc_refund_requests(id TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),payment_id TEXT UNIQUE NOT NULL REFERENCES svc_payments(id),requested INTEGER NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS svc_webhooks(id TEXT PRIMARY KEY,event_type TEXT NOT NULL,digest TEXT NOT NULL,status TEXT NOT NULL,created INTEGER NOT NULL,updated INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS svc_events(id TEXT PRIMARY KEY,user_id TEXT,event TEXT NOT NULL,project TEXT,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS svc_intakes(id TEXT PRIMARY KEY,user_id TEXT UNIQUE NOT NULL REFERENCES users(id),goal TEXT NOT NULL,state TEXT NOT NULL,current_step INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1,created INTEGER NOT NULL,updated INTEGER NOT NULL,submitted INTEGER,result_draft TEXT NOT NULL DEFAULT '{}',draft_version INTEGER NOT NULL DEFAULT 0,result_published TEXT NOT NULL DEFAULT '{}',result_version INTEGER NOT NULL DEFAULT 0,published_draft_version INTEGER NOT NULL DEFAULT 0,ready_at INTEGER);
CREATE TABLE IF NOT EXISTS svc_intake_turns(intake_id TEXT NOT NULL REFERENCES svc_intakes(id),number INTEGER NOT NULL,theme TEXT NOT NULL,question TEXT NOT NULL,answer TEXT,answered_at INTEGER,request_id TEXT UNIQUE,PRIMARY KEY(intake_id,number));
CREATE TABLE IF NOT EXISTS svc_intake_ai_requests(id TEXT PRIMARY KEY,intake_id TEXT NOT NULL REFERENCES svc_intakes(id),number INTEGER NOT NULL,month TEXT NOT NULL,status TEXT NOT NULL,reserved_yen INTEGER NOT NULL,cost_yen INTEGER,created INTEGER NOT NULL,result TEXT NOT NULL DEFAULT '',error TEXT NOT NULL DEFAULT '',UNIQUE(intake_id,number));
CREATE TABLE IF NOT EXISTS svc_intake_notifications(id TEXT PRIMARY KEY,intake_id TEXT NOT NULL REFERENCES svc_intakes(id),kind TEXT NOT NULL,revision INTEGER NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,idempotency_key TEXT UNIQUE NOT NULL,provider_id TEXT NOT NULL DEFAULT '',created INTEGER NOT NULL,updated INTEGER NOT NULL,attempted_at INTEGER,error TEXT NOT NULL DEFAULT '',UNIQUE(intake_id,kind,revision));
CREATE INDEX IF NOT EXISTS svc_intake_state ON svc_intakes(state,updated);
CREATE INDEX IF NOT EXISTS svc_intake_mail ON svc_intake_notifications(status,updated);
"""


def migrate(connection):
    connection.executescript(SCHEMA)
