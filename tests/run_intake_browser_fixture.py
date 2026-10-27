"""Local-only fixture; no real AI, email or payment request can leave this process."""
import json
import os
import secrets
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as s
import service_intake as intake
import service_providers as providers

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "intake-browser"
DATA.mkdir(parents=True, exist_ok=True)
os.environ.update(NOTTA_SERVICE_ENABLED="yes", NOTTA_INTAKE_ENABLED="yes",
                  NOTTA_AUTH_SECRET=secrets.token_urlsafe(40),
                  NOTTA_PUBLIC_ORIGIN="http://127.0.0.1:8880")
providers.mail_ready = lambda: True
providers.ai_ready = lambda: True
providers.search_ready = lambda: False


def send_code(email, code, challenge):
    (DATA / "inbox.json").write_text(json.dumps({"email": email, "code": code}), encoding="utf-8")


def interview(goal, history, theme, number):
    return {"question": "「" + goal[:40] + "」について、" + theme + "を教えてください。", "cost_yen": 1}


def notify(payload, key):
    with (DATA / "notifications.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps({"payload": payload, "key": key}, ensure_ascii=False) + "\n")
    return {"id": "local-" + s.ident()}


providers.send_code = send_code
providers.interview_question = interview
providers.send_intake_notification = notify
s.init_db(DATA / "browser.sqlite3")
httpd = ThreadingHTTPServer(("127.0.0.1", 8880), s.Handler)
print("Intake browser fixture on 127.0.0.1:8880. All providers are local stubs.", flush=True)
httpd.serve_forever()
