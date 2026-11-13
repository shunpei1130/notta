"""Loopback-only, fictional accounts. No real email/AI/payment can leave."""
import json
import os
import secrets
import sys
import time
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
if os.getenv('NOTTA_BROWSER_PLANNING') == 'yes':
    os.environ['NOTTA_PLANNING_ENABLED'] = 'yes'
if os.getenv('NOTTA_BROWSER_SIMPLE') == 'yes':
    os.environ['NOTTA_SIMPLE_UI_ENABLED'] = 'yes'
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
    if work.planning.enabled():
        stages = [{'id': s.ident(), 'title': name, 'outcome': outcome} for name, outcome in (
            ('開催の条件を整える', '会場と費用・役割が決まり、具体的な準備に進める'),
            ('写真・告知・展示を準備する', '参加者と展示内容が揃い、当日を迎えられる'),
            ('開催し、片付けとお礼を終える', '写真展と交流を実施し、会場を返却してお礼まで終える'))]
        result['roadmap'] = {'goal': 'ファンと写真展を開き、片付け・お礼まで終える', 'criteria': '写真を展示して交流し、会場返却と参加者へのお礼が完了している', 'stages': stages}
        result['public'] = {'title': 'ファンとつくる写真展', 'overview': '写真を選んで展示する、小さな共創企画です。', 'schedule': '日程と会場は未定。会場探しから一緒に進めます。', 'conditions': '任意の協力。参加費・実費・権利は事前に相談して決めます。', 'recruitment': '写真や展示が好きな方と一緒につくりたいです。'}
        for i, task in enumerate(result['tasks']):
            task.update(id=s.ident(), stageId=stages[0 if i in (0,1,2,4) else 2 if i>=11 else 1]['id'], dependsOn=[], useCommonTerms=True, recruit=i in (1,5,7,8), recruitCandidate=i in (1,5,7,8), offer={**task, 'terms': ''})
        dependencies = [[],[0],[1],[0],[0],[0,1,4],[5],[2],[1,2],[6,7,8],[3,6,7,8],[9,10],[11],[12]]
        for task, before in zip(result['tasks'], dependencies): task['dependsOn'] = [result['tasks'][index]['id'] for index in before]
    if os.getenv('NOTTA_BROWSER_LARGE') == 'yes' and work.planning.enabled():
        for name in ('当日を迎える', '開催する', '片付けとお礼'):
            result['roadmap']['stages'].append({'id': s.ident(), 'title': name, 'outcome': name+'の作業を終え、次の準備に進める'})
        while len(result['tasks']) < 50:
            index = len(result['tasks'])
            result['tasks'].append({'id': s.ident(), 'title': '長い名前でも内容が読めることを確認する架空の準備タスク '+str(index+1),
                                    'instructions': '【検証用】作業内容を確認し、必要な準備を進める。', 'role': '準備担当', 'estimate': '30分', 'completion': '準備内容を記録して本人が確認する',
                                    'stageId': result['roadmap']['stages'][index % 6]['id'], 'dependsOn': [], 'recruitCandidate': False, 'recruit': False, 'useCommonTerms': True})
    pid = work.deliver(c, operator, item["id"], {"result": result, "draftVersion": 0, "recruitTaskIndexes": [1, 5, 7, 8]})["projectId"]
    if os.getenv('NOTTA_BROWSER_LARGE') == 'yes':
        value = work.detail(c, owner, pid)
        value['draft']['tasks'][-1]['stageId'] = ''
        work.edit(c, owner, pid, {'version': value['version'], 'draft': value['draft']})
    if os.getenv('NOTTA_BROWSER_COMPLETE') == 'yes':
        value = work.detail(c, owner, pid)
        for task in value['draft']['tasks']:
            task['status'] = '完了'
        work.edit(c, owner, pid, {'version': value['version'], 'draft': value['draft']})
    (DATA / "fixture.json").write_text(json.dumps({"project": pid, "intake": item["id"]}), encoding="utf-8")
if os.getenv('NOTTA_BROWSER_SLOW_EDIT') == 'yes':
    original_edit = work.edit
    def slow_edit(*args, **kwargs):
        time.sleep(0.8)
        return original_edit(*args, **kwargs)
    work.edit = slow_edit
httpd = ThreadingHTTPServer(("127.0.0.1", 8892), s.Handler)
print("Workspace fixture ready at http://127.0.0.1:8892; all accounts fictional; all providers local.", flush=True)
httpd.serve_forever()
