"""Explicit validation-branch smoke. Fictional content and provider stubs only."""
import argparse
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as s
import service_core as core
import service_intake as intake
import service_providers as providers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-host", required=True)
    args = parser.parse_args()
    values = dict(line.split("=", 1) for line in (Path(__file__).resolve().parents[1] / ".env.validation").read_text(encoding="utf-8").splitlines() if "=" in line)
    dsn = values["NOTTA_VALIDATION_RUNTIME_URL"]
    if urlparse(dsn).hostname != args.expected_host:
        raise SystemExit("Validation target mismatch")
    os.environ.update(NOTTA_INTAKE_ENABLED="yes", NOTTA_PUBLIC_ORIGIN="https://notta.example")
    s.init_db(dsn, backend="postgres", initialize=False)
    with s.connect() as c:
        before = tuple(c.execute("SELECT (SELECT count(*) FROM projects) AS projects,(SELECT count(*) FROM applications) AS applications").fetchone().values())
        uid = s.ident()
        c.execute("INSERT INTO users(id,email,name,role,salt,hash) VALUES(?,?,?,?,?,?)",
                  (uid, "intake-validation-" + uid + "@example.test", "検証用の架空利用者", "creator", secrets.token_hex(16), "disabled"))
        c.execute("INSERT INTO svc_accounts(user_id,verified_at,created) VALUES(?,?,?)", (uid, s.stamp(), s.stamp()))
        operator = c.execute("SELECT id FROM users WHERE email=?", (intake.OPERATOR,)).fetchone()
        if not operator:
            operator_id = s.ident()
            c.execute("INSERT INTO users(id,email,name,role,salt,hash) VALUES(?,?,?,?,?,?)",
                      (operator_id, intake.OPERATOR, "検証用の運営", "creator", secrets.token_hex(16), "disabled"))
        else:
            operator_id = operator["id"]
        c.execute("INSERT OR IGNORE INTO svc_accounts(user_id,verified_at,created) VALUES(?,?,?)", (operator_id, s.stamp(), s.stamp()))
        c.commit()
        with patch.object(providers, "ai_ready", return_value=True), patch.object(
                providers, "interview_question", side_effect=lambda goal, history, theme, number: {"question": theme + "を教えてください。", "cost_yen": 1}) as ai, patch.object(
                providers, "send_intake_notification", return_value={"id": "validation-no-email"}) as send:
            item = intake.start(c, uid, {"goal": "検証用：ファンと地域紹介の小冊子4ページをつくる"})
            while item["state"] == "interview":
                turn = next(t for t in item["turns"] if t["number"] == item["step"])
                item = intake.answer(c, uid, item["id"], {"step": item["step"], "answer": "" if item["step"] == 10 else "未定",
                    "version": item["version"], "questionHash": turn["questionHash"], "requestId": s.ident()})
            assert item["state"] == "submitted" and ai.call_count == 9 and send.call_count == 1
            for action, code in ((lambda: intake.admin_list(c, uid), 403),
                                 (lambda: intake.detail(c, operator_id, item["id"]), 404)):
                try:
                    action()
                except s.Problem as error:
                    assert error.status == code
                    c.rollback()
                else:
                    raise AssertionError("Private access not enforced")
            draft = {"title": "検証用の小冊子", "overview": "ファンと地域を紹介する4ページをつくる",
                     "firstDeliverable": "4ページの構成", "tasks": [{"title": "題材を選ぶ", "instructions": "候補を3つ出して理由を書く",
                         "role": "地域が好きなファン", "estimate": "20分", "completion": "候補3件の一覧"}]}
            intake.save_result(c, operator_id, item["id"], {"draftVersion": 0, "result": draft})
            assert intake.detail(c, uid, item["id"])["result"] is None and send.call_count == 1
            completed = intake.complete(c, operator_id, item["id"], {"draftVersion": 1})
            intake.complete(c, operator_id, item["id"], {"draftVersion": 1})
            assert completed["resultVersion"] == 1 and send.call_count == 2
            assert intake.detail(c, uid, item["id"])["result"] == draft
            assert any(r["id"] == item["id"] for r in intake.admin_list(c, operator_id)["items"])
            assert all(n["status"] == "sent" for n in intake.notification_view(c, item["id"]))
            after = tuple(c.execute("SELECT (SELECT count(*) FROM projects) AS projects,(SELECT count(*) FROM applications) AS applications").fetchone().values())
            assert before == after
            print("PASS: PostgreSQL runtime grants; ten answers/nine questions; owner/operator privacy; draft/publish; two idempotent notification records; legacy projects/applications preserved. No real email, AI or payment.")


if __name__ == "__main__":
    main()
