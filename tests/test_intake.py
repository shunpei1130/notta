import copy
import io
import json
import os
import unittest
from unittest.mock import patch

import test_service as fixtures
import server as s
import service_core as core
import service_intake as intake
import service_providers as providers
import wsgi


def result():
    return {"title": "ファンと小さな写真展", "overview": "写真10点を一緒に選び、小さな展示をつくる。",
            "firstDeliverable": "展示テーマと写真10点の一覧",
            "tasks": [{"title": "写真を選ぶ", "instructions": "候補30点から10点選んで理由を添える。",
                       "role": "写真が好きなファン", "estimate": "30分", "completion": "10点の一覧と選定理由"}]}


class IntakeTest(unittest.TestCase):
    user = fixtures.ServiceTest.user
    problem = fixtures.ServiceTest.problem

    def setUp(self):
        fixtures.ServiceTest.setUp(self)
        self.operator = self.user()
        self.c.execute("UPDATE users SET email=? WHERE id=?", (intake.OPERATOR, self.operator))
        self.c.commit()
        self.mode = patch.dict(os.environ, {"NOTTA_INTAKE_ENABLED": "yes"})
        self.mode.start()
        self.ai_ready = patch.object(providers, "interview_ready", return_value=True)
        self.ai_ready.start()
        self.generate = patch.object(providers, "interview_question",
                                     side_effect=lambda goal, history, theme, number: {"question": str(number) + ". " + theme + "について教えてください。", "cost_yen": 2}).start()
        self.send = patch.object(providers, "send_intake_notification", return_value={"id": "test-mail"}).start()

    def tearDown(self):
        patch.stopall()
        fixtures.ServiceTest.tearDown(self)

    def start(self, uid=None):
        return intake.start(self.c, uid or self.uid, {"goal": "ファンと写真展をつくりたい"})

    def response(self, item, value="未定"):
        turn = next(t for t in item["turns"] if t["number"] == item["step"])
        return {"step": item["step"], "answer": value, "version": item["version"],
                "questionHash": turn["questionHash"], "requestId": s.ident()}

    def finish(self, item=None):
        item = item or self.start()
        while item["state"] == "interview":
            item = intake.answer(self.c, self.uid, item["id"], self.response(item, "" if item["step"] == 10 else "おまかせ"))
        return item

    def request(self, method, path, data=None, uid=None, csrf="fixture", query=""):
        application = wsgi.create_application(s.DB, initialize=False)
        body = json.dumps(data or {}).encode()
        user = self.c.execute("SELECT * FROM users WHERE id=?", (uid or self.uid,)).fetchone()
        session = dict(user); session["csrf"] = "fixture"
        response = {}
        environment = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
                       "SERVER_PORT": "8765", "HTTP_HOST": "localhost:8765",
                       "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)),
                       "HTTP_X_CSRF_TOKEN": csrf, "wsgi.input": io.BytesIO(body)}
        with patch.object(s.Handler, "session", return_value=session):
            payload = application(environment, lambda status, headers: response.update(status=int(status.split()[0]), headers=dict(headers)))
            response["body"] = b"".join(payload)
        return response

    def test_ordered_ten_questions_one_intake_max_nine_ai_and_private_receipt(self):
        item = self.finish()
        self.assertEqual(item["state"], "submitted")
        self.assertEqual([t["number"] for t in item["turns"]], list(range(1, 11)))
        self.assertEqual(item["turns"][-1]["answer"], "")
        self.assertEqual(self.generate.call_count, 9)
        for index, call in enumerate(self.generate.call_args_list):
            self.assertEqual(call.args[2:], (intake.TOPICS[index][0], index + 1))
            self.assertEqual(len(call.args[1]), index)
        self.assertEqual(self.start()["id"], item["id"])
        self.assertEqual(self.generate.call_count, 9)
        mail, key = self.send.call_args.args
        self.assertEqual(mail["to"], [intake.OPERATOR])
        self.assertEqual(mail["reply_to"], item["email"])
        self.assertIn(item["goal"], mail["text"])
        self.assertIn("/admin/intakes/" + item["id"], mail["text"])
        self.assertIn("10.", mail["text"])
        self.assertNotIn("draft", item)
        self.assertEqual(item["message"], intake.WAITING)
        self.assertEqual(self.send.call_count, 1)
        self.assertIn("-receipt-1", key)
        self.assertEqual(tuple(self.c.execute("SELECT spent_yen,reserved_yen FROM svc_budgets").fetchone()), (18, 0))

    def test_reload_and_answer_replay_do_not_regenerate_or_resend(self):
        item = self.start()
        data = self.response(item)
        updated = intake.answer(self.c, self.uid, item["id"], data)
        self.assertEqual(self.generate.call_count, 2)
        for _ in range(3):
            self.assertEqual(intake.detail(self.c, self.uid, item["id"])["step"], 2)
            self.assertEqual(intake.answer(self.c, self.uid, item["id"], data)["step"], 2)
        self.assertEqual(self.generate.call_count, 2)
        self.problem(409, lambda: intake.answer(self.c, self.uid, item["id"], {**data, "answer": "違う回答"}))
        self.problem(409, lambda: intake.answer(self.c, self.uid, item["id"], {**self.response(updated), "requestId": data["requestId"]}))
        while updated["step"] < 10:
            updated = intake.answer(self.c, self.uid, item["id"], self.response(updated))
        final_data = self.response(updated, "")
        intake.answer(self.c, self.uid, item["id"], final_data)
        intake.answer(self.c, self.uid, item["id"], final_data)
        self.assertEqual(self.send.call_count, 1)

    def test_ai_failure_and_budget_allow_all_answers_without_paid_reserve(self):
        self.generate.side_effect = providers.ProviderError("definite failure")
        item = self.start()
        self.assertEqual(item["turns"][0]["question"], intake.TOPICS[0][1])
        self.assertEqual(self.c.execute("SELECT reserved_yen FROM svc_budgets").fetchone()[0], 0)
        self.c.execute("UPDATE svc_budgets SET spent_yen=1990")
        self.c.commit()
        self.generate.side_effect = providers.ProviderError("result unknown", uncertain=True)
        updated = intake.answer(self.c, self.uid, item["id"], self.response(item))
        self.assertEqual(updated["step"], 2)
        self.assertEqual(self.c.execute("SELECT reserved_yen FROM svc_budgets").fetchone()[0], 10)
        self.finish(updated)
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM svc_intake_turns").fetchone()[0], 10)
        self.assertEqual(self.c.execute("SELECT reserved_yen FROM svc_budgets").fetchone()[0], 10)

    def test_missing_ai_still_accepts_and_does_not_retroactively_generate(self):
        with patch.object(providers, "interview_ready", return_value=False):
            item = self.start()
        self.assertEqual(item["turns"][0]["question"], intake.TOPICS[0][1])
        for _ in range(3):
            intake.detail(self.c, self.uid, item["id"])
            self.start()
        self.assertEqual(self.generate.call_count, 0)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM svc_intake_ai_requests").fetchone()[0], 0)

    def test_concurrent_fallback_answer_is_not_overwritten_by_late_ai(self):
        def concurrent(goal, history, theme, number):
            self.assertFalse(self.c.in_transaction)
            with s.connect() as other:
                item = intake.summary(other, self.uid)
                visible = intake.detail(other, self.uid, item["id"])
                self.assertEqual(visible["turns"][0]["question"], intake.TOPICS[0][1])
                with patch.object(providers, "interview_ready", return_value=False):
                    intake.answer(other, self.uid, item["id"], self.response(visible))
            return {"question": "遅れて戻った質問", "cost_yen": 1}
        self.generate.side_effect = concurrent
        item = self.start()
        self.assertEqual(item["step"], 2)
        self.assertEqual(item["turns"][0]["question"], intake.TOPICS[0][1])
        self.assertEqual(item["turns"][0]["answer"], "未定")
        self.assertEqual(self.generate.call_count, 1)

    def test_question_hash_stale_version_and_required_values_are_enforced(self):
        item = self.start()
        self.problem(409, lambda: intake.answer(self.c, self.uid, item["id"], {**self.response(item), "questionHash": "stale"}))
        self.problem(409, lambda: intake.answer(self.c, self.uid, item["id"], {**self.response(item), "version": 0}))
        self.problem(400, lambda: intake.answer(self.c, self.uid, item["id"], self.response(item, "")))
        self.problem(400, lambda: intake.answer(self.c, self.uid, item["id"], self.response(item, "a" * 2001)))
        self.assertEqual(self.generate.call_count, 1)
        self.assertIsNone(intake.detail(self.c, self.uid, item["id"])["turns"][0]["answer"])

    def test_only_owner_and_exact_verified_operator_can_see_or_update(self):
        item = self.finish()
        self.problem(404, lambda: intake.detail(self.c, self.other, item["id"]))
        self.problem(404, lambda: intake.answer(self.c, self.other, item["id"], self.response(item)))
        for uid in (self.uid, self.other):
            self.problem(403, lambda: intake.admin_list(self.c, uid))
            self.problem(403, lambda: intake.detail(self.c, uid, item["id"], True))
            self.problem(403, lambda: intake.save_result(self.c, uid, item["id"], {"result": result(), "draftVersion": 0}))
        self.assertEqual(len(intake.admin_list(self.c, self.operator)["items"]), 1)
        self.assertIn("draft", intake.detail(self.c, self.operator, item["id"], True))
        self.c.execute("UPDATE users SET name='運営',role='creator' WHERE id=?", (self.other,))
        self.c.commit()
        self.problem(403, lambda: intake.admin_list(self.c, self.other))
        self.c.execute("UPDATE svc_accounts SET verified_at=0 WHERE user_id=?", (self.operator,))
        self.c.commit()
        self.problem(403, lambda: intake.admin_list(self.c, self.operator))

    def test_draft_publish_revision_and_mail_are_separate_and_idempotent(self):
        item = self.finish()
        draft = intake.save_result(self.c, self.operator, item["id"], {"result": result(), "draftVersion": 0})
        self.assertEqual(draft["state"], "working")
        self.assertIsNone(intake.detail(self.c, self.uid, item["id"])["result"])
        self.assertEqual(self.send.call_count, 1)
        ready = intake.complete(self.c, self.operator, item["id"], {"draftVersion": 1})
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(ready["result"]["tasks"], result()["tasks"])
        mail, key = self.send.call_args.args
        self.assertEqual(mail["to"], [item["email"]])
        self.assertEqual(mail["reply_to"], intake.OPERATOR)
        self.assertIn("/intakes/" + item["id"], mail["text"])
        self.assertNotIn("token=", mail["text"])
        self.assertNotIn("写真10点", mail["text"])  # private result stays behind login
        intake.complete(self.c, self.operator, item["id"], {"draftVersion": 1})
        self.assertEqual(self.send.call_count, 2)
        changed = result(); changed["tasks"][0]["title"] = "新しいタスク名"
        draft = intake.save_result(self.c, self.operator, item["id"], {"result": changed, "draftVersion": 1})
        self.assertEqual(draft["draftVersion"], 2)
        self.assertEqual(intake.detail(self.c, self.uid, item["id"])["result"]["tasks"][0]["title"], "写真を選ぶ")
        ready = intake.complete(self.c, self.operator, item["id"], {"draftVersion": 2})
        self.assertEqual(ready["resultVersion"], 2)
        self.assertEqual(self.send.call_count, 3)
        self.assertNotEqual(key, self.send.call_args.args[1])
        intake.save_result(self.c, self.operator, item["id"], {"result": changed, "draftVersion": 2})
        intake.complete(self.c, self.operator, item["id"], {"draftVersion": 2})
        self.assertEqual(self.send.call_count, 3)

    def test_incomplete_drafts_allowed_publish_requires_each_task_field_and_version(self):
        item = self.finish()
        intake.save_result(self.c, self.operator, item["id"], {"result": {"title": "途中"}, "draftVersion": 0})
        self.problem(400, lambda: intake.complete(self.c, self.operator, item["id"], {"draftVersion": 1}))
        self.problem(409, lambda: intake.save_result(self.c, self.operator, item["id"], {"result": result(), "draftVersion": 0}))
        invalid = result(); invalid["tasks"][0]["completion"] = ""
        intake.save_result(self.c, self.operator, item["id"], {"result": invalid, "draftVersion": 1})
        self.problem(400, lambda: intake.complete(self.c, self.operator, item["id"], {"draftVersion": 2}))
        self.assertEqual(self.send.call_count, 1)

    def test_notification_failure_preserves_receipt_and_frozen_payload_and_retry_window(self):
        self.send.side_effect = providers.ProviderError("timeout", uncertain=True)
        item = self.finish()
        self.assertEqual(item["state"], "submitted")
        mail = intake.notification_view(self.c, item["id"])[0]
        self.assertEqual(mail["status"], "unknown")
        original = self.send.call_args.args
        self.c.execute("UPDATE users SET name='名前の変更' WHERE id=?", (self.uid,))
        self.c.commit()
        self.send.side_effect = None
        intake.retry_notification(self.c, self.operator, item["id"], mail["id"])
        self.assertEqual(self.send.call_args.args, original)
        self.assertEqual(intake.notification_view(self.c, item["id"])[0]["status"], "sent")
        intake.retry_notification(self.c, self.operator, item["id"], mail["id"])
        self.assertEqual(self.send.call_count, 2)
        self.c.execute("UPDATE svc_intake_notifications SET status='unknown'")
        self.c.commit()
        self.clock.return_value += 24 * 3600
        self.problem(409, lambda: intake.retry_notification(self.c, self.operator, item["id"], mail["id"]))
        self.assertEqual(self.send.call_count, 2)

    def test_completion_mail_failure_does_not_hide_result(self):
        item = self.finish()
        intake.save_result(self.c, self.operator, item["id"], {"result": result(), "draftVersion": 0})
        self.send.side_effect = providers.ProviderError("rejected")
        intake.complete(self.c, self.operator, item["id"], {"draftVersion": 1})
        visible = intake.detail(self.c, self.uid, item["id"])
        self.assertEqual(visible["state"], "ready")
        self.assertEqual(visible["result"]["title"], result()["title"])
        self.assertEqual(intake.notification_view(self.c, item["id"])[-1]["status"], "failed")

    def test_http_csrf_operator_privacy_static_routes_and_old_ai_checkout_closed(self):
        item = self.start()
        paths = ["/api/service/intakes/" + item["id"], "/api/service/admin/intakes/" + item["id"], "/api/service/admin/intakes"]
        self.assertEqual(self.request("GET", paths[0], uid=self.other)["status"], 404)
        self.assertEqual(self.request("GET", paths[1])["status"], 403)
        self.assertEqual(self.request("GET", paths[2])["status"], 403)
        self.assertEqual(self.request("GET", paths[2], uid=self.operator)["status"], 200)
        self.assertEqual(self.request("POST", "/api/service/intakes", {"goal": "企画"}, csrf="bad")["status"], 403)
        for path in ("/api/service/projects", "/api/service/billing/checkout", "/api/service/projects/" + s.ident() + "/assist"):
            with self.subTest(path=path):
                self.assertEqual(self.request("POST", path)["status"], 409)
        for path in ("/intake.js", "/wait.js", "/wait.css", "/intakes/" + item["id"], "/admin/intakes/" + item["id"]):
            self.assertEqual(self.request("GET", path)["status"], 200)
        me = json.loads(self.request("GET", "/api/service/me")["body"])
        self.assertEqual(me["intake"]["id"], item["id"])
        self.assertFalse(me["operator"])

    def test_otp_login_then_goal_and_resume_same_account(self):
        with patch.object(providers, "mail_ready", return_value=True), patch.object(providers, "send_code") as send:
            challenge = core.auth_start(self.c, {"email": "creator@example.test", "name": "企画者", "consent": True}, "local")
        session, _ = core.auth_verify(self.c, {"challenge": challenge["challenge"], "code": send.call_args.args[1]})
        uid = session["user"]["id"]
        item = intake.start(self.c, uid, {"goal": "ファンとライブをしたい"})
        self.assertEqual(item["goal"], "ファンとライブをしたい")
        item = intake.answer(self.c, uid, item["id"], self.response(item, "一緒に楽しみたい"))
        self.clock.return_value += 8 * 3600 + 1
        self.assertEqual(intake.summary(self.c, uid)["id"], item["id"])
        self.assertEqual(intake.detail(self.c, uid, item["id"])["step"], 2)


class InterviewTransportTest(unittest.TestCase):
    def test_only_one_bounded_question_no_search_decomposition_or_contacts(self):
        response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"question":"何を届けたいですか？"}'}]}}],
                    "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 40, "thoughtsTokenCount": 0}}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-only", "NOTTA_AI_ENABLED": "yes"}), patch.object(
                providers, "request_json", side_effect=[{"totalTokens": 1000}, response]) as request:
            value = providers.interview_question("小さなライブ", [{"number": 1, "question": "目的", "answer": "楽しみたい"}], "完成形", 2)
        self.assertEqual(value["question"], "何を届けたいですか？")
        self.assertEqual(request.call_count, 2)
        generate = request.call_args.kwargs["data"]
        self.assertNotIn("tools", generate)
        self.assertEqual(generate["generationConfig"]["maxOutputTokens"], 800)
        self.assertEqual(generate["generationConfig"]["thinkingConfig"], {"thinkingLevel": "minimal"})
        self.assertEqual(generate["generationConfig"]["responseJsonSchema"]["required"], ["question"])
        self.assertNotIn("email", json.dumps(generate))


if __name__ == "__main__":
    unittest.main()
