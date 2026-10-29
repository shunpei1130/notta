"""Private ten-question intake and human-authored results.

Only the question transport can use AI. Every answer, fallback question, budget
reservation and frozen notification is committed before external I/O. GETs never
generate, send, retry or publish anything.
"""
import hashlib
import json
import os
import re

import server as s
import service_core as core
import service_providers as providers

OPERATOR = "shumpei.hasegawa@maora.co.jp"
MONTH_LIMIT = 2000
QUESTION_RESERVE = 10
WAITING = "タスクを分解しています。作業が終わるまで数時間かかるのでメールでの通知をお待ちください"
TOPICS = [
    ("目的", "この企画をやってみたいと思った理由や、実現したいことを教えてください。"),
    ("完成形", "どんなもの・体験を、どのくらいの規模でつくりたいですか？ 完成した姿を教えてください。"),
    ("届けたい相手", "この企画を誰に届けたいですか？ 楽しんでほしい人を教えてください。"),
    ("日程", "いつごろ実現したいですか？ 決まっている日や締切があれば教えてください。"),
    ("予算", "制作や開催に使える予算は、どのくらいを考えていますか？"),
    ("自分ができること", "ご自身の得意なことと、この企画に使えそうな時間を教えてください。"),
    ("使えるもの・仲間", "すでにある素材・道具・場所や、一緒に取り組めそうな人はいますか？"),
    ("ファンとのつくり方", "ファンにはどんなことを手伝ってほしいですか？ 一緒につくる体験やクレジットなど、参加した人に届けたいものも教えてください。"),
    ("大切な条件", "大切にしたい条件や避けたいことはありますか？ 公開できる範囲、権利、参加条件なども教えてください。"),
    ("その他の要望", "ほかに伝えておきたいことや、要望があれば自由に入力してください。（任意）"),
]


def enabled():
    return os.getenv("NOTTA_INTAKE_ENABLED") == "yes"


def is_operator(c, uid):
    return bool(c.execute(
        "SELECT 1 FROM users u JOIN svc_accounts a ON a.user_id=u.id WHERE u.id=? AND u.email=? AND a.verified_at>0",
        (uid, OPERATOR)).fetchone())


def require_operator(c, uid):
    s.require(is_operator(c, uid), 403, "運営用のアカウントでログインしてください。")


def question_hash(question):
    return hashlib.sha256(question.encode()).hexdigest()


def row(c, intake_id, uid, operator=False):
    found = c.execute("SELECT * FROM svc_intakes WHERE id=?", (intake_id,)).fetchone()
    s.require(found and (found["user_id"] == uid or (operator and is_operator(c, uid))), 404, "受付が見つかりません。")
    return found


def notification_view(c, intake_id):
    return [dict(r) for r in c.execute(
        "SELECT id,kind,revision,status,created,updated,attempted_at,error,provider_id FROM svc_intake_notifications WHERE intake_id=? ORDER BY created,revision",
        (intake_id,))]


def detail(c, uid, intake_id, operator=False):
    if operator:
        require_operator(c, uid)
    item = row(c, intake_id, uid, operator)
    turns = [dict(r) for r in c.execute(
        "SELECT number,theme,question,answer,answered_at FROM svc_intake_turns WHERE intake_id=? ORDER BY number", (intake_id,))]
    for turn in turns:
        turn["questionHash"] = question_hash(turn["question"])
    owner = c.execute("SELECT name,email FROM users WHERE id=?", (item["user_id"],)).fetchone()
    result = {
        "id": item["id"], "goal": item["goal"], "state": item["state"],
        "step": item["current_step"], "version": item["version"], "turns": turns,
        "created": item["created"], "updated": item["updated"], "submitted": item["submitted"],
        "name": owner["name"], "email": owner["email"], "message": WAITING,
        "result": json.loads(item["result_published"]) if item["result_version"] else None,
        "resultVersion": item["result_version"], "readyAt": item["ready_at"],
    }
    if operator:
        result.update(draft=json.loads(item["result_draft"]), draftVersion=item["draft_version"],
                      publishedDraftVersion=item["published_draft_version"],
                      notifications=notification_view(c, intake_id))
    return result


def summary(c, uid):
    item = c.execute("SELECT id,state,goal,updated FROM svc_intakes WHERE user_id=?", (uid,)).fetchone()
    return dict(item) if item else None


def _prepare_turn(c, intake_id, number):
    """Under the write lock. Persist a usable fallback before reserving AI."""
    topic, fallback = TOPICS[number - 1]
    c.execute("INSERT OR IGNORE INTO svc_intake_turns(intake_id,number,theme,question) VALUES(?,?,?,?)",
              (intake_id, number, topic, fallback))
    if number == 10 or not providers.interview_ready():
        return None
    if c.execute("SELECT 1 FROM svc_intake_ai_requests WHERE intake_id=? AND number=?", (intake_id, number)).fetchone():
        return None
    budget = core.budget(c)  # actual spend/reservations only; no legacy paid admission reserve
    if budget["spent_yen"] + budget["reserved_yen"] + QUESTION_RESERVE > MONTH_LIMIT:
        return None
    request_id = s.ident()
    c.execute("INSERT INTO svc_intake_ai_requests(id,intake_id,number,month,status,reserved_yen,created) VALUES(?,?,?,?,?,?,?)",
              (request_id, intake_id, number, budget["month"], "running", QUESTION_RESERVE, s.stamp()))
    c.execute("UPDATE svc_budgets SET reserved_yen=reserved_yen+? WHERE month=?", (QUESTION_RESERVE, budget["month"]))
    return request_id


def _generate_question(c, request_id):
    if not request_id:
        return
    attempt = c.execute("SELECT * FROM svc_intake_ai_requests WHERE id=?", (request_id,)).fetchone()
    item = c.execute("SELECT goal FROM svc_intakes WHERE id=?", (attempt["intake_id"],)).fetchone()
    history = [dict(r) for r in c.execute(
        "SELECT number,question,answer FROM svc_intake_turns WHERE intake_id=? AND number<? AND answer IS NOT NULL ORDER BY number",
        (attempt["intake_id"], attempt["number"]))]
    c.commit()  # never hold a database lock during provider calls
    question, cost, error, uncertain = None, None, "", False
    try:
        response = providers.interview_question(item["goal"], history, TOPICS[attempt["number"] - 1][0], attempt["number"])
        question = s.text(response.get("question"), 600)
        cost = response.get("cost_yen")
        if type(cost) is not int or not 0 <= cost <= QUESTION_RESERVE:
            raise providers.ProviderError("質問の利用量を確認しています。", uncertain=True)
    except providers.ProviderError as failure:
        uncertain, error = failure.uncertain, "provider_unknown" if failure.uncertain else "provider_failed"
    except (s.Problem, TypeError, AttributeError):
        uncertain, error = True, "invalid_question"
    c.execute("BEGIN IMMEDIATE")
    # A crash before this point leaves the reservation in place and the fallback
    # usable. The same turn is never inferred again, including after a reload.
    if cost is not None and not error:
        c.execute("UPDATE svc_budgets SET reserved_yen=reserved_yen-?,spent_yen=spent_yen+? WHERE month=?",
                  (attempt["reserved_yen"], cost, attempt["month"]))
        c.execute("UPDATE svc_intake_ai_requests SET status='completed',cost_yen=?,result=? WHERE id=?",
                  (cost, question, request_id))
        # A user may have answered the persisted fallback in another tab.
        c.execute("UPDATE svc_intake_turns SET question=? WHERE intake_id=? AND number=? AND answer IS NULL",
                  (question, attempt["intake_id"], attempt["number"]))
    else:
        if not uncertain:
            c.execute("UPDATE svc_budgets SET reserved_yen=reserved_yen-? WHERE month=?",
                      (attempt["reserved_yen"], attempt["month"]))
        c.execute("UPDATE svc_intake_ai_requests SET status=?,error=? WHERE id=?",
                  ("unknown" if uncertain else "failed", error, request_id))
    c.commit()


def start(c, uid, data):
    goal = s.text(data.get("goal"), 1000)
    c.execute("BEGIN IMMEDIATE")
    existing = c.execute("SELECT id FROM svc_intakes WHERE user_id=?", (uid,)).fetchone()
    if existing:
        c.commit()
        return detail(c, uid, existing["id"])
    intake_id = s.ident()
    now = s.stamp()
    c.execute("INSERT INTO svc_intakes(id,user_id,goal,state,created,updated) VALUES(?,?,?,?,?,?)",
              (intake_id, uid, goal, "interview", now, now))
    request_id = _prepare_turn(c, intake_id, 1)
    core.event(c, uid, "intake_started")
    c.commit()
    _generate_question(c, request_id)
    return detail(c, uid, intake_id)


def _origin():
    origin = os.getenv("NOTTA_PUBLIC_ORIGIN", "").rstrip("/")
    s.require(re.fullmatch(r"https://[a-zA-Z0-9.-]+(?::[0-9]+)?", origin) or
              re.fullmatch(r"http://127\.0\.0\.1:[0-9]+", origin), 503, "通知先の設定を確認してください。")
    return origin


def _enqueue(c, item, kind, revision):
    owner = c.execute("SELECT name,email FROM users WHERE id=?", (item["user_id"],)).fetchone()
    footer = "\n\nMAORA株式会社（マオラ）\nhttps://www.maora.co.jp/\n" + OPERATOR
    if kind == "receipt":
        turns = c.execute("SELECT number,question,answer FROM svc_intake_turns WHERE intake_id=? ORDER BY number", (item["id"],)).fetchall()
        body = ("企画のヒアリングを受け付けました。\n受付ID：" + item["id"] +
                "\n受付日時：" + core.dt.datetime.fromtimestamp(item["submitted"], core.JST).isoformat() +
                "\n表示名：" + owner["name"] + "\n確認済みメール：" + owner["email"] +
                "\n\n最初の企画：\n" + item["goal"] + "\n\n" +
                "\n\n".join(str(t["number"]) + ". " + t["question"] + "\n回答：" + (t["answer"] or "（記入なし）") for t in turns) +
                "\n\n運営画面（ログインが必要です）：\n" + _origin() + "/admin/intakes/" + item["id"] + footer)
        payload = {"from": "ノッタ <" + OPERATOR + ">", "to": [OPERATOR], "reply_to": owner["email"],
                   "subject": "【ノッタ】ヒアリング受付 " + item["id"], "text": body}
    else:
        body = (owner["name"] + "さん\n\nお待たせしました。企画の概要とタスク分解ができました。\n" +
                "下のリンクから、ヒアリング時のメールアドレスでログインしてご確認ください。\n" +
                _origin() + "/intakes/" + item["id"] +
                "\n\n受付ID：" + item["id"] + "\nご質問や修正のご相談は、このメールへの返信でお知らせください。" + footer)
        payload = {"from": "ノッタ <" + OPERATOR + ">", "to": [owner["email"]], "reply_to": OPERATOR,
                   "subject": "【ノッタ】企画のタスク分解ができました", "text": body}
    notification_id = s.ident()
    c.execute("INSERT OR IGNORE INTO svc_intake_notifications(id,intake_id,kind,revision,status,payload,idempotency_key,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
              (notification_id, item["id"], kind, revision, "pending", core.dumps(payload),
               "notta-intake-" + item["id"] + "-" + kind + "-" + str(revision), s.stamp(), s.stamp()))
    return notification_id


def send_notification(c, notification_id, retry=False):
    c.execute("BEGIN IMMEDIATE")
    mail = c.execute("SELECT * FROM svc_intake_notifications WHERE id=?", (notification_id,)).fetchone()
    s.require(mail, 404)
    if mail["status"] == "sent" or (mail["status"] != "pending" and not retry):
        c.commit()
        return
    if retry:
        # Even a process crash after the remote service accepts a message must
        # not create another message after its idempotency window has expired.
        s.require(not mail["attempted_at"] or s.stamp() - mail["attempted_at"] < 23 * 3600,
                  409, "再送できる時間を過ぎました。Resendの送信記録で配送結果を確認し、必要な連絡は個別に行ってください。")
        s.require(mail["status"] != "sending" or mail["updated"] <= s.stamp() - 60,
                  409, "送信中です。1分ほど待ってから状態を確認してください。")
    c.execute("UPDATE svc_intake_notifications SET status='sending',updated=?,attempted_at=COALESCE(attempted_at,?) WHERE id=?",
              (s.stamp(), s.stamp(), notification_id))
    c.commit()
    try:
        response = providers.send_intake_notification(json.loads(mail["payload"]), mail["idempotency_key"])
        provider_id = response.get("id") if isinstance(response, dict) else None
        if not isinstance(provider_id, str) or not provider_id or len(provider_id) > 100:
            raise providers.ProviderError("送信結果を確認しています。", uncertain=True)
    except providers.ProviderError as failure:
        c.execute("UPDATE svc_intake_notifications SET status=?,error=?,updated=? WHERE id=?",
                  ("unknown" if failure.uncertain else "failed", "送信結果不明" if failure.uncertain else "送信失敗", s.stamp(), notification_id))
    else:
        c.execute("UPDATE svc_intake_notifications SET status='sent',provider_id=?,error='',updated=? WHERE id=?",
                  (provider_id, s.stamp(), notification_id))
    c.commit()


def answer(c, uid, intake_id, data):
    number = data.get("step")
    s.require(type(number) is int and 1 <= number <= 10, 400, "質問の番号を確認してください。")
    value = s.text(data.get("answer", ""), 2000, blank=number == 10)
    request_id = s.text(data.get("requestId"), 64)
    s.require(re.fullmatch(r"[a-zA-Z0-9_-]{20,64}", request_id), 400, "送信の識別情報を確認してください。")
    c.execute("BEGIN IMMEDIATE")
    item = row(c, intake_id, uid)
    turn = c.execute("SELECT * FROM svc_intake_turns WHERE intake_id=? AND number=?", (intake_id, number)).fetchone()
    if turn and turn["answer"] is not None:
        s.require(turn["answer"] == value and turn["request_id"] == request_id, 409, "この回答は保存済みです。画面を更新して続きをご確認ください。")
        c.commit()
        return detail(c, uid, intake_id)
    s.require(item["state"] == "interview" and item["current_step"] == number and
              data.get("version") == item["version"] and turn and
              data.get("questionHash") == question_hash(turn["question"]), 409,
              "質問が更新されています。入力は残したまま、最新の質問を確認してください。")
    s.require(not c.execute("SELECT 1 FROM svc_intake_turns WHERE request_id=?", (request_id,)).fetchone(),
              409, "送信済みの操作です。最新の画面をご確認ください。")
    c.execute("UPDATE svc_intake_turns SET answer=?,answered_at=?,request_id=? WHERE intake_id=? AND number=?",
              (value, s.stamp(), request_id, intake_id, number))
    notification_id, generation_id = None, None
    if number < 10:
        c.execute("UPDATE svc_intakes SET current_step=?,version=version+1,updated=? WHERE id=?",
                  (number + 1, s.stamp(), intake_id))
        generation_id = _prepare_turn(c, intake_id, number + 1)
    else:
        c.execute("UPDATE svc_intakes SET state='submitted',current_step=10,version=version+1,submitted=?,updated=? WHERE id=?",
                  (s.stamp(), s.stamp(), intake_id))
        item = c.execute("SELECT * FROM svc_intakes WHERE id=?", (intake_id,)).fetchone()
        notification_id = _enqueue(c, item, "receipt", 1)
        core.event(c, uid, "intake_submitted")
    c.commit()
    _generate_question(c, generation_id)
    if notification_id:
        send_notification(c, notification_id)
    return detail(c, uid, intake_id)


def admin_list(c, uid, before=None):
    require_operator(c, uid)
    cursor = 9223372036854775807
    if before:
        s.require(re.fullmatch(r"[0-9]{1,19}", before) and int(before) < cursor, 400)
        cursor = int(before)
    # Stable ordinal pagination. No public email/list endpoint.
    rows = [dict(r) for r in c.execute(
        "SELECT i.id,i.goal,i.state,i.created,i.submitted,i.updated,i.rowid AS cursor,u.name,u.email FROM svc_intakes i JOIN users u ON u.id=i.user_id WHERE i.rowid<? ORDER BY i.rowid DESC LIMIT 51",
        (cursor,))]
    items = rows[:50]
    for item in items:
        item["notifications"] = notification_view(c, item["id"])
    return {"items": items, "next": str(items[-1]["cursor"]) if len(rows) > 50 else None}


def _result(value, complete=False):
    s.require(isinstance(value, dict), 400, "結果を入力してください。")
    cleaned = {key: s.text(value.get(key, ""), limit, blank=not complete)
               for key, limit in (("title", 120), ("overview", 4000), ("firstDeliverable", 2000))}
    tasks = value.get("tasks", [])
    s.require(isinstance(tasks, list) and len(tasks) <= 50 and (not complete or len(tasks) >= 1),
              400, "タスクは1〜50件で入力してください。")
    cleaned["tasks"] = []
    for task in tasks:
        s.require(isinstance(task, dict), 400, "タスクの内容を確認してください。")
        cleaned["tasks"].append({
            key: s.text(task.get(key, ""), limit, blank=not complete)
            for key, limit in (("title", 120), ("instructions", 3000), ("role", 120), ("estimate", 120), ("completion", 2000))
        })
    return cleaned


def save_result(c, uid, intake_id, data):
    require_operator(c, uid)
    result = _result(data.get("result"))
    c.execute("BEGIN IMMEDIATE")
    item = row(c, intake_id, uid, True)
    s.require(item["state"] in ("submitted", "working", "ready"), 409, "ヒアリングの完了をお待ちください。")
    s.require(type(data.get("draftVersion")) is int and data["draftVersion"] == item["draft_version"],
              409, "ほかの画面で保存されています。最新の内容をご確認ください。")
    encoded = core.dumps(result)
    if item["result_draft"] != encoded:
        c.execute("UPDATE svc_intakes SET result_draft=?,draft_version=draft_version+1,state=?,updated=? WHERE id=?",
                  (encoded, "ready" if item["result_version"] else "working", s.stamp(), intake_id))
    c.commit()
    return detail(c, uid, intake_id, True)


def complete(c, uid, intake_id, data):
    require_operator(c, uid)
    c.execute("BEGIN IMMEDIATE")
    item = row(c, intake_id, uid, True)
    s.require(type(data.get("draftVersion")) is int and data["draftVersion"] == item["draft_version"], 409, "最新の下書きを保存してから確定してください。")
    if item["result_version"] and item["published_draft_version"] == item["draft_version"]:
        c.commit()
        return detail(c, uid, intake_id, True)  # double-click/replay never re-notifies
    s.require(item["state"] in ("submitted", "working", "ready"), 409, "ヒアリングの完了をお待ちください。")
    result = _result(json.loads(item["result_draft"]), complete=True)
    revision = item["result_version"] + 1
    c.execute("UPDATE svc_intakes SET result_published=?,result_version=?,published_draft_version=draft_version,state='ready',ready_at=?,updated=? WHERE id=?",
              (core.dumps(result), revision, s.stamp(), s.stamp(), intake_id))
    item = c.execute("SELECT * FROM svc_intakes WHERE id=?", (intake_id,)).fetchone()
    notification_id = _enqueue(c, item, "result", revision)
    core.event(c, uid, "intake_result_published")
    c.commit()
    send_notification(c, notification_id)
    return detail(c, uid, intake_id, True)


def retry_notification(c, uid, intake_id, notification_id):
    require_operator(c, uid)
    row(c, intake_id, uid, True)
    s.require(c.execute("SELECT 1 FROM svc_intake_notifications WHERE id=? AND intake_id=?", (notification_id, intake_id)).fetchone(), 404)
    send_notification(c, notification_id, retry=True)
    return detail(c, uid, intake_id, True)
