"""Opt-in, fictional fixtures only, on the explicitly selected validation branch."""
import argparse
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server as s
import service_core as core
import service_providers as providers
from test_service import draft


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--expected-host',required=True); args=parser.parse_args()
    values=dict(line.split('=',1) for line in (Path(__file__).resolve().parents[1]/'.env.validation').read_text(encoding='utf-8').splitlines() if '=' in line)
    dsn=values['NOTTA_VALIDATION_RUNTIME_URL']
    if urlparse(dsn).hostname != args.expected_host: raise SystemExit('Validation branch mismatch')
    s.init_db(dsn,backend='postgres',initialize=False)
    with s.connect() as c:
        fixtures=[r[0] for r in c.execute("SELECT id FROM users WHERE email LIKE ? AND name=?", ('validation-%@example.test','検証用の架空利用者'))]
        for fixture in fixtures:
            c.execute("UPDATE svc_projects SET state='archived' WHERE project IN (SELECT id FROM projects WHERE owner=?)", (fixture,))
            c.execute("UPDATE svc_subscriptions SET status='CANCELED',paid_until=0 WHERE user_id=? AND customer_id='validation'", (fixture,))
        c.commit()
        uid=s.ident(); fan=s.ident()
        for account in (uid,fan):
            c.execute('INSERT INTO users(id,email,name,role,salt,hash) VALUES(?,?,?,?,?,?)',(account,'validation-'+account+'@example.test','検証用の架空利用者','creator',secrets.token_hex(16),'disabled'))
            c.execute('INSERT INTO svc_accounts(user_id,verified_at,created) VALUES(?,?,?)',(account,s.stamp(),s.stamp()))
        c.commit()
        pid=core.new_project(c,uid,{'goal':'検証用：ファンと季節の小冊子をつくる'})['id']
        with patch.object(providers,'ai_ready',return_value=True),patch.object(providers,'generate',return_value={'reply':'試作から始めましょう','draft':draft(),'cost_yen':3,'sources':[]}):
            core.assist(c,uid,pid,{'version':1,'prompt':'検証用の企画','requestId':s.ident()})
        c.execute("INSERT INTO svc_subscriptions(id,user_id,customer_id,status,paid_until,checked_at,period) VALUES(?,?,?,?,?,?,?)",(s.ident(),uid,'validation','ACTIVE',s.stamp()+86400,s.stamp(),'validation'))
        c.commit()
        core.publish(c,uid,pid,{'version':2,'consent':True})
        role=core.project_detail(c,uid,pid)['roles'][0]['id']
        aid=core.apply(c,fan,pid,{'task':role,'experience':'検証用の応募です','consent':True})['id']
        core.application_action(c,uid,aid,'status',{'version':1,'status':'担当'})
        core.application_action(c,fan,aid,'submit',{'version':2,'body':'検証用の成果物'})
        core.application_action(c,uid,aid,'status',{'version':3,'status':'確認済','comment':'検証用の成果を確認'})
        assert core.project_detail(c,uid,pid)['applications'][0]['status']=='確認済'
        core.archive(c,uid,pid,{'version':core.project_row(c,pid,uid)['version'],'confirm':True})
        c.execute("UPDATE svc_subscriptions SET status='CANCELED',paid_until=0 WHERE user_id=? AND customer_id='validation'", (uid,))
        c.commit()
        print('PASS: restricted runtime role; PostgreSQL save/read; AI boundary stub; publish/apply/assign/submit/confirm')


if __name__=='__main__': main()
