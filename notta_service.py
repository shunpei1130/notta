"""Public service dispatcher; legacy integration endpoints stay private."""
import hashlib
import hmac
import json
import os
import re

import server as s
import service_core as core
import service_stripe as billing
import service_providers as providers
import service_intake as intake
from urllib.parse import parse_qs, urlparse


def enabled():
    return os.getenv("NOTTA_SERVICE_ENABLED") == "yes"


def public_sandbox():
    return os.getenv("NOTTA_PUBLIC_SANDBOX") == "yes"


def configuration():
    return {"mailReady": providers.mail_ready(), "aiReady": providers.interview_ready() if intake.enabled() else providers.ai_ready(),
            "searchReady": providers.search_ready(), "billingReady": billing.ready(),
            "terms": "2026-10-intake-v1" if intake.enabled() else core.TERMS, "billingEnvironment": os.getenv("STRIPE_ENVIRONMENT", "sandbox"),
            "billingProvider": "stripe", "publicSandbox": public_sandbox(),
            "phone": os.getenv("NOTTA_SELLER_PHONE", "090-9325-5945"), "price": 3000,
            "intakeMode": intake.enabled()}


def dashboard(c, user):
    uid = user["id"]
    projects = [dict(r) for r in c.execute("SELECT p.id,p.title,v.state,v.slug,v.updated,v.version FROM projects p JOIN svc_projects v ON v.project=p.id WHERE p.owner=? ORDER BY v.updated DESC", (uid,))]
    # Fans see only their own application and its selected role; never other applicants.
    applications = [dict(r) for r in c.execute("SELECT a.id,a.project,a.status,a.version,a.experience,a.submission,a.feedback,a.confirmation,a.confirmed_at,t.title AS role,p.title,u.name AS creator,v.state FROM applications a JOIN tasks t ON t.id=a.task JOIN projects p ON p.id=a.project JOIN users u ON u.id=p.owner JOIN svc_projects v ON v.project=p.id WHERE a.fan=? ORDER BY a.rowid DESC", (uid,))]
    result = {"user": {key: user[key] for key in ("id", "name", "email")}, "csrf": user["csrf"], "projects": projects, "applications": applications, "billing": billing.summary(c, uid), "quota": core.quota(c, uid)}
    if intake.enabled():
        result.update(intake=intake.summary(c, uid), operator=intake.is_operator(c, uid))
        result["legacyProjects"] = [dict(r) for r in c.execute("SELECT p.id,p.title,p.goal,p.created FROM projects p WHERE p.owner=? AND NOT EXISTS(SELECT 1 FROM svc_projects v WHERE v.project=p.id) ORDER BY p.rowid DESC", (uid,))]
    return result


def dispatch(handler, method, path):
    try:
        with s.connect() as c:
            s.require(path != "/api/service/square/webhook", 404, "ページがありません。")
            if path == "/api/service/stripe/webhook":
                s.require(method == "POST", 405)
                try:
                    size = int(handler.headers.get("Content-Length", "0"))
                except ValueError:
                    raise s.Problem(400, "通知サイズが不正です。")
                s.require(0 < size <= 262144, 413)
                result = billing.webhook(c, handler.rfile.read(size), handler.headers.get("Stripe-Signature"))
                handler.respond(200, result)
                return
            if method == "GET" and path == "/api/service/config":
                handler.respond(200, configuration())
                return
            public = re.fullmatch(r"/api/service/public/([A-Za-z0-9_-]{16})", path)
            if method == "GET" and public:
                handler.respond(200, core.public_project(c, public[1]))
                return
            data = handler.input() if method == "POST" else None
            # Block browser cross-origin posts even if a future proxy changes CORS.
            if method == "POST":
                s.require(handler.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none"), 403, "別のサイトからは操作できません。")
            if method == "POST" and path == "/api/service/auth/start":
                handler.respond(200, core.auth_start(c, data, handler.client_address[0]))
                return
            if method == "POST" and path == "/api/service/auth/verify":
                result, token = core.auth_verify(c, data)
                handler.respond(200, result, "notta_session=" + token + "; HttpOnly; SameSite=Lax; Path=/; Max-Age=28800")
                return
            user = handler.session(c)
            s.require(c.execute("SELECT 1 FROM svc_accounts WHERE user_id=?", (user["id"],)).fetchone(), 401, "メール認証でログインしてください。")
            if method == "GET":
                if intake.enabled():
                    item = re.fullmatch(r"/api/service/intakes/([a-f0-9]{24})", path)
                    admin_item = re.fullmatch(r"/api/service/admin/intakes/([a-f0-9]{24})", path)
                    if item or admin_item:
                        handler.respond(200, intake.detail(c, user["id"], (item or admin_item)[1], operator=bool(admin_item)))
                        return
                    if path == "/api/service/admin/intakes":
                        before = parse_qs(urlparse(handler.path).query).get("before", [None])[0]
                        handler.respond(200, intake.admin_list(c, user["id"], before))
                        return
                grounding = re.fullmatch(r"/api/service/grounding/([a-f0-9-]{24,64})", path)
                if grounding:
                    row = c.execute("SELECT result FROM svc_ai_requests WHERE id=? AND user_id=? AND status='completed'", (grounding[1], user["id"])).fetchone()
                    s.require(row, 404)
                    content = json.loads(row[0]).get("searchEntryPoint", "")
                    s.require(content, 404)
                    raw = ('<!doctype html><meta charset="utf-8"><base target="_blank">' + content).encode()
                    handler.send_response(200)
                    handler.send_header('Content-Type', 'text/html; charset=utf-8')
                    handler.send_header('Cache-Control', 'no-store')
                    handler.send_header('Referrer-Policy', 'no-referrer')
                    handler.send_header('X-Content-Type-Options', 'nosniff')
                    handler.send_header('X-Frame-Options', 'SAMEORIGIN')
                    if public_sandbox(): handler.send_header('X-Robots-Tag', 'noindex, nofollow')
                    handler.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; img-src https: data:; sandbox allow-popups allow-popups-to-escape-sandbox; frame-ancestors 'self'; base-uri 'none'; form-action 'none'")
                    handler.send_header('Content-Length', str(len(raw)))
                    handler.end_headers(); handler.wfile.write(raw)
                    return
                if path == "/api/service/me":
                    handler.respond(200, dashboard(c, user))
                    return
                project = re.fullmatch(r"/api/service/projects/([a-f0-9]{24})", path)
                if project:
                    handler.respond(200, core.project_detail(c, user["id"], project[1]))
                    return
                raise s.Problem(404, "ページがありません。")
            s.require(hmac.compare_digest(handler.headers.get("X-CSRF-Token", ""), user["csrf"]), 403, "操作確認が切れました。再ログインしてください。")
            uid = user["id"]
            if intake.enabled():
                answer = re.fullmatch(r"/api/service/intakes/([a-f0-9]{24})/answers", path)
                operation = re.fullmatch(r"/api/service/admin/intakes/([a-f0-9]{24})/(result|complete|retry)", path)
                if path == "/api/service/intakes":
                    handler.respond(200, intake.start(c, uid, data))
                    return
                if answer:
                    handler.respond(200, intake.answer(c, uid, answer[1], data))
                    return
                if operation:
                    functions = {"result": intake.save_result, "complete": intake.complete}
                    result = (intake.retry_notification(c, uid, operation[1], data.get("notificationId"))
                              if operation[2] == "retry" else functions[operation[2]](c, uid, operation[1], data))
                    handler.respond(200, result)
                    return
                s.require(path not in ("/api/service/projects", "/api/service/billing/checkout") and not
                          re.fullmatch(r"/api/service/projects/[a-f0-9]{24}/assist", path), 409,
                          "現在はヒアリングとタスク分解の受付を行っています。新規の月額申込みは受け付けていません。")
            if path == "/api/service/logout":
                c.execute("DELETE FROM sessions WHERE user_id=? AND csrf=?", (uid, user["csrf"]))
                c.commit()
                handler.respond(200, {"ok": True}, "notta_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0")
                return
            if path == "/api/service/projects":
                result = core.new_project(c, uid, data)
            elif path == "/api/service/billing/checkout":
                result = billing.checkout(c, user, data)
            elif path == "/api/service/billing/sync":
                # No automatic retry loop. Double clicks cannot create a new charge.
                result = billing.reconcile(c, uid)
            elif path == "/api/service/billing/cancel":
                result = billing.cancel(c, uid, data)
            elif path == "/api/service/billing/refund":
                result = billing.request_refund(c, uid, data)
            else:
                project = re.fullmatch(r"/api/service/projects/([a-f0-9]{24})/(edit|assist|publish|archive|apply)", path)
                application = re.fullmatch(r"/api/service/applications/([a-f0-9]{24})/(status|submit|cancel)", path)
                if project:
                    functions = {"edit": core.edit_project, "assist": core.assist, "publish": core.publish, "archive": core.archive, "apply": core.apply}
                    result = functions[project[2]](c, uid, project[1], data)
                elif application:
                    result = core.application_action(c, uid, application[1], application[2], data)
                else:
                    raise s.Problem(404, "操作が見つかりません。")
            handler.respond(200, result)
    except providers.ProviderError as error:
        handler.respond(503, {"error": str(error), "uncertain": error.uncertain})
