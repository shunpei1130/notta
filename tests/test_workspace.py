import copy
import json
import os
import unittest
from unittest.mock import patch

import test_service as fixtures
import test_intake as intake_fixtures
import server as s
import service_core as core
import service_intake as intake
import service_workspace as work
import service_providers as providers
import workspace_ai


def initial():
    return {"title": "Christmas オふう会", "overview": "本人用の概要。予算は未定。", "firstDeliverable": "開催準備シート",
            "tasks": [{"title": f"作業{i}", "instructions": "候補を整理する", "role": "協力者", "estimate": "30分", "completion": "比較表"} for i in range(14)]}


class WorkspaceTest(unittest.TestCase):
    user = fixtures.ServiceTest.user
    problem = fixtures.ServiceTest.problem
    request = intake_fixtures.IntakeTest.request

    def setUp(self):
        fixtures.ServiceTest.setUp(self)
        self.flags = patch.dict(os.environ, {"NOTTA_INTAKE_ENABLED": "yes", "NOTTA_WORKSPACE_ENABLED": "yes", "OPENAI_API_KEY": "test-only-key", "NOTTA_AI_ENABLED": "yes"})
        self.flags.start()
        self.operator = self.user()
        self.c.execute("UPDATE users SET email=? WHERE id=?", (intake.OPERATOR, self.operator))
        self.c.commit()
        self.providers = patch.object(providers, "interview_ready", return_value=False)
        self.providers.start()
        self.mail = patch.object(providers, "send_intake_notification", return_value={"id": "fixture"}).start()
        item = intake.start(self.c, self.uid, {"goal": "オフ会を開催したい"})
        while item["state"] == "interview":
            turn = next(t for t in item["turns"] if t["number"] == item["step"])
            item = intake.answer(self.c, self.uid, item["id"], {"step": item["step"], "answer": "未定", "version": item["version"], "questionHash": turn["questionHash"], "requestId": s.ident()})
        self.intake_id = item["id"]
        self.data = {"result": initial(), "draftVersion": 0, "recruitTaskIndexes": [1, 5, 7, 8]}
        self.pid = work.deliver(self.c, self.operator, self.intake_id, self.data)["projectId"]

    def tearDown(self):
        patch.stopall()
        fixtures.ServiceTest.tearDown(self)

    def get(self):
        return work.detail(self.c, self.uid, self.pid)

    def edit(self, draft):
        return work.edit(self.c, self.uid, self.pid, {"version": self.get()["version"], "draft": draft})

    def published(self):
        d = self.get()["draft"]
        d["public"] = {"title": "公開用タイトル", "overview": "公開用概要", "schedule": "12/19", "conditions": "条件は当事者と合意", "recruitment": "一緒につくる方を募集"}
        for t in d["tasks"]:
            t["termsConfirmed"] = True
            t["offer"]["terms"] = "協力は任意。参加費と実費は事前に合意"
        self.edit(d)
        w = self.get()
        work.publish(self.c, self.uid, self.pid, {"version": w["version"], "consent": True})
        return self.get()

    def applied(self, fan=None):
        w = self.published()
        tid = w["published"]["roles"][0]["id"]
        aid = work.apply(self.c, fan or self.fan, self.pid, {"task": tid, "experience": "会場探しを手伝えます", "consent": True, "publicationRevision": work.public(self.c, w["slug"])["publicationRevision"]})["id"]
        return aid, tid

    def output(self):
        return {"reply": "進行表を資料に保存しました。", "changes": [], "newTasks": [], "removeTaskIds": [], "artifacts": [{"id": "", "title": "当日の進行表", "body": "17:30 受付\n18:00 歓談", "taskId": ""}]}

    def test_delivery_is_private_free_idempotent_and_never_notifies(self):
        self.assertEqual(len(self.get()["draft"]["tasks"]), 14)
        self.assertEqual(self.get()["state"], "draft")
        self.assertEqual(self.get()["access"]["source"], "first_project")
        self.assertFalse(core.paid(self.c, self.uid))
        self.assertEqual(self.mail.call_count, 1)  # Receipt only; delivering has no mail I/O.
        self.assertEqual(work.deliver(self.c, self.operator, self.intake_id, self.data), {"projectId": self.pid, "created": False})
        self.assertEqual(intake.detail(self.c, self.uid, self.intake_id)["projectId"], self.pid)
        self.problem(404, lambda: work.public(self.c, self.get()["slug"]))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM svc_subscriptions").fetchone()[0], 0)

    def test_owner_and_operator_read_permissions(self):
        self.problem(404, lambda: work.detail(self.c, self.other, self.pid))
        self.problem(403, lambda: work.operator_detail(self.c, self.other, self.intake_id))
        self.assertEqual(work.operator_detail(self.c, self.operator, self.intake_id)["id"], self.pid)
        self.problem(403, lambda: work.deliver(self.c, self.other, self.intake_id, self.data))

    def test_public_snapshot_only_explicit_fields_and_stable_ids(self):
        w = self.published()
        public = work.public(self.c, w["slug"])
        self.assertEqual(len(public["roles"]), 4)
        self.assertNotIn("予算は未定", json.dumps(public, ensure_ascii=False))
        for k in ("draft", "email", "messages", "artifacts", "intake"):
            self.assertNotIn(k, public)
        d = w["draft"]
        ids = [t["id"] for t in d["tasks"]]
        d["public"]["title"] = "編集中のタイトル"
        self.edit(d)
        self.assertEqual(work.public(self.c, w["slug"])["title"], "公開用タイトル")
        self.assertEqual([t["id"] for t in self.get()["draft"]["tasks"]], ids)

    def test_requires_confirmed_role_conditions_and_owner_publish(self):
        w = self.get()
        self.problem(400, lambda: work.publish(self.c, self.uid, self.pid, {"version": w["version"], "consent": True}))
        self.problem(404, lambda: work.publish(self.c, self.other, self.pid, {"version": w["version"], "consent": True}))
        self.assertEqual(self.get()["published"], {})

    def test_free_apply_double_click_and_assignment_capacity(self):
        aid, tid = self.applied()
        body = {"task": tid, "experience": "会場探しを手伝えます", "consent": True, "publicationRevision": work.public(self.c, self.get()["slug"])["publicationRevision"]}
        self.assertEqual(work.apply(self.c, self.fan, self.pid, body)["id"], aid)
        aid2 = work.apply(self.c, self.other, self.pid, body)["id"]
        work.application_action(self.c, self.uid, aid, "status", {"version": 1, "status": "担当"})
        self.problem(409, lambda: work.application_action(self.c, self.uid, aid2, "status", {"version": 1, "status": "担当"}))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM applications WHERE task=? AND status='担当'", (tid,)).fetchone()[0], 1)
        self.assertFalse(core.paid(self.c, self.uid))

    def test_applied_tasks_survive_edit_and_offer_cannot_change(self):
        aid, tid = self.applied()
        d = self.get()["draft"]
        task = next(t for t in d["tasks"] if t["id"] == tid)
        task["instructions"] = "本人の準備メモだけを変更"
        self.edit(d)
        self.assertEqual(work.conversation(self.c, self.fan, aid)["id"], aid)
        d = self.get()["draft"]
        d["tasks"] = [t for t in d["tasks"] if t["id"] != tid]
        self.problem(409, lambda: self.edit(d))
        d = self.get()["draft"]
        next(t for t in d["tasks"] if t["id"] == tid)["offer"]["instructions"] = "変更された依頼条件"
        w = self.edit(d)
        self.problem(409, lambda: work.publish(self.c, self.uid, self.pid, {"version": w["version"], "consent": True}))

    def test_role_close_blocks_new_applications_keeps_conversations(self):
        aid, tid = self.applied()
        w = self.get()
        work.close_role(self.c, self.uid, self.pid, {"version": w["version"], "taskId": tid})
        self.problem(409, lambda: work.apply(self.c, self.other, self.pid, {"task": tid, "experience": "参加希望", "consent": True}))
        self.assertEqual(work.conversation(self.c, self.fan, aid)["id"], aid)

    def test_messages_are_private_deduplicated_and_unread_is_per_person(self):
        aid, tid = self.applied()
        body = {"body": "夕方に伺えます。", "requestId": s.ident()}
        mid = work.send_message(self.c, self.fan, aid, body)["id"]
        self.assertEqual(work.send_message(self.c, self.fan, aid, body)["id"], mid)
        self.assertEqual(work.unread(self.c, aid, self.uid), 1)
        self.assertEqual(work.unread(self.c, aid, self.fan), 0)
        self.problem(404, lambda: work.conversation(self.c, self.other, aid))
        self.problem(404, lambda: work.send_message(self.c, self.other, aid, {"body": "侵入", "requestId": s.ident()}))
        msg = work.conversation(self.c, self.uid, aid)["messages"][0]
        self.assertFalse(msg["mine"])
        self.assertNotIn("sender", msg)
        work.mark_read(self.c, self.uid, aid, {"cursor": msg["cursor"]})
        self.assertEqual(work.unread(self.c, aid, self.uid), 0)

    def test_cancel_prevents_sending_but_retains_messages(self):
        aid, _ = self.applied()
        work.send_message(self.c, self.fan, aid, {"body": "応募しました", "requestId": s.ident()})
        work.application_action(self.c, self.fan, aid, "cancel", {"version": 1})
        self.problem(409, lambda: work.send_message(self.c, self.uid, aid, {"body": "返信", "requestId": s.ident()}))
        self.assertEqual(len(work.conversation(self.c, self.uid, aid)["messages"]), 1)

    def test_ai_saves_artifacts_without_publishing_or_sending(self):
        request = {"requestId": s.ident(), "version": self.get()["version"], "prompt": "当日の進行表をつくって"}
        with patch.object(workspace_ai, "generate", return_value=(self.output(), 2)) as generate:
            work.assist(self.c, self.uid, self.pid, request)
            work.assist(self.c, self.uid, self.pid, request)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(self.get()["draft"]["artifacts"][0]["title"], "当日の進行表")
        self.assertEqual(self.get()["state"], "draft")
        self.assertEqual(self.get()["aiQuota"]["used"], 1)
        self.assertEqual(tuple(self.c.execute("SELECT spent_yen,reserved_yen FROM svc_budgets").fetchone()), (2, 0))
        self.assertEqual(self.mail.call_count, 1)

    def test_ai_concurrent_edit_charges_once_and_does_not_overwrite(self):
        def concurrent(context):
            d = self.get()["draft"]
            d["title"] = "本人が同時に変更"
            self.edit(d)
            return self.output(), 2
        with patch.object(workspace_ai, "generate", side_effect=concurrent):
            self.problem(409, lambda: work.assist(self.c, self.uid, self.pid, {"requestId": s.ident(), "version": self.get()["version"], "prompt": "進行表"}))
        self.assertEqual(self.get()["draft"]["title"], "本人が同時に変更")
        self.assertEqual(self.get()["draft"]["artifacts"], [])
        self.assertEqual(tuple(self.c.execute("SELECT spent_yen,reserved_yen FROM svc_budgets").fetchone()), (2, 0))

    def test_ai_unknown_is_not_retried_or_released(self):
        data = {"requestId": s.ident(), "version": self.get()["version"], "prompt": "修正して"}
        with patch.object(workspace_ai, "generate", side_effect=providers.ProviderError("不明", uncertain=True)) as gen:
            with self.assertRaises(providers.ProviderError):
                work.assist(self.c, self.uid, self.pid, data)
            self.problem(409, lambda: work.assist(self.c, self.uid, self.pid, data))
        self.assertEqual(gen.call_count, 1)
        self.assertEqual(self.c.execute("SELECT reserved_yen FROM svc_budgets").fetchone()[0], 10)

    def test_budget_and_quota_do_not_block_manual_edits(self):
        b = core.budget(self.c)
        self.c.execute("UPDATE svc_budgets SET spent_yen=2000 WHERE month=?", (b["month"],))
        self.c.commit()
        with patch.object(workspace_ai, "generate") as gen:
            self.problem(429, lambda: work.assist(self.c, self.uid, self.pid, {"requestId": s.ident(), "version": self.get()["version"], "prompt": "修正"}))
        gen.assert_not_called()
        d = self.get()["draft"]
        d["title"] = "手編集"
        self.edit(d)
        self.assertEqual(self.get()["draft"]["title"], "手編集")

    def test_history_restore_does_not_publish_and_preserves_original_intake(self):
        before = intake.detail(self.c, self.uid, self.intake_id)
        d = self.get()["draft"]
        d["overview"] = "本人が修正"
        w = self.edit(d)
        work.restore(self.c, self.uid, self.pid, {"version": w["version"], "restoreVersion": 1})
        self.assertEqual(self.get()["draft"]["overview"], initial()["overview"])
        self.assertEqual(len(work.history(self.c, self.uid, self.pid)["items"]), 3)
        self.assertEqual(intake.detail(self.c, self.uid, self.intake_id)["turns"], before["turns"])

    def test_wsgi_dispatch_and_csrf_for_free_flow(self):
        self.assertEqual(self.request("GET", "/api/service/projects/"+self.pid)["status"], 200)
        self.assertEqual(self.request("GET", "/api/service/projects/"+self.pid, uid=self.other)["status"], 404)
        self.assertEqual(self.request("POST", "/api/service/projects/"+self.pid+"/edit", {"version": 1, "draft": self.get()["draft"]}, csrf="wrong")["status"], 403)
        self.assertEqual(self.request("GET", "/api/service/admin/intakes/"+self.intake_id+"/workspace", uid=self.operator)["status"], 200)
        self.assertEqual(self.request("POST", "/api/service/billing/checkout", {})["status"], 409)
        self.assertEqual(self.request("GET", "/workspace.js")["status"], 200)

    def test_fifty_tasks_and_foreign_task_ids(self):
        d = self.get()["draft"]
        for i in range(36):
            t = copy.deepcopy(d["tasks"][0]);t["id"] = s.ident();d["tasks"].append(t)
        self.edit(d)
        self.assertEqual(len(self.get()["draft"]["tasks"]), 50)
        d["tasks"].append({**d["tasks"][0], "id": s.ident()})
        self.problem(400, lambda: self.edit(d))

    def test_management_import_not_available_to_web_or_sqlite_connections(self):
        self.problem(403, lambda: work.deliver_managed(self.c, self.intake_id, self.data, 'other@example.test'))

    def test_fans_only_see_frozen_public_titles(self):
        aid, tid = self.applied()
        d = self.get()["draft"]
        d["title"] = "非公開の内部タイトル"
        next(t for t in d["tasks"] if t["id"] == tid)["title"] = "非公開の作業メモ"
        self.edit(d)
        thread = work.conversation(self.c, self.fan, aid)
        self.assertEqual(thread["projectTitle"], "公開用タイトル")
        self.assertNotIn("非公開", json.dumps(thread, ensure_ascii=False))
        self.assertNotIn("非公開", json.dumps(work.dashboard(self.c, self.fan), ensure_ascii=False))

    def test_feature_flag_rollback_does_not_expose_internal_tasks_even_for_paid_owner(self):
        w = self.published()
        self.c.execute("INSERT INTO svc_subscriptions(id,user_id,customer_id,status,paid_until,first_paid,checked_at,period) VALUES(?,?,?,'ACTIVE',?,?,?,'test')", (s.ident(), self.uid, 'fixture', s.stamp()+99999, s.stamp(), s.stamp()))
        self.c.commit()
        with patch.dict(os.environ, {"NOTTA_WORKSPACE_ENABLED": "no"}):
            self.assertEqual(self.request('GET','/api/service/projects/'+self.pid)['status'], 503)
            self.assertEqual(self.request('GET','/api/service/public/'+w['slug'])['status'], 404)
        self.assertEqual(len(self.get()['draft']['tasks']), 14)

    def test_ai_thirtieth_call_limit_keeps_manual_editing_available(self):
        for _ in range(30):
            self.c.execute("INSERT INTO svc_ai_requests(id,user_id,project,fingerprint,mode,period,status,reserved_yen,search_used,created) VALUES(?,?,?,'fixture','workspace','lifetime','completed',0,0,?)", (s.ident(),self.uid,self.pid,s.stamp()))
        self.c.commit()
        with patch.object(workspace_ai, 'generate') as gen:
            self.problem(429, lambda: work.assist(self.c,self.uid,self.pid,{'requestId':s.ident(),'version':self.get()['version'],'prompt':'変更'}))
        gen.assert_not_called()
        d=self.get()['draft'];d['title']='上限後も手編集';self.edit(d)
        self.assertEqual(self.get()['draft']['title'],'上限後も手編集')

    def test_invalid_ai_edit_cannot_remove_applied_task(self):
        aid,tid=self.applied()
        output=self.output();output['removeTaskIds']=[tid]
        with patch.object(workspace_ai,'generate',return_value=(output,2)):
            self.problem(409, lambda:work.assist(self.c,self.uid,self.pid,{'requestId':s.ident(),'version':self.get()['version'],'prompt':'タスク整理'}))
        self.assertEqual(work.conversation(self.c,self.fan,aid)['id'],aid)
        self.assertIn(tid,[t['id'] for t in self.get()['draft']['tasks']])

    def test_application_requires_the_public_conditions_the_fan_read(self):
        w = self.published()
        public = work.public(self.c, w['slug'])
        body = {'task': public['roles'][0]['id'], 'experience': '参加します', 'consent': True,
                'publicationRevision': public['publicationRevision']}
        d = w['draft'];d['public']['conditions'] = '変更後の参加条件'
        w = self.edit(d)
        work.publish(self.c, self.uid, self.pid, {'version': w['version'], 'consent': True})
        self.problem(409, lambda: work.apply(self.c, self.fan, self.pid, body))
        self.assertEqual(self.c.execute('SELECT COUNT(*) FROM applications').fetchone()[0], 0)
        body['publicationRevision'] = work.public(self.c, w['slug'])['publicationRevision']
        first = work.apply(self.c, self.fan, self.pid, body)
        body['publicationRevision'] = public['publicationRevision']
        self.assertEqual(work.apply(self.c, self.fan, self.pid, body), first)

    def test_global_public_conditions_are_frozen_after_an_application(self):
        aid, tid = self.applied()
        w = self.get();before = work.public(self.c, w['slug'])
        d = w['draft'];d['public']['conditions'] = '応募後に変更した参加費'
        w = self.edit(d)
        self.problem(409, lambda: work.publish(self.c, self.uid, self.pid, {'version': w['version'], 'consent': True}))
        self.assertEqual(work.public(self.c, w['slug']), before)
        self.assertEqual(work.conversation(self.c, self.fan, aid)['id'], aid)


if __name__ == '__main__':
    unittest.main(verbosity=2)
