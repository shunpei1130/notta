import copy
import datetime as dt
import hashlib
import hmac
import base64
import io
import json
import os
import secrets
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as s
import service_core as core
import service_billing as billing
import service_providers as providers
import wsgi


def draft(goal='ファンと小冊子をつくる'):
    return {'title': goal[:50], 'goal': goal, 'success': '試作1冊を企画者が確認', 'output': '6ページの試作冊子', 'schedule': '日程は企画者と相談', 'conditions': '報酬と権利は参加前に合意', 'recruitment': '一緒に試作する方を募集します。',
            'roles': [{'title':'試作する人','output':'試作1件'},{'title':'確かめる人','output':'改善メモ'}], 'questions':['参加したい理由','取り組める時間'], 'unknowns':['日程を確認']}


class FakeSquare:
    VERSION = '2026-09-16'
    location, merchant, plan = 'location', 'merchant', 'plan'
    order_id, sub_id, customer = 'checkout-order', 'subscription', 'customer'

    def __init__(self):
        self.paid = True
        self.refunded = False
        self.version = 1
        self.canceled = False
        self.extra_invoice = False
        self.wrong_template = False
        self.wrong_amount = False
        self.fail_renewal = False

    @staticmethod
    def ready(): return True
    def checkout(self, key, email): return {'id':'link','order_id':self.order_id,'url':'https://sandbox.square.link/u/test'}
    def order(self, oid):
        if oid == self.order_id: return {'id':oid,'location_id':self.location,'fulfillments':[{'pickup_details':{'recipient':{'customer_id':self.customer}}}]}
        return {'id':oid,'location_id':self.location,'total_money':{'amount':3001 if self.wrong_amount else 3000,'currency':'JPY'},'tenders':[{'payment_id':'pay2' if oid == 'order2' else 'pay1'}]}
    def subscription(self, sid):
        return {'id':sid,'location_id':self.location,'customer_id':self.customer,'plan_variation_id':self.plan,'status':'ACTIVE','timezone':'Asia/Tokyo','version':self.version,'monthly_billing_anchor_date':23,
                'phases':[{'order_template_id':'wrong' if self.wrong_template else self.order_id}], 'invoice_ids':(['invoice2'] if self.extra_invoice else []) + ['invoice1'], **({'canceled_date':'2026-11-23'} if self.canceled else {})}
    def subscriptions(self, customer): return [self.subscription(self.sub_id)]
    def invoice(self, iid):
        second = iid == 'invoice2'
        return {'id':iid,'location_id':self.location,'subscription_id':self.sub_id,'order_id':'order2' if second else 'order1','status':'UNPAID' if not self.paid or (second and self.fail_renewal) else 'REFUNDED' if self.refunded else 'PAID','payment_requests':[{'request_type':'BALANCE','due_date':'2026-11-23' if second else '2026-10-23'}]}
    def payment(self, pid):
        return {'id':pid,'location_id':self.location,'order_id':'order2' if pid == 'pay2' else 'order1','status':'COMPLETED','amount_money':{'amount':3000,'currency':'JPY'},'created_at':'2026-11-23T01:00:00Z' if pid == 'pay2' else '2026-10-23T01:00:00Z','refunded_money':{'amount':3000 if self.refunded else 0,'currency':'JPY'},'refund_ids':['refund-'+pid] if self.refunded else []}
    def refund(self, rid): return {'id':rid,'payment_id':rid.split('-',1)[1],'amount_money':{'amount':3000,'currency':'JPY'},'status':'COMPLETED'}
    def cancel(self, sid): self.canceled = True; self.version += 1; return self.subscription(sid)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        folder = Path(__file__).resolve().parents[1] / 'data'
        folder.mkdir(exist_ok=True)
        self.db_path = folder / ('test-' + s.ident() + '.sqlite3')
        s.init_db(self.db_path)
        self.c = s.connect()
        self.env = patch.dict(os.environ, {'NOTTA_AUTH_SECRET':'a'*48,'NOTTA_SERVICE_ENABLED':'yes','NOTTA_PUBLIC_ORIGIN':'https://notta.example','SQUARE_WEBHOOK_SIGNATURE_KEY':'test-key'})
        self.env.start()
        self.clock_patch = patch.object(s, 'stamp', return_value=int(dt.datetime(2026,10,23,10,tzinfo=core.JST).timestamp()))
        self.clock = self.clock_patch.start()
        self.uid = self.user()
        self.other = self.user()
        self.fan = self.user()

    def tearDown(self):
        self.c.close(); self.clock_patch.stop(); self.env.stop()
        for path in (self.db_path, Path(str(self.db_path)+'-wal'), Path(str(self.db_path)+'-shm')):
            path.unlink(missing_ok=True)

    def user(self):
        uid = s.ident()
        self.c.execute('INSERT INTO users VALUES(?,?,?,?,?,?)',(uid,uid+'@example.test','表示名','creator',secrets.token_hex(16),'disabled'))
        self.c.execute('INSERT INTO svc_accounts(user_id,verified_at,created) VALUES(?,?,?)',(uid,s.stamp(),s.stamp()))
        self.c.commit(); return uid

    def project(self, uid=None):
        return core.new_project(self.c,uid or self.uid,{'goal':'料理のファンとレシピ冊子をつくる'})['id']

    def fill(self, pid):
        row = core.project_row(self.c,pid,self.uid)
        core.edit_project(self.c,self.uid,pid,{'version':row['version'],'draft':draft()})

    def paid(self, uid=None):
        uid = uid or self.uid
        self.c.execute("INSERT INTO svc_subscriptions(id,user_id,customer_id,status,paid_until,first_paid,checked_at,period) VALUES(?,?,?,?,?,?,?,?)",(s.ident(),uid,'cust','ACTIVE',s.stamp()+86400*30,s.stamp(),s.stamp(),'period'))
        self.c.commit()

    def problem(self, status, call):
        with self.assertRaises(s.Problem) as caught: call()
        self.assertEqual(status,caught.exception.status)
        self.c.rollback()

    def test_otp_single_use_attempt_limit_and_account(self):
        with patch.object(providers,'mail_ready',return_value=True), patch.object(providers,'send_code') as send:
            start = core.auth_start(self.c,{'email':'new@example.test','name':'新しい人','consent':True},'local')
        code = send.call_args.args[1]
        self.assertNotIn(code,json.dumps(start))
        for _ in range(5): self.problem(403,lambda:core.auth_verify(self.c,{'challenge':start['challenge'],'code':'000000' if code != '000000' else '000001'}))
        self.problem(403,lambda:core.auth_verify(self.c,{'challenge':start['challenge'],'code':code}))
        self.clock.return_value += 61
        with patch.object(providers,'mail_ready',return_value=True), patch.object(providers,'send_code') as send:
            start = core.auth_start(self.c,{'email':'new@example.test','name':'新しい人','consent':True},'local')
        result,token = core.auth_verify(self.c,{'challenge':start['challenge'],'code':send.call_args.args[1]})
        self.assertEqual(result['user']['email'],'new@example.test'); self.assertTrue(token)
        self.problem(403,lambda:core.auth_verify(self.c,{'challenge':start['challenge'],'code':send.call_args.args[1]}))

    def test_only_owner_sees_private_draft_and_free_cannot_publish(self):
        pid = self.project(); self.fill(pid)
        self.problem(404,lambda:core.project_detail(self.c,self.other,pid))
        self.problem(402,lambda:core.publish(self.c,self.uid,pid,{'version':2,'consent':True}))
        slug=core.project_row(self.c,pid,self.uid)['slug']
        self.problem(404,lambda:core.public_project(self.c,slug))
        self.paid(); core.publish(self.c,self.uid,pid,{'version':2,'consent':True})
        public=core.public_project(self.c,slug)
        self.assertNotIn('unknowns',public); self.assertNotIn('messages',public); self.assertNotIn('email',public)

    def test_fan_workflow_versions_cancel_withdraws_contribution(self):
        pid=self.project(); self.fill(pid); self.paid(); core.publish(self.c,self.uid,pid,{'version':2,'consent':True})
        role=core.project_detail(self.c,self.uid,pid)['roles'][0]['id']
        aid=core.apply(self.c,self.fan,pid,{'task':role,'experience':'試作できます','consent':True})['id']
        self.problem(403,lambda:core.application_action(self.c,self.fan,aid,'status',{'version':1,'status':'担当'}))
        core.application_action(self.c,self.uid,aid,'status',{'version':1,'status':'担当'})
        self.problem(409,lambda:core.application_action(self.c,self.fan,aid,'submit',{'version':1,'body':'古い版'}))
        core.application_action(self.c,self.fan,aid,'submit',{'version':2,'body':'試作の結果 https://example.test/result'})
        core.application_action(self.c,self.uid,aid,'status',{'version':3,'status':'差戻し','comment':'手順の追記をお願いします'})
        core.application_action(self.c,self.fan,aid,'submit',{'version':4,'body':'追記した結果'})
        core.application_action(self.c,self.uid,aid,'status',{'version':5,'status':'確認済','comment':'試作と手順を確認しました'})
        self.assertEqual(core.project_detail(self.c,self.uid,pid)['applications'][0]['confirmation'],'試作と手順を確認しました')
        core.application_action(self.c,self.fan,aid,'cancel',{'version':6})
        row=self.c.execute('SELECT status,confirmation,confirmed_at FROM applications WHERE id=?',(aid,)).fetchone()
        self.assertEqual(tuple(row),('取消','',None))

    def test_no_forced_fourteen_day_expiry_next_project_after_archive(self):
        pid=self.project(); self.paid()
        self.clock.return_value += 16*86400
        self.fill(pid)
        self.problem(409,lambda:self.project())
        core.archive(self.c,self.uid,pid,{'version':2,'confirm':True})
        self.assertTrue(self.project())

    def test_ai_result_idempotency_quota_and_budget(self):
        pid=self.project()
        data={'requestId':s.ident(),'prompt':'小冊子にしてください','version':1}
        with patch.object(providers,'ai_ready',return_value=True),patch.object(providers,'generate',return_value={'reply':'小さな試作から始めましょう','draft':draft(),'cost_yen':3,'sources':[]}) as generate:
            first=core.assist(self.c,self.uid,pid,data); second=core.assist(self.c,self.uid,pid,data)
            self.assertEqual(first,second); self.assertEqual(generate.call_count,1)
        self.assertEqual(core.quota(self.c,self.uid)['repliesUsed'],1)
        self.assertEqual(core.budget(self.c)['spent_yen'],3)
        self.assertEqual(core.budget(self.c)['reserved_yen'],0)
        self.assertEqual(core.project_detail(self.c,self.uid,pid)['draft']['output'],'6ページの試作冊子')

    def test_uncertain_ai_keeps_reservation_no_blind_retry(self):
        pid=self.project(); data={'requestId':s.ident(),'prompt':'企画をつくる','version':1}
        with patch.object(providers,'ai_ready',return_value=True),patch.object(providers,'generate',side_effect=providers.ProviderError('応答不明',uncertain=True)) as gen:
            with self.assertRaises(providers.ProviderError): core.assist(self.c,self.uid,pid,data)
            self.problem(409,lambda:core.assist(self.c,self.uid,pid,data))
            self.assertEqual(gen.call_count,1)
        self.assertEqual(core.budget(self.c)['reserved_yen'],10)

    def test_budget_and_trial_admission_preserve_paid_capacity(self):
        self.c.execute("INSERT INTO svc_budgets VALUES('2026-10',995,0)"); self.c.commit()
        self.problem(429,lambda:self.project())
        self.c.execute("UPDATE svc_budgets SET spent_yen=810"); self.c.commit()
        self.assertTrue(self.project())

    def test_trial_admission_reserves_every_members_remaining_replies(self):
        for _ in range(5): self.project(self.user())
        self.problem(429, lambda:self.project(self.user()))
        self.assertEqual(core.commitments(self.c), 2950)

    def test_free_quota_blocks_eleventh_reply_without_provider_call(self):
        pid=self.project()
        with patch.object(providers,'ai_ready',return_value=True), patch.object(providers,'generate',return_value={'reply':'案を更新','draft':draft(),'cost_yen':3,'sources':[]}) as generate:
            for version in range(1,11): core.assist(self.c,self.uid,pid,{'version':version,'prompt':'企画を整える','requestId':s.ident()})
            self.problem(429,lambda:core.assist(self.c,self.uid,pid,{'version':11,'prompt':'企画を整える','requestId':s.ident()}))
        self.assertEqual(generate.call_count,10)
        self.assertEqual([r['role'] for r in core.project_detail(self.c,self.uid,pid)['messages'][-2:]],['user','assistant'])

    def test_free_trial_cannot_be_restarted_after_archive(self):
        pid=self.project(); core.archive(self.c,self.uid,pid,{'version':1,'confirm':True})
        self.problem(402,lambda:self.project())

    def test_refund_period_uses_first_payment_and_closes_after_seven_days(self):
        fake=FakeSquare(); self.start_checkout(fake)
        with patch.object(providers,'Square',return_value=fake): billing.reconcile(self.c,self.uid)
        self.clock.return_value += 8*86400
        self.assertFalse(billing.summary(self.c,self.uid)['refundEligible'])
        self.problem(409,lambda:billing.request_refund(self.c,self.uid,{'confirm':True}))

    def test_expired_owners_contract_never_charges_fan_or_accepts_submission(self):
        pid=self.project(); self.fill(pid); self.paid(); core.publish(self.c,self.uid,pid,{'version':2,'consent':True})
        role=core.project_detail(self.c,self.uid,pid)['roles'][0]['id']
        aid=core.apply(self.c,self.fan,pid,{'task':role,'experience':'参加希望','consent':True})['id']
        core.application_action(self.c,self.uid,aid,'status',{'version':1,'status':'担当'})
        self.clock.return_value += 31*86400
        self.problem(409,lambda:core.application_action(self.c,self.fan,aid,'submit',{'version':2,'body':'成果'}))

    def start_checkout(self, fake):
        with patch.object(providers,'Square',return_value=fake):
            billing.checkout(self.c,{'id':self.uid,'email':'owner@example.test'},{'consent':True,'terms':core.TERMS})

    def test_checkout_reuse_and_unpaid_subscription_never_grants(self):
        fake=FakeSquare(); fake.paid=False
        self.start_checkout(fake)
        with patch.object(providers,'Square',return_value=fake):
            result=billing.checkout(self.c,{'id':self.uid,'email':'owner@example.test'},{'consent':True,'terms':core.TERMS})
            billing.reconcile(self.c,self.uid)
        self.assertTrue(result['url']); self.assertFalse(core.paid(self.c,self.uid))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM svc_checkouts').fetchone()[0],1)

    def test_unpaid_checkout_can_resume_but_another_user_cannot_see_its_link(self):
        fake=FakeSquare(); self.start_checkout(fake)
        self.assertEqual(billing.summary(self.c,self.uid)['pendingUrl'],'https://sandbox.square.link/u/test')
        self.assertIsNone(billing.summary(self.c,self.other)['pendingUrl'])

    def test_payment_renew_failure_cancel_and_refund(self):
        fake=FakeSquare(); self.start_checkout(fake)
        with patch.object(providers,'Square',return_value=fake):
            billing.reconcile(self.c,self.uid); self.assertTrue(core.paid(self.c,self.uid))
            initial=core.subscription(self.c,self.uid)['paid_until']
            self.clock.return_value += 31*86400
            fake.extra_invoice=True; fake.fail_renewal=True; fake.version+=1
            billing.reconcile(self.c,self.uid); self.assertEqual(core.subscription(self.c,self.uid)['paid_until'],initial)
            fake.fail_renewal=False; fake.version+=1
            billing.reconcile(self.c,self.uid); self.assertGreater(core.subscription(self.c,self.uid)['paid_until'],initial)
            billing.cancel(self.c,self.uid,{'confirm':True}); self.assertTrue(core.paid(self.c,self.uid)); self.assertTrue(core.subscription(self.c,self.uid)['cancel_at'])
            fake.refunded=True; fake.version+=1
            billing.reconcile(self.c,self.uid); self.assertFalse(core.paid(self.c,self.uid))
            billing.reconcile(self.c,self.uid); self.assertFalse(core.paid(self.c,self.uid))

    def test_wrong_order_template_never_links_by_email(self):
        fake=FakeSquare(); fake.wrong_template=True; self.start_checkout(fake)
        with patch.object(providers,'Square',return_value=fake): billing.reconcile(self.c,self.uid)
        self.assertIsNone(core.subscription(self.c,self.uid))

    def test_wrong_amount_does_not_grant(self):
        fake=FakeSquare(); fake.wrong_amount=True; self.start_checkout(fake)
        with patch.object(providers,'Square',return_value=fake): self.problem(503,lambda:billing.reconcile(self.c,self.uid))
        self.assertFalse(core.paid(self.c,self.uid))

    def test_webhook_signature_duplicate_and_late_event(self):
        fake=FakeSquare(); self.start_checkout(fake)
        data={'event_id':'event1','merchant_id':'merchant','type':'invoice.payment_made','data':{'untrusted':'ignored'}}
        raw=json.dumps(data).encode(); signature=base64.b64encode(hmac.new(b'test-key',b'https://notta.example/api/service/square/webhook'+raw,hashlib.sha256).digest()).decode()
        self.problem(403,lambda:billing.webhook(self.c,raw,'bad'))
        with patch.object(providers,'Square',return_value=fake):
            billing.webhook(self.c,raw,signature)
            self.assertTrue(billing.webhook(self.c,raw,signature)['duplicate'])
            fake.refunded=True; fake.version+=1
            data['event_id']='late-event'; late=json.dumps(data).encode(); sig=base64.b64encode(hmac.new(b'test-key',b'https://notta.example/api/service/square/webhook'+late,hashlib.sha256).digest()).decode()
            billing.webhook(self.c,late,sig)
        self.assertFalse(core.paid(self.c,self.uid)); self.assertEqual(self.c.execute('SELECT COUNT(*) FROM svc_payments').fetchone()[0],1)

    def test_initial_refund_request_records_notice_once(self):
        fake=FakeSquare(); self.start_checkout(fake)
        with patch.object(providers,'Square',return_value=fake):
            billing.reconcile(self.c,self.uid)
            billing.request_refund(self.c,self.uid,{'confirm':True}); billing.request_refund(self.c,self.uid,{'confirm':True})
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM svc_refund_requests').fetchone()[0],1)
        fake.refunded=True; fake.version+=1
        with patch.object(providers,'Square',return_value=fake): billing.reconcile(self.c,self.uid)
        self.assertEqual(self.c.execute('SELECT status FROM svc_refund_requests').fetchone()[0],'completed')

    def test_search_is_separate_from_private_project_and_chat(self):
        private=draft('非公開の企画の目的')
        search_response={'candidates':[{'content':{'parts':[{'text':'公開された事例です'}]},'groundingMetadata':{'webSearchQueries':['小冊子 共同制作'],'groundingChunks':[{'web':{'title':'公開事例','uri':'https://example.test'}}],'searchEntryPoint':{'renderedContent':'<p>Google</p>'}}}],'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':30}}
        model_response={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps({'reply':'企画の案','draft':private})}]}}],'usageMetadata':{'promptTokenCount':500,'candidatesTokenCount':1000}}
        with patch.dict(os.environ,{'GEMINI_API_KEY':'test-only','NOTTA_AI_ENABLED':'yes','NOTTA_SEARCH_LIMIT_VERIFIED':'yes'}),patch.object(providers,'request_json',side_effect=[search_response,model_response]) as transport:
            result=providers.generate(private,[{'role':'user','body':'非公開の会話'}],'役割を考える','小冊子 共同制作')
        first=json.dumps(transport.call_args_list[0].kwargs['data'],ensure_ascii=False)
        self.assertNotIn('非公開',first)
        self.assertEqual(result['search_queries'],1)
        self.assertEqual(result['search_entry_point'],'<p>Google</p>')

    def test_public_mode_blocks_legacy_endpoints_and_private_reads(self):
        application=wsgi.create_application(s.DB,initialize=False)
        def get(path):
            result={}; body=application({'REQUEST_METHOD':'GET','PATH_INFO':path,'SERVER_PORT':'8765','HTTP_HOST':'localhost:8765','wsgi.input':io.BytesIO()},lambda status,headers:result.update(status=int(status.split()[0]),headers=dict(headers)))
            return result,b''.join(body)
        result,body=get('/'); self.assertEqual(result['status'],200); self.assertIn('service.js',body.decode())
        self.assertEqual(get('/api/projects')[0]['status'],404)
        self.assertEqual(get('/api/service/me')[0]['status'],401)


if __name__ == '__main__': unittest.main()
