"""Loopback-only, fictional accounts. No real email/AI/payment can leave."""
import json
import os
import secrets
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as s
import service_intake as intake
import service_workspace as work
import service_providers as providers
import workspace_ai

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/workspace-browser"
DATA.mkdir(parents=True, exist_ok=True)
os.environ.update(NOTTA_SERVICE_ENABLED="yes", NOTTA_INTAKE_ENABLED="yes", NOTTA_WORKSPACE_ENABLED="yes",
                  NOTTA_AUTH_SECRET=secrets.token_urlsafe(40), NOTTA_PUBLIC_ORIGIN="http://127.0.0.1:8892",
                  NOTTA_AI_ENABLED="yes", OPENAI_API_KEY="fixture-not-a-key")
providers.mail_ready = lambda: True
providers.interview_ready = lambda: False
providers.search_ready = lambda: False
providers.send_code = lambda email, code, challenge: (DATA / "inbox.json").write_text(json.dumps({"email": email, "code": code}), encoding="utf-8")
providers.send_intake_notification = lambda payload, key: {"id": "fixture-only"}


def generate(context):
    return {"reply": "進行表を資料に保存しました。", "changes": [], "newTasks": [], "removeTaskIds": [],
            "artifacts": [{"id": "", "title": "当日の進行表", "body": "17:30 受付\n18:00 歓談\n20:30 終了", "taskId": ""}]}, 1


workspace_ai.generate = generate
s.init_db(DATA / ("fixture-" + s.ident() + ".sqlite3"))
with s.connect() as c:
    def user(email, name):
        uid = s.ident()
        c.execute("INSERT INTO users VALUES(?,?,?,'creator',?,'disabled-password')", (uid, email, name, secrets.token_hex(16)))
        c.execute("INSERT INTO svc_accounts(user_id,verified_at,created) VALUES(?,?,?)", (uid, s.stamp(), s.stamp()))
        c.commit()
        return uid
    operator = user(intake.OPERATOR, "架空の運営")
    owner = user("owner@workspace.example.test", "架空の企画者")
    user("fan@workspace.example.test", "架空のファン")
    item = intake.start(c, owner, {"goal": "【検証用の架空企画】ファンと小さな写真展をつくる"})
    while item["state"] == "interview":
        turn = next(t for t in item["turns"] if t["number"] == item["step"])
        item = intake.answer(c, owner, item["id"], {"step": item["step"], "version": item["version"], "answer": "未定", "questionHash": turn["questionHash"], "requestId": s.ident()})
    result = {"title": "ファンとつくる、小さな写真展", "overview": "【検証用の架空企画】写真を選び、展示と交流を楽しむ。", "firstDeliverable": "写真10点と展示計画",
              "tasks": [{"title": name, "instructions": "候補をまとめて企画者に提案する。", "role": "写真が好きな協力者", "estimate": "30分", "completion": "候補一覧と提案メモ"}
                        for name in ("テーマを決める", "会場を探す", "費用を整理する", "写真を選ぶ", "役割を相談する", "告知をつくる", "申込を整理する", "交流企画を考える", "会場を飾る", "進行表をつくる", "備品を揃える", "設営する", "当日を進める", "片付けとお礼") ]}
    pid = work.deliver(c, operator, item["id"], {"result": result, "draftVersion": 0, "recruitTaskIndexes": [1, 5, 7, 8]})["projectId"]
    (DATA / "fixture.json").write_text(json.dumps({"project": pid, "intake": item["id"]}), encoding="utf-8")
httpd = ThreadingHTTPServer(("127.0.0.1", 8892), s.Handler)
print("Workspace fixture ready at http://127.0.0.1:8892; all accounts fictional; all providers local.", flush=True)
httpd.serve_forever()
