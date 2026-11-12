"""First-project collaboration, private edits and explicit public snapshots."""
import copy
import hashlib
import json
import os
import re
import secrets

import server as s
import service_core as core
import service_intake as intake
import service_providers as providers
import workspace_ai

LIMIT = 30
MONTH_LIMIT = 2000
RESERVE = 10
ASSIGNED = ("担当", "提出", "確認済", "差戻し")
PROJECT_FIELDS = {"title": 120, "overview": 4000, "firstDeliverable": 2000}
PUBLIC_FIELDS = {"title": 120, "overview": 4000, "schedule": 1500, "conditions": 2000, "recruitment": 3000}
TASK_FIELDS = {"title": 120, "instructions": 3000, "role": 120, "estimate": 120, "completion": 2000}


def enabled():
    return os.getenv("NOTTA_WORKSPACE_ENABLED") == "yes"


def exists(c, pid):
    return bool(c.execute("SELECT 1 FROM svc_workspaces WHERE project=?", (pid,)).fetchone())


def require_enabled():
    s.require(enabled(), 503, "共同作業の画面はメンテナンス中です。保存した内容は保持されています。")


def owner_row(c, uid, pid):
    require_enabled()
    row = c.execute("SELECT p.owner,v.*,w.intake,w.published FROM svc_projects v JOIN projects p ON p.id=v.project JOIN svc_workspaces w ON w.project=v.project WHERE v.project=?", (pid,)).fetchone()
    s.require(row and row["owner"] == uid, 404, "企画が見つかりません。")
    return dict(row)


def text_fields(value, fields, required=False):
    s.require(isinstance(value, dict), 400, "入力の形式を確認してください。")
    return {k: s.text(value.get(k, ""), n, blank=not required) for k, n in fields.items()}


def validate(value):
    result = text_fields(value, PROJECT_FIELDS)
    s.require(result["title"], 400, "企画名を入力してください。")
    result["public"] = text_fields(value.get("public", {}), PUBLIC_FIELDS)
    tasks = value.get("tasks", [])
    s.require(isinstance(tasks, list) and 1 <= len(tasks) <= 50, 400, "タスクは1〜50件にしてください。")
    result["tasks"], ids = [], set()
    for task in tasks:
        t = text_fields(task, TASK_FIELDS)
        tid = s.text(task.get("id"), 24)
        s.require(re.fullmatch(r"[a-f0-9]{24}", tid) and tid not in ids and t["title"], 400, "タスクのIDと名前を確認してください。")
        ids.add(tid)
        status = task.get("status", "未着手")
        s.require(status in ("未着手", "進行中", "完了"), 400)
        recruit = task.get("recruit", False)
        s.require(type(recruit) is bool, 400)
        offer = text_fields(task.get("offer", {}), {**TASK_FIELDS, "terms": 2000})
        capacity = task.get("capacity", 1)
        s.require(type(capacity) is int and 1 <= capacity <= 50, 400, "募集人数は1〜50名にしてください。")
        confirmed = task.get("termsConfirmed", False)
        s.require(type(confirmed) is bool, 400)
        result["tasks"].append({**t, "id": tid, "status": status, "recruit": recruit,
                                "capacity": capacity, "offer": offer, "termsConfirmed": confirmed})
    artifacts = value.get("artifacts", [])
    s.require(isinstance(artifacts, list) and len(artifacts) <= 50, 400, "資料は50件まで保存できます。")
    result["artifacts"], artifact_ids = [], set()
    for artifact in artifacts:
        a = text_fields(artifact, {"id": 24, "title": 120, "body": 12000, "taskId": 24})
        s.require(re.fullmatch(r"[a-f0-9]{24}", a["id"]) and a["id"] not in artifact_ids and a["title"], 400)
        s.require(not a["taskId"] or a["taskId"] in ids, 400, "資料に紐づくタスクを確認してください。")
        artifact_ids.add(a["id"])
        result["artifacts"].append(a)
    return result


def _change_summary(before, after):
    changes = []
    for key, label in (("title", "企画名"), ("overview", "企画の概要"), ("firstDeliverable", "最初の成果物")):
        if before.get(key) != after.get(key):
            changes.append(label)
    if before.get("public") != after.get("public"):
        changes.append("募集ページの下書き")
    labels = {"title": "名前", "instructions": "作業内容", "role": "役割", "estimate": "時間", "completion": "完了条件",
              "status": "進捗", "recruit": "募集対象", "capacity": "募集人数", "offer": "募集条件", "termsConfirmed": "条件確認"}
    for collection, label in (("tasks", "タスク"), ("artifacts", "資料")):
        previous = {item["id"]: item for item in before.get(collection, [])}
        current = {item["id"]: item for item in after.get(collection, [])}
        for key, item in current.items():
            name = label + "「" + item["title"] + "」"
            if key not in previous:
                changes.append(name + "を追加")
            elif item != previous[key]:
                fields = [value for field, value in labels.items() if previous[key].get(field) != item.get(field)] if collection == "tasks" else ["内容"]
                changes.append(name + "の" + "・".join(fields))
        changes.extend(label + "「" + item["title"] + "」を削除" for key, item in previous.items() if key not in current)
    return "、".join(changes) if changes else "内容の変更なし"


def _save(c, row, draft, reason):
    draft = validate(draft)
    before = json.loads(row["draft"])
    prior = {t["id"] for t in before["tasks"]}
    next_ids = {t["id"] for t in draft["tasks"]}
    for tid in prior - next_ids:
        s.require(not c.execute("SELECT 1 FROM applications WHERE task=?", (tid,)).fetchone(), 409,
                  "応募のあるタスクは削除できません。募集を停止し、新しいタスクを追加してください。")
        c.execute("DELETE FROM tasks WHERE id=? AND project=?", (tid, row["project"]))
    for t in draft["tasks"]:
        found = c.execute("SELECT project FROM tasks WHERE id=?", (t["id"],)).fetchone()
        s.require(not found or found[0] == row["project"], 400, "別の企画のタスクは使えません。")
        if found:
            c.execute("UPDATE tasks SET title=?,role=?,output=? WHERE id=?", (t["title"], t["role"], t["completion"], t["id"]))
        else:
            c.execute("INSERT INTO tasks(id,project,parent,depth,title,role,output,reference) VALUES(?,?,NULL,0,?,?,?,NULL)",
                      (t["id"], row["project"], t["title"], t["role"], t["completion"]))
    version = row["version"] + 1
    encoded = core.dumps(draft)
    c.execute("UPDATE svc_projects SET draft=?,version=?,updated=? WHERE project=?", (encoded, version, s.stamp(), row["project"]))
    c.execute("UPDATE projects SET title=?,goal=?,output=?,version=version+1 WHERE id=?", (draft["title"], draft["overview"], draft["firstDeliverable"], row["project"]))
    c.execute("INSERT INTO svc_workspace_history(project,version,draft,reason,created) VALUES(?,?,?,?,?)", (row["project"], version, encoded, reason + "：" + _change_summary(before, draft), s.stamp()))
    return version


def _editable(c, uid, pid, version):
    row = owner_row(c, uid, pid)
    s.require(type(version) is int and version == row["version"], 409, "ほかの画面で更新されました。入力を控えて最新の内容を読み込んでください。")
    s.require(row["state"] != "archived", 409, "完了した企画は閲覧のみです。")
    return row


def deliver(c, operator, intake_id, data):
    require_enabled()
    intake.require_operator(c, operator)
    return _deliver(c, operator, intake_id, data)


def deliver_managed(c, intake_id, data, expected_email):
    """Operational import through the authenticated DB owner, never an HTTP route.

    No user session is created, and the runtime role cannot use this path.
    The explicit recipient must already own a verified completed intake.
    """
    require_enabled()
    s.require(getattr(c, "dialect", "") == "postgres", 403)
    identity = c.execute("SELECT current_user,current_database()").fetchone()
    s.require(identity[0] == "notta_owner" and identity[1] == "notta", 403, "認証されたノッタのDB管理接続が必要です。")
    recipient = c.execute("SELECT u.email FROM svc_intakes i JOIN users u ON u.id=i.user_id JOIN svc_accounts a ON a.user_id=u.id WHERE i.id=? AND a.verified_at>0", (intake_id,)).fetchone()
    s.require(recipient and recipient[0] == expected_email, 409, "受付の本人と反映先が一致しません。")
    return _deliver(c, None, intake_id, data)


def _deliver(c, operator, intake_id, data):
    c.execute("BEGIN IMMEDIATE")
    item = c.execute("SELECT * FROM svc_intakes WHERE id=?", (intake_id,)).fetchone()
    s.require(item, 404, "受付が見つかりません。")
    linked = c.execute("SELECT project FROM svc_workspaces WHERE intake=?", (intake_id,)).fetchone()
    if linked:
        c.commit()
        return {"projectId": linked[0], "created": False}
    s.require(item["state"] in ("submitted", "working", "ready"), 409, "ヒアリングの完了をお待ちください。")
    s.require(data.get("draftVersion") == item["draft_version"], 409, "最新の下書きを確認してください。")
    result = intake._result(data.get("result", json.loads(item["result_draft"])), complete=True)
    uid = item["user_id"]
    s.require(not c.execute("SELECT 1 FROM projects p JOIN svc_projects v ON v.project=p.id WHERE p.owner=? AND v.state!='archived'", (uid,)).fetchone(), 409,
              "すでに進行中の企画があります。既存の企画を完了してから届けてください。")
    indexes = data.get("recruitTaskIndexes", [])
    s.require(isinstance(indexes, list) and all(type(i) is int and 0 <= i < len(result["tasks"]) for i in indexes), 400)
    tasks = []
    for i, t in enumerate(result["tasks"]):
        tasks.append({**t, "id": s.ident(), "status": "未着手", "recruit": i in indexes,
                      "capacity": 1, "offer": {**t, "terms": ""}, "termsConfirmed": False})
    draft = {"title": result["title"], "overview": result["overview"], "firstDeliverable": result["firstDeliverable"],
             "public": {"title": result["title"], "overview": result["overview"], "schedule": "", "conditions": "", "recruitment": ""},
             "tasks": tasks, "artifacts": []}
    pid, now = s.ident(), s.stamp()
    c.execute("INSERT INTO projects(id,owner,title,goal,output,conditions,days,created) VALUES(?,?,?,?,?,'',36500,?)", (pid, uid, draft["title"], draft["overview"], draft["firstDeliverable"], now))
    c.execute("INSERT INTO svc_projects(project,slug,state,draft,version,created,updated) VALUES(?,?,'draft',?,0,?,?)", (pid, secrets.token_urlsafe(12), core.dumps({"tasks": []}), now, now))
    c.execute("INSERT INTO svc_workspaces(project,intake,created) VALUES(?,?,?)", (pid, intake_id, now))
    _save(c, {"project": pid, "version": 0, "draft": core.dumps({"tasks": []})}, draft, "運営から初回の企画を提供")
    encoded = core.dumps(result)
    c.execute("UPDATE svc_intakes SET result_draft=?,result_published=?,draft_version=draft_version+1,result_version=result_version+1,published_draft_version=draft_version+1,state='ready',ready_at=?,updated=? WHERE id=?", (encoded, encoded, now, now, intake_id))
    core.event(c, operator, "workspace_delivered" if operator else "workspace_delivered_db_operator", pid)
    c.commit()
    return {"projectId": pid, "created": True}


def ai_quota(c, pid):
    count = c.execute("SELECT COUNT(*) FROM svc_ai_requests WHERE project=? AND mode='workspace' AND status IN ('running','reserved','completed','unknown')", (pid,)).fetchone()[0]
    return {"used": count, "limit": LIMIT}


def _operator_workspace(c, operator, intake_id):
    require_enabled()
    intake.require_operator(c, operator)
    source = intake.row(c, intake_id, operator, True)
    linked = c.execute("SELECT project FROM svc_workspaces WHERE intake=?", (intake_id,)).fetchone()
    s.require(linked, 404, "まだ企画として提供されていません。")
    return source, linked[0]


def operator_detail(c, operator, intake_id):
    source, pid = _operator_workspace(c, operator, intake_id)
    result = detail(c, source["user_id"], pid)
    protected = [r[0] for r in c.execute("SELECT DISTINCT task FROM applications WHERE project=?", (pid,))]
    recipient = c.execute("SELECT name,email FROM users WHERE id=?", (source["user_id"],)).fetchone()
    result.update(readOnly=True, operatorMode=True, canEdit=result["state"] != "archived",
                  recipient=dict(recipient), protectedTaskIds=protected, applications=[], messages=[])
    return result


def operator_edit(c, operator, intake_id, data):
    c.execute("BEGIN IMMEDIATE")
    source, pid = _operator_workspace(c, operator, intake_id)
    row = _editable(c, source["user_id"], pid, data.get("version"))
    _save(c, row, data.get("draft"), "運営が編集")
    core.event(c, operator, "workspace_edited_operator", pid)
    c.commit()
    return operator_detail(c, operator, intake_id)


def operator_history(c, operator, intake_id):
    source, pid = _operator_workspace(c, operator, intake_id)
    return history(c, source["user_id"], pid)


def operator_restore(c, operator, intake_id, data):
    c.execute("BEGIN IMMEDIATE")
    source, pid = _operator_workspace(c, operator, intake_id)
    row = _editable(c, source["user_id"], pid, data.get("version"))
    target = c.execute("SELECT draft FROM svc_workspace_history WHERE project=? AND version=?", (pid, data.get("restoreVersion"))).fetchone()
    s.require(target, 404)
    _save(c, row, json.loads(target[0]), "運営が履歴 第" + str(data.get("restoreVersion")) + "版から復元")
    core.event(c, operator, "workspace_restored_operator", pid)
    c.commit()
    return operator_detail(c, operator, intake_id)


def detail(c, uid, pid):
    row = owner_row(c, uid, pid)
    applications = [dict(r) for r in c.execute("SELECT a.*,u.name FROM applications a JOIN users u ON u.id=a.fan WHERE a.project=? ORDER BY a.rowid", (pid,))]
    for a in applications:
        a["unread"] = unread(c, a["id"], uid)
    return {"workspace": True, "id": pid, "version": row["version"], "state": row["state"], "slug": row["slug"],
            "draft": json.loads(row["draft"]), "published": json.loads(row["published"]), "applications": applications,
            "access": {"source": "first_project", "collaboration": True}, "aiQuota": ai_quota(c, pid),
            "messages": [dict(r) for r in c.execute("SELECT role,body,created FROM svc_messages WHERE project=? ORDER BY rowid", (pid,))]}


def dashboard(c, uid):
    owned = [dict(r) for r in c.execute("SELECT v.project AS id,p.title,v.state,v.slug,v.version FROM svc_workspaces w JOIN svc_projects v ON v.project=w.project JOIN projects p ON p.id=w.project WHERE p.owner=? ORDER BY v.updated DESC", (uid,))]
    apps = [dict(r) for r in c.execute("SELECT a.id,a.project,a.task,a.status,a.version,a.experience,w.published,u.name AS creator FROM applications a JOIN svc_workspaces w ON w.project=a.project JOIN projects p ON p.id=a.project JOIN users u ON u.id=p.owner WHERE a.fan=? ORDER BY a.rowid DESC", (uid,))]
    for a in apps:
        snap = json.loads(a.pop("published"))
        offer = next((r for r in snap.get("roles", []) if r["id"] == a["task"]), {})
        a.update(title=snap.get("title", ""), taskTitle=offer.get("title", ""))
        a["unread"] = unread(c, a["id"], uid)
    return {"workspaceProjects": owned, "workspaceApplications": apps}


def edit(c, uid, pid, data):
    c.execute("BEGIN IMMEDIATE")
    row = _editable(c, uid, pid, data.get("version"))
    _save(c, row, data.get("draft"), "本人が編集")
    c.commit()
    return detail(c, uid, pid)


def _offer(t):
    return {"id": t["id"], **t["offer"], "capacity": t["capacity"], "closed": False}


def _snapshot(c, row):
    d = json.loads(row["draft"])
    fields = d["public"]
    s.require(all(fields.get(k) for k in PUBLIC_FIELDS), 400, "公開する企画概要・日程・参加条件・募集文を入力してください。")
    old = json.loads(row["published"])
    if c.execute("SELECT 1 FROM applications WHERE project=?", (row["project"],)).fetchone():
        s.require(all(fields.get(k) == old.get(k) for k in PUBLIC_FIELDS), 409,
                  "応募済みの企画案内・参加条件は固定されています。役割の条件を変える場合は旧枠の受付を停止し、新しい募集枠を作成してください。")
    previous = {r["id"]: r for r in old.get("roles", [])}
    roles = []
    for task in d["tasks"]:
        original = previous.get(task["id"])
        applied = c.execute("SELECT 1 FROM applications WHERE task=?", (task["id"],)).fetchone()
        if original and applied:
            if task["recruit"]:
                s.require(not original["closed"] and _offer(task) == original, 409,
                          "応募済みの募集条件は変更できません。受付を停止して新しい募集枠を作成してください。")
                roles.append(original)
            else:
                roles.append({**original, "closed": True})
            continue
        if task["recruit"]:
            s.require(task["termsConfirmed"] and all(task["offer"].get(k) for k in (*TASK_FIELDS, "terms")), 400,
                      "募集する役割の作業内容・時間・完了条件・参加費や謝礼の条件を確認してください。")
            roles.append(_offer(task))
    # Retain closed offers and their stable IDs even if there are applicants.
    s.require(roles and any(not t["closed"] for t in roles), 400, "募集する役割を1件以上選んでください。")
    return {**fields, "roles": roles}


def publish(c, uid, pid, data):
    c.execute("BEGIN IMMEDIATE")
    row = _editable(c, uid, pid, data.get("version"))
    s.require(data.get("consent") is True, 400, "公開前の確認にチェックを入れてください。")
    snap = _snapshot(c, row)
    c.execute("UPDATE svc_workspaces SET published=? WHERE project=?", (core.dumps(snap), pid))
    c.execute("UPDATE svc_projects SET state='published',version=version+1,updated=? WHERE project=?", (s.stamp(), pid))
    c.execute("UPDATE projects SET launched=1,started=COALESCE(started,?) WHERE id=?", (s.stamp(), pid))
    core.event(c, uid, "workspace_published", pid)
    c.commit()
    return detail(c, uid, pid)


def public(c, slug):
    require_enabled()
    row = c.execute("SELECT v.project,v.state,w.published,u.name FROM svc_projects v JOIN svc_workspaces w ON w.project=v.project JOIN projects p ON p.id=v.project JOIN users u ON u.id=p.owner WHERE v.slug=?", (slug,)).fetchone()
    s.require(row and row["state"] == "published", 404, "この募集は現在公開されていません。")
    snap = json.loads(row["published"])
    s.require(snap.get("roles"), 404)
    return {"workspace": True, "id": row["project"], "slug": slug, "creator": row["name"], "publicationRevision": publication_revision(snap),
            **{k: snap[k] for k in PUBLIC_FIELDS}, "roles": snap["roles"]}


def publication_revision(snapshot):
    return hashlib.sha256(core.dumps(snapshot).encode()).hexdigest()


def public_slug(c, slug):
    return bool(c.execute("SELECT 1 FROM svc_projects v JOIN svc_workspaces w ON w.project=v.project WHERE v.slug=?", (slug,)).fetchone())


def apply(c, uid, pid, data):
    require_enabled()
    c.execute("BEGIN IMMEDIATE")
    row = c.execute("SELECT p.owner,v.state,w.published FROM projects p JOIN svc_projects v ON v.project=p.id JOIN svc_workspaces w ON w.project=p.id WHERE p.id=?", (pid,)).fetchone()
    s.require(row and row["state"] == "published", 404, "募集が見つかりません。")
    s.require(uid != row["owner"] and data.get("consent") is True, 400, "応募する役割と参加条件を確認してください。")
    body = s.text(data.get("experience"), 2000)
    tid = data.get("task")
    prior = c.execute("SELECT id,experience FROM applications WHERE task=? AND fan=? AND project=?", (tid, uid, pid)).fetchone()
    if prior:
        s.require(prior["experience"] == body, 409, "この役割には応募済みです。メッセージでご相談ください。")
        c.commit()
        return {"id": prior["id"]}
    snap = json.loads(row["published"])
    s.require(data.get("publicationRevision") == publication_revision(snap), 409,
              "募集内容が更新されました。ページを再読込し、最新の条件を確認してから応募してください。")
    task = next((t for t in snap.get("roles", []) if t["id"] == tid), None)
    s.require(task and not task["closed"], 409, "この役割の応募受付は停止しています。")
    s.require(_assigned(c, tid) < task["capacity"], 409, "この役割の担当は決まりました。")
    s.require(c.execute("SELECT COUNT(*) FROM applications WHERE project=?", (pid,)).fetchone()[0] < 100, 429, "この企画の応募上限に達しました。")
    aid = s.ident()
    c.execute("INSERT INTO applications(id,project,task,fan,experience,status) VALUES(?,?,?,?,?,'応募')", (aid, pid, tid, uid, body))
    core.event(c, uid, "workspace_applied", pid)
    c.commit()
    return {"id": aid}


def _assigned(c, tid):
    return c.execute("SELECT COUNT(*) FROM applications WHERE task=? AND status IN ('担当','提出','確認済','差戻し')", (tid,)).fetchone()[0]


def application_row(c, uid, aid):
    require_enabled()
    row = c.execute("SELECT a.*,p.owner,u.name AS fan_name,v.state,w.published FROM applications a JOIN projects p ON p.id=a.project JOIN svc_projects v ON v.project=p.id JOIN svc_workspaces w ON w.project=p.id JOIN users u ON u.id=a.fan WHERE a.id=?", (aid,)).fetchone()
    s.require(row and uid in (row["owner"], row["fan"]), 404, "応募が見つかりません。")
    result = dict(row)
    result["fanName"] = result.pop("fan_name")
    snap = json.loads(row["published"])
    offer = next((t for t in snap.get("roles", []) if t["id"] == row["task"]), {})
    result.update(projectTitle=snap.get("title", ""), taskTitle=offer.get("title", ""))
    return result


def application_action(c, uid, aid, action, data):
    c.execute("BEGIN IMMEDIATE")
    row = application_row(c, uid, aid)
    s.require(data.get("version") == row["version"], 409, "応募が更新されています。最新の状態を確認してください。")
    if action == "cancel":
        s.require(uid == row["fan"], 403)
        c.execute("UPDATE applications SET status='取消',version=version+1,confirmation='',confirmed_at=NULL WHERE id=?", (aid,))
    else:
        s.require(row["state"] != "archived", 409, "完了した企画は閲覧のみです。")
        if action == "submit":
            s.require(uid == row["fan"] and row["status"] in ("担当", "差戻し"), 403)
            body = s.text(data.get("body"), 4000)
            s.require(c.execute("SELECT COUNT(*) FROM revisions WHERE application=?", (aid,)).fetchone()[0] < 20, 429)
            c.execute("INSERT INTO revisions(application,body,created) VALUES(?,?,?)", (aid, body, s.stamp()))
            c.execute("UPDATE applications SET status='提出',submission=?,version=version+1 WHERE id=?", (body, aid))
        else:
            s.require(uid == row["owner"] and action == "status", 403)
            target = data.get("status")
            allowed = {"応募": ("候補", "担当", "見送り"), "候補": ("担当", "見送り"), "提出": ("確認済", "差戻し")}
            s.require(target in allowed.get(row["status"], ()), 409, "現在の状態からは変更できません。")
            if target == "担当":
                role = next((t for t in json.loads(row["published"]).get("roles", []) if t["id"] == row["task"]), None)
                s.require(role and _assigned(c, row["task"]) < role["capacity"], 409, "募集人数に達しているため担当を追加できません。")
            comment = s.text(data.get("comment", ""), 1000, blank=target not in ("確認済", "差戻し"))
            c.execute("UPDATE applications SET status=?,feedback=?,confirmation=?,confirmed_at=?,version=version+1 WHERE id=?", (target, comment, comment if target == "確認済" else "", s.stamp() if target == "確認済" else None, aid))
    core.event(c, uid, "workspace_application_" + action, row["project"])
    c.commit()
    return {"ok": True}


def unread(c, aid, uid):
    read = c.execute("SELECT last_read FROM svc_workspace_reads WHERE application=? AND user_id=?", (aid, uid)).fetchone()
    return c.execute("SELECT COUNT(*) FROM svc_workspace_messages WHERE application=? AND sender!=? AND rowid>?", (aid, uid, read[0] if read else 0)).fetchone()[0]


def conversation(c, uid, aid, after=0):
    row = application_row(c, uid, aid)
    s.require(type(after) is int and after >= 0, 400)
    messages = [dict(r) for r in c.execute("SELECT id,sender,body,created,rowid AS cursor FROM svc_workspace_messages WHERE application=? AND rowid>? ORDER BY rowid LIMIT 50", (aid, after))]
    return {"id": aid, "project": row["project"], "projectTitle": row["projectTitle"], "taskTitle": row["taskTitle"],
            "name": row["fanName"], "owner": uid == row["owner"], "status": row["status"], "version": row["version"],
            "experience": row["experience"], "submission": row["submission"], "feedback": row["feedback"],
            "messages": [{**{k: v for k, v in m.items() if k != "sender"}, "mine": m["sender"] == uid} for m in messages],
            "next": messages[-1]["cursor"] if len(messages) == 50 else None,
            "canSend": row["state"] != "archived" and row["status"] not in ("取消", "見送り")}


def send_message(c, uid, aid, data):
    c.execute("BEGIN IMMEDIATE")
    row = application_row(c, uid, aid)
    body = s.text(data.get("body"), 2000)
    request_id = s.text(data.get("requestId"), 64)
    s.require(re.fullmatch(r"[a-zA-Z0-9_-]{20,64}", request_id), 400)
    prior = c.execute("SELECT id,body FROM svc_workspace_messages WHERE application=? AND sender=? AND request_id=?", (aid, uid, request_id)).fetchone()
    if prior:
        s.require(prior["body"] == body, 409)
        c.commit()
        return {"id": prior["id"]}
    s.require(row["state"] != "archived" and row["status"] not in ("取消", "見送り"), 409, "この応募はメッセージの受付を終了しています。")
    s.require(c.execute("SELECT COUNT(*) FROM svc_workspace_messages WHERE sender=? AND created>?", (uid, s.stamp() - 60)).fetchone()[0] < 20, 429, "送信が続いています。少し待ってお試しください。")
    mid = s.ident()
    c.execute("INSERT INTO svc_workspace_messages(id,application,sender,body,request_id,created) VALUES(?,?,?,?,?,?)", (mid, aid, uid, body, request_id, s.stamp()))
    c.commit()
    return {"id": mid}


def mark_read(c, uid, aid, data):
    c.execute("BEGIN IMMEDIATE")
    application_row(c, uid, aid)
    cursor = data.get("cursor")
    s.require(type(cursor) is int and cursor >= 0, 400)
    maximum = c.execute("SELECT MAX(rowid) FROM svc_workspace_messages WHERE application=?", (aid,)).fetchone()[0] or 0
    cursor = min(cursor, maximum)
    prior = c.execute("SELECT last_read FROM svc_workspace_reads WHERE application=? AND user_id=?", (aid, uid)).fetchone()
    if prior:
        c.execute("UPDATE svc_workspace_reads SET last_read=? WHERE application=? AND user_id=?", (max(prior[0], cursor), aid, uid))
    else:
        c.execute("INSERT INTO svc_workspace_reads(application,user_id,last_read) VALUES(?,?,?)", (aid, uid, cursor))
    c.commit()
    return {"ok": True}


def history(c, uid, pid):
    owner_row(c, uid, pid)
    return {"items": [dict(r) for r in c.execute("SELECT version,reason,created FROM svc_workspace_history WHERE project=? ORDER BY version DESC LIMIT 100", (pid,))]}


def restore(c, uid, pid, data):
    c.execute("BEGIN IMMEDIATE")
    row = _editable(c, uid, pid, data.get("version"))
    target = c.execute("SELECT draft FROM svc_workspace_history WHERE project=? AND version=?", (pid, data.get("restoreVersion"))).fetchone()
    s.require(target, 404)
    _save(c, row, json.loads(target[0]), "履歴 第" + str(data.get("restoreVersion")) + "版から復元")
    c.commit()
    return detail(c, uid, pid)


def close_role(c, uid, pid, data):
    c.execute("BEGIN IMMEDIATE")
    row = _editable(c, uid, pid, data.get("version"))
    draft, snap = json.loads(row["draft"]), json.loads(row["published"])
    tid = data.get("taskId")
    target = next((t for t in draft["tasks"] if t["id"] == tid), None)
    s.require(target, 404)
    target["recruit"] = False
    for role in snap.get("roles", []):
        if role["id"] == tid:
            role["closed"] = True
    _save(c, row, draft, "役割の応募受付を停止")
    c.execute("UPDATE svc_workspaces SET published=? WHERE project=?", (core.dumps(snap), pid))
    c.commit()
    return detail(c, uid, pid)


def _ai_edit(draft, output):
    s.require(isinstance(output, dict) and set(output) == set(workspace_ai.SCHEMA["properties"]), 502)
    s.text(output["reply"], 4000)
    for key in ("changes", "newTasks", "removeTaskIds", "artifacts"):
        s.require(isinstance(output[key], list) and len(output[key]) <= 50, 502)
    tasks = {t["id"]: t for t in draft["tasks"]}
    for change in output["changes"]:
        target, key, tid = change.get("target"), change.get("field"), change.get("id")
        if target in ("project", "public"):
            limits = PROJECT_FIELDS if target == "project" else PUBLIC_FIELDS
            s.require(key in limits, 502)
            (draft if target == "project" else draft["public"])[key] = s.text(change.get("value"), limits[key], blank=True)
        else:
            s.require(target == "task" and tid in tasks and key in TASK_FIELDS, 502)
            tasks[tid][key] = s.text(change.get("value"), TASK_FIELDS[key], blank=True)
    for value in output["newTasks"]:
        task = text_fields(value, TASK_FIELDS, required=True)
        draft["tasks"].append({**task, "id": s.ident(), "status": "未着手", "recruit": False, "capacity": 1, "offer": {**task, "terms": ""}, "termsConfirmed": False})
    s.require(all(tid in tasks for tid in output["removeTaskIds"]), 502)
    draft["tasks"] = [t for t in draft["tasks"] if t["id"] not in output["removeTaskIds"]]
    arts = {a["id"]: a for a in draft["artifacts"]}
    for value in output["artifacts"]:
        aid = value.get("id")
        s.require(not aid or aid in arts, 502)
        artifact = {**value, "id": aid or s.ident()}
        if aid:
            arts[aid].update(artifact)
        else:
            draft["artifacts"].append(artifact)
    return validate(draft)


def assist(c, uid, pid, data):
    require_enabled()
    request_id = s.text(data.get("requestId"), 64)
    s.require(re.fullmatch(r"[a-zA-Z0-9_-]{20,64}", request_id), 400)
    prompt = s.text(data.get("prompt"), 1500)
    fingerprint = hashlib.sha256(core.dumps({"project": pid, "user": uid, "prompt": prompt, "version": data.get("version"), "applicationId": data.get("applicationId", "")}).encode()).hexdigest()
    c.execute("BEGIN IMMEDIATE")
    owner_row(c, uid, pid)
    previous = c.execute("SELECT * FROM svc_ai_requests WHERE id=?", (request_id,)).fetchone()
    if previous:
        s.require(previous["user_id"] == uid and previous["project"] == pid and previous["fingerprint"] == fingerprint, 409)
        s.require(previous["status"] == "completed", 409, "このAI依頼は処理済み、または結果を確認中です。再送せず保存状況をご確認ください。")
        c.commit()
        return json.loads(previous["result"])
    row = _editable(c, uid, pid, data.get("version"))
    s.require(workspace_ai.interview.ready(), 503, "AIの設定を確認しています。手編集は引き続き使えます。")
    s.require(ai_quota(c, pid)["used"] < LIMIT, 429, "この企画のAI利用30回に達しました。手編集と共同作業は引き続き使えます。")
    s.require(not c.execute("SELECT 1 FROM svc_ai_requests WHERE project=? AND status IN ('running','reserved','unknown')", (pid,)).fetchone(), 409, "前のAI依頼を確認しています。再送せずお待ちください。")
    b = core.budget(c)
    s.require(b["spent_yen"] + b["reserved_yen"] + RESERVE <= MONTH_LIMIT, 429, "今月のAI費用上限に達しました。手編集と共同作業は使えます。")
    draft = json.loads(row["draft"])
    context = {"project": draft, "request": prompt, "history": [dict(r) for r in c.execute("SELECT role,body FROM svc_messages WHERE project=? ORDER BY rowid DESC LIMIT 4", (pid,))][::-1]}
    if data.get("applicationId"):
        thread = application_row(c, uid, data["applicationId"])
        s.require(thread["project"] == pid and thread["owner"] == uid, 404)
        context["selectedConversation"] = {"role": thread["taskTitle"], "application": thread["experience"],
                                           "messages": [dict(r) for r in c.execute("SELECT body FROM svc_workspace_messages WHERE application=? ORDER BY rowid DESC LIMIT 10", (thread["id"],))][::-1]}
    c.execute("INSERT INTO svc_ai_requests(id,user_id,project,fingerprint,mode,period,status,reserved_yen,search_used,created) VALUES(?,?,?,?,'workspace',?,'running',?,0,?)", (request_id, uid, pid, fingerprint, b["month"], RESERVE, s.stamp()))
    c.execute("UPDATE svc_budgets SET reserved_yen=reserved_yen+? WHERE month=?", (RESERVE, b["month"]))
    c.commit()
    cost = None
    try:
        output, cost = workspace_ai.generate(context)
        s.require(type(cost) is int and 0 <= cost <= RESERVE, 502, "AI利用量を確認しています。")
        draft = _ai_edit(copy.deepcopy(draft), output)
        c.execute("BEGIN IMMEDIATE")
        latest = _editable(c, uid, pid, row["version"])
        _save(c, latest, draft, "AIによる修正・資料作成")
        for role, body in (("user", prompt), ("assistant", output["reply"])):
            c.execute("INSERT INTO svc_messages(id,project,role,body,created) VALUES(?,?,?,?,?)", (s.ident(), pid, role, body, s.stamp()))
        result = {"reply": output["reply"], "applied": True}
        c.execute("UPDATE svc_ai_requests SET status='completed',result=?,cost_yen=? WHERE id=?", (core.dumps(result), cost, request_id))
        c.execute("UPDATE svc_budgets SET reserved_yen=reserved_yen-?,spent_yen=spent_yen+? WHERE month=?", (RESERVE, cost, b["month"]))
        c.commit()
        return result
    except Exception as error:
        c.rollback()
        known_cost = type(cost) is int and 0 <= cost <= RESERVE
        uncertain = not known_cost and (not isinstance(error, providers.ProviderError) or error.uncertain)
        c.execute("BEGIN IMMEDIATE")
        status = "completed" if known_cost else "unknown" if uncertain else "failed"
        c.execute("UPDATE svc_ai_requests SET status=?,cost_yen=?,result=?,error=? WHERE id=?", (status, cost if known_cost else None, core.dumps({"reply": "変更を保存できませんでした。最新の内容を確認してください。", "applied": False}), "AI結果または保存状況の確認が必要", request_id))
        if known_cost or not uncertain:
            c.execute("UPDATE svc_budgets SET reserved_yen=reserved_yen-?,spent_yen=spent_yen+? WHERE month=?", (RESERVE, cost if known_cost else 0, b["month"]))
        c.commit()
        raise
