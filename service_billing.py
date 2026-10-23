"""Square reconciliation. Redirects and webhook payloads never grant entitlements."""
import calendar
import datetime as dt
import hashlib
import json
import re
from urllib.parse import urlparse

import server as s
import service_core as core
import service_providers as providers


def date_value(value):
    return dt.date.fromisoformat(value)


def timestamp(value):
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def period_end(invoice, subscription):
    requests = invoice.get("payment_requests", [])
    s.require(len(requests) == 1 and requests[0].get("request_type") == "BALANCE", 503, "請求期間の確認が必要です。")
    start = date_value(requests[0]["due_date"])
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    anchor = subscription.get("monthly_billing_anchor_date", start.day)
    end = dt.date(year, month, min(anchor, calendar.monthrange(year, month)[1]))
    s.require(subscription.get("timezone") == "Asia/Tokyo", 503, "契約のタイムゾーンを確認しています。")
    return int(dt.datetime.combine(end, dt.time(), core.JST).timestamp())


def checkout(c, user, data):
    s.require(providers.Square.ready(), 503, "月額プランは準備中です。現在は請求されません。")
    s.require(data.get("consent") is True and data.get("terms") == core.TERMS, 400, "料金・自動更新・解約・返金条件をご確認ください。")
    uid = user["id"]
    c.execute("BEGIN IMMEDIATE")
    s.require(not core.paid(c, uid), 409, "すでに月額プランを利用中です。")
    # One unresolved link per account. Reuse it instead of creating duplicate contracts.
    old = c.execute("SELECT * FROM svc_checkouts WHERE user_id=? AND status IN ('reserved','pending','unknown') ORDER BY created DESC LIMIT 1", (uid,)).fetchone()
    if old:
        s.require(old["status"] == "pending" and old["url"], 409, "前の決済を確認中です。契約画面で状態を確認してください。")
        c.commit()
        return {"url": old["url"], "id": old["id"]}
    old_sub = core.subscription(c, uid)
    s.require(not old_sub or old_sub["status"] in ("CANCELED", "DEACTIVATED") and old_sub["paid_until"] <= s.stamp(), 409, "既存の契約を確認してください。新しい契約は作成していません。")
    count = c.execute("SELECT COUNT(DISTINCT user_id) FROM svc_subscriptions WHERE status IN ('ACTIVE','PENDING') OR paid_until>?", (s.stamp(),)).fetchone()[0]
    pending = c.execute("SELECT COUNT(*) FROM svc_checkouts WHERE status IN ('reserved','pending','unknown')").fetchone()[0]
    b = core.budget(c)
    required = core.commitments(c, exclude_uid=uid, new_paid_uid=uid, reserve_paid_full=True)
    s.require(count + pending < 5 and b["spent_yen"] + b["reserved_yen"] + required <= core.MONTH_LIMIT, 429, "現在、月額プランの新規受付を調整しています。保存した企画はそのまま使えます。")
    key = s.ident()
    c.execute("INSERT INTO svc_checkouts(id,user_id,status,created,month,terms) VALUES(?,?,?,?,?,?)", (key, uid, "reserved", s.stamp(), core.month(), core.TERMS))
    core.event(c, uid, "checkout_started")
    c.commit()
    try:
        link = providers.Square().checkout(key, user["email"])
        url = link["url"]
        parsed = urlparse(url)
        s.require(parsed.scheme == "https" and parsed.hostname in ("square.link", "sandbox.square.link", "checkout.square.site", "connect.squareupsandbox.com"), 502, "決済ページを確認できませんでした。")
        c.execute("BEGIN IMMEDIATE")
        c.execute("UPDATE svc_checkouts SET status='pending',link_id=?,order_id=?,url=? WHERE id=?", (link["id"], link["order_id"], url, key))
        c.commit()
        return {"url": url, "id": key}
    except Exception as error:
        c.rollback()
        uncertain = not isinstance(error, providers.ProviderError) or error.uncertain
        c.execute("UPDATE svc_checkouts SET status=? WHERE id=?", ("unknown" if uncertain else "failed", key))
        c.commit()
        raise


def customer_from_order(order):
    customers = {order["customer_id"]} if order.get("customer_id") else set()
    for fulfillment in order.get("fulfillments", []):
        for key in ("pickup_details", "shipment_details", "delivery_details"):
            customer = fulfillment.get(key, {}).get("recipient", {}).get("customer_id")
            if customer:
                customers.add(customer)
    s.require(len(customers) <= 1, 503, "契約者の照合を確認しています。")
    return next(iter(customers), None)


def link_subscription(c, checkout_row, square):
    order = square.order(checkout_row["order_id"])
    s.require(order.get("id") == checkout_row["order_id"] and order.get("location_id") == square.location, 503, "決済の店舗を照合できませんでした。")
    customer = customer_from_order(order)
    if not customer:
        return None
    matches = []
    for sub in square.subscriptions(customer):
        templates = {phase.get("order_template_id") for phase in sub.get("phases", [])}
        if sub.get("customer_id") == customer and sub.get("plan_variation_id") == square.plan and sub.get("location_id") == square.location and checkout_row["order_id"] in templates:
            matches.append(sub)
    s.require(len(matches) <= 1, 503, "複数の契約が見つかりました。追加の決済をせずお問い合わせください。")
    if not matches:
        return None
    sub = matches[0]
    c.execute("BEGIN IMMEDIATE")
    old = c.execute("SELECT user_id FROM svc_subscriptions WHERE id=?", (sub["id"],)).fetchone()
    s.require(not old or old[0] == checkout_row["user_id"], 409, "この契約は別のアカウントで確認済みです。")
    c.execute("INSERT OR IGNORE INTO svc_subscriptions(id,user_id,customer_id,status,checked_at) VALUES(?,?,?,?,?)", (sub["id"], checkout_row["user_id"], customer, "PENDING", s.stamp()))
    c.execute("UPDATE svc_checkouts SET status='linked' WHERE id=?", (checkout_row["id"],))
    c.commit()
    return sub["id"]


def sync_subscription(c, subscription_id, square=None):
    square = square or providers.Square()
    local = c.execute("SELECT * FROM svc_subscriptions WHERE id=?", (subscription_id,)).fetchone()
    if not local:
        return False
    current = square.subscription(subscription_id)
    s.require(current.get("id") == subscription_id and current.get("customer_id") == local["customer_id"] and current.get("plan_variation_id") == square.plan and current.get("location_id") == square.location, 503, "契約の照合ができませんでした。")
    records = []
    # Always re-read all invoices on this small pilot. Do not trust webhook order or amounts.
    ids = current.get("invoice_ids", [])
    s.require(len(ids) <= 120, 503, "契約の追加照合が必要です。")
    for iid in ids:
        invoice = square.invoice(iid)
        s.require(invoice.get("id") == iid and invoice.get("subscription_id") == subscription_id and invoice.get("location_id") == square.location, 503, "請求の照合ができませんでした。")
        if invoice.get("status") not in ("PAID", "REFUNDED", "PARTIALLY_REFUNDED"):
            continue
        order = square.order(invoice["order_id"])
        s.require(order.get("location_id") == square.location and order.get("total_money") == {"amount": 3000, "currency": "JPY"}, 503, "請求額を確認しています。")
        tenders = order.get("tenders", [])
        s.require(len(tenders) == 1 and tenders[0].get("payment_id"), 503, "支払情報の確認が必要です。")
        payment = square.payment(tenders[0]["payment_id"])
        s.require(payment.get("order_id") == invoice["order_id"] and payment.get("location_id") == square.location, 503, "支払先を照合できませんでした。")
        if payment.get("status") != "COMPLETED":
            continue
        s.require(payment.get("amount_money") == {"amount": 3000, "currency": "JPY"}, 503, "支払額が月額プランと一致しません。")
        refunds = []
        for rid in payment.get("refund_ids", []):
            refund = square.refund(rid)
            s.require(refund.get("payment_id") == payment["id"] and refund.get("amount_money", {}).get("currency") == "JPY", 503, "返金情報の確認が必要です。")
            refunds.append(refund)
        refunded = payment.get("refunded_money", {}).get("amount", 0)
        # Pending refunds suspend paid writes too; a late paid event cannot revive them.
        pending_refund = any(r.get("status") in ("PENDING", "COMPLETED") for r in refunds)
        records.append({"id": payment["id"], "invoice": iid, "completed": timestamp(payment["created_at"]), "end": period_end(invoice, current), "refunded": int(refunded > 0 or pending_refund), "refunds": refunds})
    c.execute("BEGIN IMMEDIATE")
    current_local = c.execute("SELECT provider_version FROM svc_subscriptions WHERE id=?", (subscription_id,)).fetchone()
    if current_local[0] > current.get("version", 0):
        c.commit()
        return True
    for record in records:
        old = c.execute("SELECT user_id,subscription_id FROM svc_payments WHERE id=?", (record["id"],)).fetchone()
        s.require(not old or (old[0] == local["user_id"] and old[1] == subscription_id), 409)
        c.execute("INSERT OR IGNORE INTO svc_payments(id,subscription_id,user_id,invoice_id,amount,currency,completed,period_end,refunded) VALUES(?,?,?,?,?,?,?,?,?)", (record["id"], subscription_id, local["user_id"], record["invoice"], 3000, "JPY", record["completed"], record["end"], record["refunded"]))
        if record["refunded"]:
            c.execute("UPDATE svc_payments SET refunded=1 WHERE id=?", (record["id"],))
        for refund in record["refunds"]:
            c.execute("INSERT OR IGNORE INTO svc_refunds(id,payment_id,amount,status,updated) VALUES(?,?,?,?,?)", (refund["id"], record["id"], refund["amount_money"]["amount"], refund["status"], s.stamp()))
            c.execute("UPDATE svc_refunds SET status=?,updated=? WHERE id=?", (refund["status"], s.stamp(), refund["id"]))
        total_refunded = c.execute("SELECT COALESCE(SUM(amount),0) FROM svc_refunds WHERE payment_id=? AND status='COMPLETED'", (record['id'],)).fetchone()[0]
        if total_refunded >= 3000:
            c.execute("UPDATE svc_refund_requests SET status='completed' WHERE payment_id=?", (record['id'],))
    payments = [dict(r) for r in c.execute("SELECT * FROM svc_payments WHERE subscription_id=? ORDER BY period_end DESC", (subscription_id,))]
    valid = [p for p in payments if not p["refunded"]]
    latest = valid[0] if valid else None
    first = min((p["completed"] for p in payments), default=0)
    cancel = current.get("canceled_date")
    cancel_at = int(dt.datetime.combine(date_value(cancel), dt.time(), core.JST).timestamp()) if cancel else None
    status = current.get("status", "PENDING")
    c.execute("UPDATE svc_subscriptions SET status=?,paid_until=?,cancel_at=?,first_paid=?,refunded=?,checked_at=?,period=?,provider_version=? WHERE id=?", (status, latest["period_end"] if latest else 0, cancel_at, first, int(bool(payments) and not valid), s.stamp(), latest["invoice_id"] if latest else "", current.get("version", 0), subscription_id))
    if first and not local["first_paid"]:
        core.event(c, local["user_id"], "payment_confirmed")
    c.commit()
    return True


def reconcile(c, uid):
    s.require(providers.Square.ready(), 503, "決済の接続を準備しています。現在は請求されません。")
    square = providers.Square()
    checkouts = c.execute("SELECT * FROM svc_checkouts WHERE user_id=? AND status='pending'", (uid,)).fetchall()
    for row in checkouts:
        link_subscription(c, row, square)
    rows = c.execute("SELECT id FROM svc_subscriptions WHERE user_id=?", (uid,)).fetchall()
    for row in rows:
        sync_subscription(c, row[0], square)
    return summary(c, uid)


def summary(c, uid):
    sub = core.subscription(c, uid)
    first = c.execute("SELECT completed,refunded FROM svc_payments WHERE user_id=? ORDER BY completed,id LIMIT 1", (uid,)).fetchone()
    request = c.execute("SELECT requested,status FROM svc_refund_requests WHERE user_id=? ORDER BY requested DESC LIMIT 1", (uid,)).fetchone()
    pending = c.execute("SELECT status,url FROM svc_checkouts WHERE user_id=? AND status IN ('reserved','pending','unknown') ORDER BY created DESC LIMIT 1", (uid,)).fetchone()
    return {"paid": core.paid(c, uid), "subscription": {key: sub[key] for key in ("status", "paid_until", "cancel_at", "first_paid", "refunded")} if sub else None,
            "refundEligible": bool(first and not first["refunded"] and s.stamp() <= first["completed"] + 7 * 86400),
            "refundRequest": dict(request) if request else None, "pending": pending[0] if pending else None,
            "pendingUrl": pending['url'] if pending and pending['status'] == 'pending' else None}


def cancel(c, uid, data):
    s.require(data.get("confirm") is True, 400, "次回の更新を停止することをご確認ください。")
    sub = core.subscription(c, uid)
    s.require(sub and sub["status"] in ("ACTIVE", "CANCELED"), 409, "更新を停止できる契約がありません。")
    current = providers.Square().subscription(sub["id"])
    if not current.get("canceled_date") and current.get("status") != "CANCELED":
        providers.Square().cancel(sub["id"])
    sync_subscription(c, sub["id"])
    return summary(c, uid)


def request_refund(c, uid, data):
    s.require(data.get("confirm") is True, 400, "初回の返金申出と更新停止をご確認ください。")
    c.execute("BEGIN IMMEDIATE")
    sub = core.subscription(c, uid)
    payment = c.execute("SELECT id,completed,refunded FROM svc_payments WHERE user_id=? ORDER BY completed,id LIMIT 1", (uid,)).fetchone()
    s.require(sub and payment and not payment['refunded'] and s.stamp() <= payment['completed'] + 7 * 86400, 409, "初回7日以内の返金対象の契約がありません。お問い合わせ先へご連絡ください。")
    c.execute("INSERT OR IGNORE INTO svc_refund_requests(id,user_id,payment_id,requested,status) VALUES(?,?,?,?,?)", (s.ident(), uid, payment[0], s.stamp(), "received"))
    core.event(c, uid, "refund_requested")
    c.commit()  # record timely notice even if Square is unavailable
    try:
        cancel(c, uid, {"confirm": True})
    except (providers.ProviderError, s.Problem):
        return {"ok": True, "message": "返金のお申出を受け付けました。MAORAが返金と更新停止を確認し、メールでご連絡します。", "needsReview": True}
    return {"ok": True, "message": "返金のお申出と更新停止を受け付けました。MAORAが返金処理を確認し、メールでご連絡します。"}


def webhook(c, raw, signature):
    s.require(providers.verify_webhook(raw, signature), 403, "通知の署名が一致しません。")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise s.Problem(400, "通知形式が不正です。")
    square = providers.Square()
    s.require(data.get("merchant_id") == square.merchant, 403, "通知の店舗が一致しません。")
    key = s.text(data.get("event_id"), 100)
    kind = s.text(data.get("type"), 100)
    digest = hashlib.sha256(raw).hexdigest()
    c.execute("BEGIN IMMEDIATE")
    old = c.execute("SELECT * FROM svc_webhooks WHERE id=?", (key,)).fetchone()
    s.require(not old or old["digest"] == digest, 409, "通知IDが重複しています。")
    if old and old["status"] == "completed":
        c.commit()
        return {"ok": True, "duplicate": True}
    c.execute("INSERT OR IGNORE INTO svc_webhooks(id,event_type,digest,status,created,updated) VALUES(?,?,?,?,?,?)", (key, kind, digest, "received", s.stamp(), s.stamp()))
    c.commit()
    # Pilot has at most five paying accounts. Reconciliation also discovers events
    # that arrived before the checkout redirect. No payload customer/email mapping.
    if kind.startswith(("subscription.", "invoice.", "payment.", "refund.", "order.")):
        users = {r[0] for r in c.execute("SELECT user_id FROM svc_checkouts WHERE status='pending'")}
        users.update(r[0] for r in c.execute("SELECT user_id FROM svc_subscriptions"))
        for uid in users:
            reconcile(c, uid)
    c.execute("UPDATE svc_webhooks SET status='completed',updated=? WHERE id=?", (s.stamp(), key))
    c.commit()
    return {"ok": True}
