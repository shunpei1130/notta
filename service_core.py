"""Notta customer workflow, sharing the existing users/projects/applications tables."""
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets

import server as s
import service_providers as providers

JST = dt.timezone(dt.timedelta(hours=9))
TERMS = "2026-10-monthly-v1"
MONTH_LIMIT = 3000
PAID_RESERVE = 2000  # conservative maximum for the advertised 100 replies / 10 searches
FREE_RESERVE = 190  # nine normal replies plus one search reply


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def month():
    return dt.datetime.fromtimestamp(s.stamp(), JST).strftime("%Y-%m")


def event(c, user, name, project=None):
    c.execute("INSERT INTO svc_events VALUES(?,?,?,?,?)", (s.ident(), user, name, project, s.stamp()))


def email_value(value):
    value = s.text(value, 200).lower()
    s.require(re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,63}", value), 400, "メールアドレスを確認してください。")
    return value


def digest(value):
    key = os.getenv("NOTTA_AUTH_SECRET", "")
    s.require(len(key) >= 32, 503, "メール認証の準備中です。")
    return hmac.new(key.encode(), value.encode(), hashlib.sha256).hexdigest()


def auth_start(c, data, ip):
    s.require(providers.mail_ready(), 503, "メール認証の準備中です。入力した企画はこの画面に残っています。")
    email = email_value(data.get("email"))
    name = s.text(data.get("name", ""), 40, blank=True)
    s.require(data.get("consent") is True, 400, "利用規約と個人情報の取扱いをご確認ください。")
    now = s.stamp()
    ip_hash = digest("ip:" + ip)
    c.execute("BEGIN IMMEDIATE")
    last = c.execute("SELECT created FROM svc_auth WHERE email=? ORDER BY created DESC LIMIT 1", (email,)).fetchone()
    s.require(not last or last[0] <= now - 60, 429, "確認メールの再送は1分後にお試しください。")
    count = c.execute("SELECT COUNT(*) FROM svc_auth WHERE email=? AND created>?", (email, now - 3600)).fetchone()[0]
    ip_count = c.execute("SELECT COUNT(*) FROM svc_auth WHERE ip_hash=? AND created>?", (ip_hash, now - 3600)).fetchone()[0]
    total = c.execute("SELECT COUNT(*) FROM svc_auth WHERE created>?", (now - 86400,)).fetchone()[0]
    s.require(count < 3 and ip_count < 20 and total < 150, 429, "確認メールの回数上限です。時間をおいてお試しください。")
    challenge, code = s.ident(), str(secrets.randbelow(1000000)).zfill(6)
    c.execute("INSERT INTO svc_auth(id,email,name,ip_hash,code_hash,expires,created) VALUES(?,?,?,?,?,?,?)",
              (challenge, email, name, ip_hash, digest(challenge + ":" + code), now + 600, now))
    c.commit()
    try:
        providers.send_code(email, code, challenge)
    except providers.ProviderError as error:
        if not error.uncertain:
            c.execute("UPDATE svc_auth SET used=1 WHERE id=?", (challenge,))
            c.commit()
            raise
        return {"challenge": challenge, "delivery": "unknown", "message": "送信結果を確認中です。メールが届いていれば、そのコードを入力してください。"}
    return {"challenge": challenge, "delivery": "accepted", "message": "確認コードを送りました。10分以内に入力してください。"}


def auth_verify(c, data):
    challenge = s.text(data.get("challenge"), 24)
    code = s.text(data.get("code"), 6)
    s.require(re.fullmatch(r"[0-9]{6}", code), 400, "6桁の確認コードを入力してください。")
    c.execute("BEGIN IMMEDIATE")
    row = c.execute("SELECT * FROM svc_auth WHERE id=?", (challenge,)).fetchone()
    s.require(row and not row["used"] and row["expires"] > s.stamp() and row["attempts"] < 5, 403, "コードが無効・期限切れです。確認メールを送り直してください。")
    if not hmac.compare_digest(row["code_hash"], digest(challenge + ":" + code)):
        c.execute("UPDATE svc_auth SET attempts=attempts+1 WHERE id=?", (challenge,))
        c.commit()
        raise s.Problem(403, "確認コードが違います。")
    user = c.execute("SELECT * FROM users WHERE email=?", (row["email"],)).fetchone()
    if not user:
        uid = s.ident()
        c.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (uid, row["email"], row["name"] or "参加者", "creator", secrets.token_hex(16), "disabled-password-login"))
        user = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    c.execute("INSERT OR IGNORE INTO svc_accounts(user_id,verified_at,created) VALUES(?,?,?)", (user["id"], s.stamp(), s.stamp()))
    c.execute("UPDATE svc_auth SET used=1 WHERE email=?", (row["email"],))
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    c.execute("INSERT INTO sessions VALUES(?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), user["id"], csrf, s.stamp() + 28800))
    event(c, user["id"], "verified_login")
    c.commit()
    return {"user": {key: user[key] for key in ("id", "name", "email")}, "csrf": csrf}, token


def subscription(c, uid):
    row = c.execute("SELECT * FROM svc_subscriptions WHERE user_id=? ORDER BY first_paid DESC,checked_at DESC LIMIT 1", (uid,)).fetchone()
    return dict(row) if row else None


def paid(c, uid):
    sub = subscription(c, uid)
    return bool(sub and sub["paid_until"] > s.stamp() and not sub["refunded"]
                and sub["status"] in ("ACTIVE", "CANCELED"))


def require_paid(c, uid):
    s.require(paid(c, uid), 402, "募集と共同作業には月額プランが必要です。保存した企画は引き続き確認・編集できます。")


def quota(c, uid):
    mode = "paid" if paid(c, uid) else "free"
    sub = subscription(c, uid)
    period = (sub["period"] or month()) if mode == "paid" else "lifetime"
    rows = c.execute("SELECT status,search_used FROM svc_ai_requests WHERE user_id=? AND mode=? AND period=? AND status IN ('reserved','running','completed','unknown')", (uid, mode, period)).fetchall()
    used = len(rows)
    searches = sum(row["search_used"] for row in rows)
    return {"mode": mode, "period": period, "repliesUsed": used, "repliesLimit": 100 if mode == "paid" else 10,
            "searchesUsed": searches, "searchesLimit": 10 if mode == "paid" else 1}


def budget(c):
    key = month()
    c.execute("INSERT OR IGNORE INTO svc_budgets(month) VALUES(?)", (key,))
    return dict(c.execute("SELECT * FROM svc_budgets WHERE month=?", (key,)).fetchone())


def commitments(c, exclude_uid=None, new_paid_uid=None, reserve_paid_full=False):
    # Reserve the first paid member's capacity even before acquisition.
    owners = {r[0] for r in c.execute("SELECT user_id FROM svc_subscriptions WHERE paid_until>? AND refunded=0 AND status IN ('ACTIVE','CANCELED')", (s.stamp(),))}
    pending = {r[0] for r in c.execute("SELECT user_id FROM svc_checkouts WHERE status IN ('reserved','pending','unknown')")}
    if new_paid_uid:
        pending.add(new_paid_uid)
    members = owners | pending
    total = 0 if members else PAID_RESERVE
    for uid in members:
        if uid == exclude_uid and uid != new_paid_uid:
            continue
        if uid in owners and not reserve_paid_full:
            q = quota(c, uid)
            left = max(0, q['repliesLimit'] - q['repliesUsed'])
            total += left * 10 + min(left, max(0, q['searchesLimit'] - q['searchesUsed'])) * 90
        else:
            total += PAID_RESERVE
    # Admit a free member only when the whole advertised trial still fits.
    # Each call consumes its own allocation; other members' capacity is preserved.
    free = {r[0] for r in c.execute("SELECT DISTINCT a.user_id FROM svc_accounts a JOIN projects p ON p.owner=a.user_id JOIN svc_projects v ON v.project=p.id WHERE a.trial_allocated=1 AND v.state!='archived'")}
    for uid in free - members:
        if uid == exclude_uid:
            continue
        q = quota(c, uid)
        left = max(0, 10 - q['repliesUsed'])
        total += left * 10 + min(left, max(0, 1 - q['searchesUsed'])) * 90
    return total


def new_project(c, uid, data):
    goal = s.text(data.get("goal"), 1000)
    c.execute("BEGIN IMMEDIATE")
    account = c.execute("SELECT * FROM svc_accounts WHERE user_id=?", (uid,)).fetchone()
    s.require(account, 403, "メール認証を完了してください。")
    current = c.execute("SELECT p.id FROM projects p JOIN svc_projects v ON v.project=p.id WHERE p.owner=? AND v.state!='archived'", (uid,)).fetchone()
    s.require(not current, 409, "同時に進められる企画は1件です。現在の企画を完了してから次へ進んでください。")
    if not paid(c, uid):
        s.require(not account["trial_allocated"], 402, "無料で作成できる企画は1件です。")
        count = c.execute("SELECT COUNT(*) FROM svc_accounts WHERE trial_allocated=1").fetchone()[0]
        b = budget(c)
        s.require(count < 30 and b["spent_yen"] + b["reserved_yen"] + commitments(c, reserve_paid_full=True) + FREE_RESERVE <= MONTH_LIMIT, 429, "今月の無料体験受付は終了しました。保存済みの企画は確認できます。")
        c.execute("UPDATE svc_accounts SET trial_allocated=1 WHERE user_id=?", (uid,))
    pid = s.ident()
    draft = {"title": goal[:60], "goal": goal, "success": "", "output": "", "schedule": "", "conditions": "", "recruitment": "", "roles": [], "questions": [], "unknowns": [], "sources": []}
    now = s.stamp()
    c.execute("INSERT INTO projects(id,owner,title,goal,output,conditions,days,created) VALUES(?,?,?,?,?,?,?,?)", (pid, uid, draft["title"], goal, "", "", 36500, now))
    c.execute("INSERT INTO svc_projects(project,slug,state,draft,version,created,updated) VALUES(?,?,?,?,?,?,?)", (pid, secrets.token_urlsafe(12), "draft", dumps(draft), 1, now, now))
    event(c, uid, "trial_started", pid)
    c.commit()
    return {"id": pid}


def project_row(c, pid, uid):
    row = c.execute("SELECT p.owner,v.* FROM svc_projects v JOIN projects p ON p.id=v.project WHERE v.project=?", (pid,)).fetchone()
    s.require(row and row["owner"] == uid, 404, "企画が見つかりません。")
    return dict(row)


def validate_draft(data):
    s.require(isinstance(data, dict))
    lengths = {"title": 80, "goal": 1000, "success": 1000, "output": 1000, "schedule": 1500, "conditions": 2000, "recruitment": 3000}
    result = {key: s.text(data.get(key, ""), limit, blank=key not in ("title", "goal")) for key, limit in lengths.items()}
    roles = data.get("roles", [])
    s.require(isinstance(roles, list) and len(roles) <= 3)
    result["roles"] = [{"title": s.text(row.get("title"), 80), "output": s.text(row.get("output"), 500)} for row in roles if isinstance(row, dict)]
    s.require(len(result["roles"]) == len(roles))
    for key in ("questions", "unknowns"):
        values = data.get(key, [])
        s.require(isinstance(values, list) and len(values) <= 5)
        result[key] = [s.text(value, 300) for value in values]
    return result


def save_draft(c, row, draft):
    applications = c.execute("SELECT 1 FROM applications WHERE project=?", (row["project"],)).fetchone()
    s.require(not applications, 409, "応募後は募集条件を固定しています。作業画面から進めてください。")
    c.execute("DELETE FROM tasks WHERE project=?", (row["project"],))
    for role in draft["roles"]:
        c.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?)", (s.ident(), row["project"], None, 0, role["title"], role["title"][:40], role["output"], None))
    c.execute("UPDATE svc_projects SET draft=?,version=version+1,updated=?,state='draft' WHERE project=?", (dumps(draft), s.stamp(), row["project"]))
    c.execute("UPDATE projects SET title=?,goal=?,output=?,conditions=?,version=version+1,launched=0 WHERE id=?", (draft["title"], draft["goal"], draft["output"], draft["conditions"], row["project"]))


def edit_project(c, uid, pid, data):
    c.execute("BEGIN IMMEDIATE")
    row = project_row(c, pid, uid)
    s.require(not c.execute("SELECT 1 FROM svc_ai_requests WHERE project=? AND status IN ('reserved','running')", (pid,)).fetchone(), 409, "AIが処理中です。完了後に編集してください。")
    s.require(row["state"] != "archived", 409, "完了した企画は閲覧できます。")
    s.require(type(data.get("version")) is int and data["version"] == row["version"], 409, "別の操作で更新されました。入力を控えて再読込してください。")
    draft = validate_draft(data.get("draft"))
    draft["sources"] = json.loads(row["draft"]).get("sources", [])
    save_draft(c, row, draft)
    c.commit()
    return {"ok": True}


def assist(c, uid, pid, data):
    s.require(providers.ai_ready(), 503, "AIによる企画づくりは準備中です。入力した内容は保存されています。")
    request_id = s.text(data.get("requestId"), 64)
    s.require(re.fullmatch(r"[a-f0-9-]{24,64}", request_id))
    prompt = s.text(data.get("prompt"), 1500)
    search = data.get("search") is True
    if search:
        s.require(providers.search_ready(), 503, "検索は利用枠の設定中です。AIとの相談と編集は使えます。")
        s.require(data.get("searchConsent") is True, 400, "検索に送る語句を確認してください。")
        s.text(data.get("searchQuery"), 160)
    fingerprint = hashlib.sha256(dumps({"user": uid, "project": pid, "prompt": prompt, "search": search, "searchQuery": data.get("searchQuery") if search else None, "version": data.get("version")}).encode()).hexdigest()
    c.execute("BEGIN IMMEDIATE")
    previous = c.execute("SELECT * FROM svc_ai_requests WHERE id=?", (request_id,)).fetchone()
    if previous:
        s.require(previous["user_id"] == uid and previous["fingerprint"] == fingerprint, 409, "同じ依頼IDに別の内容は送れません。")
        if previous["status"] == "completed":
            c.commit()
            return json.loads(previous["result"])
        raise s.Problem(409, "この依頼は処理済み、または結果を確認中です。画面を読み直して保存済みの内容を確認してください。")
    row = project_row(c, pid, uid)
    s.require(row["state"] != "archived" and data.get("version") == row["version"], 409, "保存内容が変わっています。再読込してください。")
    s.require(not c.execute("SELECT 1 FROM applications WHERE project=?", (pid,)).fetchone(), 409, "応募後の募集条件は変更できません。")
    s.require(not c.execute("SELECT 1 FROM svc_ai_requests WHERE user_id=? AND status IN ('reserved','running','unknown')", (uid,)).fetchone(), 409, "前の依頼を確認しています。結果が確定するまで再送しないでください。")
    q = quota(c, uid)
    s.require(q["repliesUsed"] < q["repliesLimit"] and (not search or q["searchesUsed"] < q["searchesLimit"]), 429, "AIの利用回数に達しました。作成済みの内容は編集・保存できます。")
    reserved = 100 if search else 10
    b = budget(c)
    # Paid calls consume their own reserved capacity; free calls cannot consume it.
    floor = commitments(c, exclude_uid=uid)
    s.require(b["spent_yen"] + b["reserved_yen"] + floor + reserved <= MONTH_LIMIT, 429, "今月のAI受付を停止しています。保存・手編集と契約管理は引き続き使えます。")
    c.execute("UPDATE svc_budgets SET reserved_yen=reserved_yen+? WHERE month=?", (reserved, month()))
    c.execute("INSERT INTO svc_ai_requests(id,user_id,project,fingerprint,mode,period,status,reserved_yen,search_used,created) VALUES(?,?,?,?,?,?,?,?,?,?)", (request_id, uid, pid, fingerprint, q["mode"], q["period"], "running", reserved, int(search), s.stamp()))
    messages = [dict(r) for r in c.execute("SELECT role,body FROM svc_messages WHERE project=? ORDER BY rowid", (pid,)).fetchall()]
    draft = json.loads(row["draft"])
    c.commit()  # do not hold a database write lock while a provider is running
    try:
        output = providers.generate(draft, messages, prompt, data.get("searchQuery") if search else None)
        next_draft = validate_draft(output["draft"])
        s.require(2 <= len(next_draft["roles"]) <= 3 and next_draft["success"] and next_draft["output"] and next_draft["recruitment"], 502, "AIの応答に必要な項目がありません。")
        reply = s.text(output["reply"], 2000)
        cost = output.get("cost_yen")
        s.require(type(cost) is int and cost >= 0, 502, "AIの利用量を確認しています。")
        sources = output.get("sources", [])
        next_draft["sources"] = sources
        c.execute("BEGIN IMMEDIATE")
        latest = project_row(c, pid, uid)
        s.require(latest["version"] == row["version"] and latest["state"] != "archived", 409, "処理中に企画が更新されました。保存内容を確認してください。")
        save_draft(c, latest, next_draft)
        for role, body in (("user", prompt), ("assistant", reply)):
            c.execute("INSERT INTO svc_messages VALUES(?,?,?,?,?)", (s.ident(), pid, role, body, s.stamp()))
        result = {"reply": reply, "draft": next_draft, "sources": sources,
                  "searchEntryPoint": output.get("search_entry_point", "")}
        c.execute("UPDATE svc_ai_requests SET status='completed',result=?,cost_yen=? WHERE id=?", (dumps(result), cost, request_id))
        c.execute("UPDATE svc_budgets SET reserved_yen=reserved_yen-?,spent_yen=spent_yen+? WHERE month=?", (reserved, cost, dt.datetime.fromtimestamp(c.execute("SELECT created FROM svc_ai_requests WHERE id=?", (request_id,)).fetchone()[0], JST).strftime("%Y-%m")))
        event(c, uid, "trial_completed" if q["mode"] == "free" else "paid_assist", pid)
        c.commit()
        return result
    except Exception as error:
        c.rollback()
        uncertain = not isinstance(error, providers.ProviderError) or error.uncertain
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE svc_ai_requests SET status=?,error=? WHERE id=?", ("unknown" if uncertain else "failed", "生成結果の確認が必要" if uncertain else "外部サービスが拒否", request_id))
        if not uncertain:
            c.execute("UPDATE svc_budgets SET reserved_yen=reserved_yen-? WHERE month=?", (reserved, b["month"]))
        c.commit()
        raise


def project_detail(c, uid, pid):
    row = project_row(c, pid, uid)
    row["draft"] = json.loads(row["draft"])
    row["messages"] = [dict(r) for r in c.execute("SELECT role,body,created FROM svc_messages WHERE project=? ORDER BY rowid", (pid,))]
    row["roles"] = [dict(r) for r in c.execute("SELECT id,title,role,output FROM tasks WHERE project=? ORDER BY rowid", (pid,))]
    row["applications"] = [dict(r) for r in c.execute("SELECT a.id,a.task,a.fan,a.experience,a.status,a.version,a.submission,a.feedback,a.confirmation,a.confirmed_at,u.name FROM applications a JOIN users u ON u.id=a.fan WHERE a.project=? ORDER BY a.rowid", (pid,))]
    row["quota"] = quota(c, uid)
    row["paid"] = paid(c, uid)
    grounding = c.execute("SELECT id,result FROM svc_ai_requests WHERE project=? AND search_used=1 AND status='completed' ORDER BY created DESC LIMIT 1", (pid,)).fetchone()
    row["groundingRequestId"] = grounding[0] if grounding and json.loads(grounding[1]).get("searchEntryPoint") else ""
    return row


def public_project(c, slug):
    row = c.execute("SELECT p.owner,u.name,v.project,v.state,v.draft,v.slug FROM svc_projects v JOIN projects p ON p.id=v.project JOIN users u ON u.id=p.owner WHERE v.slug=?", (slug,)).fetchone()
    s.require(row and row["state"] == "published" and paid(c, row["owner"]), 404, "この募集は現在公開されていません。")
    d = json.loads(row["draft"])
    result = {key: d.get(key, "") for key in ("title", "goal", "success", "output", "schedule", "conditions", "recruitment", "questions")}
    result.update({"id": row["project"], "slug": slug, "creator": row["name"], "roles": [dict(r) for r in c.execute("SELECT id,title,output FROM tasks WHERE project=? ORDER BY rowid", (row["project"],))]})
    return result


def publish(c, uid, pid, data):
    c.execute("BEGIN IMMEDIATE")
    require_paid(c, uid)
    row = project_row(c, pid, uid)
    s.require(data.get("version") == row["version"] and row["state"] != "archived", 409, "保存内容を確認してください。")
    d = json.loads(row["draft"])
    s.require(data.get("consent") is True and all(d.get(k) for k in ("success", "output", "conditions", "recruitment")) and 2 <= len(d["roles"]) <= 3, 400, "目的・成果・役割・参加条件を確認してから公開してください。")
    c.execute("UPDATE svc_projects SET state='published',version=version+1,updated=? WHERE project=?", (s.stamp(), pid))
    c.execute("UPDATE projects SET launched=1,started=COALESCE(started,?),version=version+1 WHERE id=?", (s.stamp(), pid))
    event(c, uid, "published", pid)
    c.commit()
    return {"url": "/p/" + row["slug"]}


def archive(c, uid, pid, data):
    c.execute("BEGIN IMMEDIATE")
    row = project_row(c, pid, uid)
    s.require(data.get("version") == row["version"] and data.get("confirm") is True, 409, "企画を完了する確認が必要です。")
    c.execute("UPDATE svc_projects SET state='archived',version=version+1,updated=? WHERE project=?", (s.stamp(), pid))
    c.execute("UPDATE projects SET launched=0,version=version+1 WHERE id=?", (pid,))
    event(c, uid, "archived", pid)
    c.commit()
    return {"ok": True}


def apply(c, uid, pid, data):
    c.execute("BEGIN IMMEDIATE")
    row = c.execute("SELECT p.owner,v.state,v.draft FROM svc_projects v JOIN projects p ON p.id=v.project WHERE v.project=?", (pid,)).fetchone()
    s.require(row and row["state"] == "published", 404, "募集が見つかりません。")
    s.require(paid(c, row["owner"]), 409, "この企画は現在、応募の受付を停止しています。参加者への課金はありません。")
    s.require(uid != row["owner"], 400, "ご自身の企画には応募できません。")
    s.require(data.get("consent") is True, 400, "参加条件と記録範囲をご確認ください。")
    task = c.execute("SELECT id FROM tasks WHERE project=? AND id=?", (pid, data.get("task"))).fetchone()
    s.require(task, 400, "募集されている役割を選んでください。")
    s.require(c.execute("SELECT COUNT(*) FROM applications WHERE project=?", (pid,)).fetchone()[0] < 100, 429, "応募受付の上限に達しました。")
    s.require(not c.execute("SELECT 1 FROM applications WHERE task=? AND fan=?", (task[0], uid)).fetchone(), 409, "この役割には応募済みです。")
    aid = s.ident()
    body = s.text(data.get("experience"), 2000)
    c.execute("INSERT INTO applications(id,project,task,fan,experience,status) VALUES(?,?,?,?,?,?)", (aid, pid, task[0], uid, body, "応募"))
    event(c, uid, "applied", pid)
    c.commit()
    return {"id": aid}


def application_action(c, uid, aid, action, data):
    c.execute("BEGIN IMMEDIATE")
    row = c.execute("SELECT a.*,p.owner,v.state FROM applications a JOIN projects p ON p.id=a.project JOIN svc_projects v ON v.project=a.project WHERE a.id=?", (aid,)).fetchone()
    s.require(row and uid in (row["owner"], row["fan"]), 404, "応募が見つかりません。")
    s.require(data.get("version") == row["version"], 409, "応募が更新されました。再読込してください。")
    if action == "cancel":
        s.require(uid == row["fan"], 403)
        c.execute("UPDATE applications SET status='取消',confirmation='',confirmed_at=NULL,version=version+1 WHERE id=?", (aid,))
    else:
        if uid == row['owner']:
            require_paid(c, row["owner"])
        else:
            s.require(paid(c, row["owner"]), 409, "企画者の利用期間が終了したため、共同作業を停止しています。参加者への課金はありません。")
        s.require(row["state"] != "archived", 409, "完了した企画は閲覧できます。")
        if action == "submit":
            s.require(uid == row["fan"] and row["status"] in ("担当", "差戻し"), 403, "担当決定後、または差戻し後に提出できます。")
            s.require(c.execute("SELECT COUNT(*) FROM revisions WHERE application=?", (aid,)).fetchone()[0] < 20, 429, "提出は20版までです。企画者とご相談ください。")
            body = s.text(data.get("body"), 4000)
            c.execute("INSERT INTO revisions(application,body,created) VALUES(?,?,?)", (aid, body, s.stamp()))
            c.execute("UPDATE applications SET status='提出',submission=?,confirmation='',confirmed_at=NULL,version=version+1 WHERE id=?", (body, aid))
        else:
            s.require(action == "status" and uid == row["owner"], 403)
            target = data.get("status")
            allowed = {"応募": ("候補", "担当", "見送り"), "候補": ("担当", "見送り"), "提出": ("確認済", "差戻し")}
            s.require(target in allowed.get(row["status"], ()), 409, "現在の状態からは変更できません。")
            comment = s.text(data.get("comment", ""), 1000, blank=target not in ("確認済", "差戻し"))
            c.execute("UPDATE applications SET status=?,feedback=?,confirmation=?,confirmed_at=?,version=version+1 WHERE id=?", (target, comment, comment if target == "確認済" else "", s.stamp() if target == "確認済" else None, aid))
    event(c, uid, "application_" + action, row["project"])
    c.commit()
    return {"ok": True}
