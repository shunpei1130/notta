"""Stripe-hosted monthly Checkout. Only verified settled invoices grant access.

The generic service tables are shared with the previous provider; Stripe IDs
are selected explicitly, and no customer is matched by email. SDK imports are
lazy so billing disabled deployments and local workflow tests need no key.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import string
from urllib.parse import urlparse

import server as s
import service_core as core
import service_providers as providers

API_VERSION = "2026-08-26.dahlia"
APP = "notta-monthly-v1"
ERROR = "決済サービスの処理結果を確認できませんでした。追加で支払わず、契約画面で状態を確認してください。"


def ready():
    environment = os.getenv("STRIPE_ENVIRONMENT", "sandbox")
    key = os.getenv("STRIPE_RESTRICTED_KEY", "")
    prefix = "rk_live_" if environment == "production" else "rk_test_"
    return bool(environment in ("sandbox", "production") and key.startswith(prefix)
                and os.getenv("STRIPE_PRICE_ID", "").startswith("price_")
                and os.getenv("STRIPE_ACCOUNT_ID", "").startswith("acct_")
                and os.getenv("STRIPE_WEBHOOK_SECRET", "").startswith("whsec_")
                and os.getenv("NOTTA_BILLING_ENABLED") == "yes"
                and (environment != "production" or os.getenv("NOTTA_SELLER_PHONE")
                     and os.getenv("NOTTA_LEGAL_VERIFIED") == "yes"))


def _id(value):
    return value.get("id") if isinstance(value, dict) else value


def _dict(value):
    # Stripe 15.6 resources are no longer dict subclasses. Public to_dict()
    # recursively converts nested prices, metadata, lists and object references.
    return value.to_dict() if hasattr(value, "to_dict") else value


def _url(value):
    parsed = urlparse(value or "")
    s.require(parsed.scheme == "https" and parsed.hostname == "checkout.stripe.com"
              and not parsed.username and not parsed.password, 502, "決済ページを確認できませんでした。")
    return value


def _jpy_presentment(value, *, amount=False):
    presentment = value.get("presentment_details")
    return not presentment or (presentment.get("presentment_currency") == "jpy"
                              and (not amount or presentment.get("presentment_amount") == 3000))


def _no_tax_or_discount(value):
    return ((value.get("automatic_tax") or {}).get("enabled") is False
            and not value.get("default_tax_rates") and not value.get("discounts"))


def _validate_session(session):
    # A Price can remain unspecified when tax calculation is off. Enforce the
    # customer's actual JPY total instead of enabling Stripe Tax to set a label.
    details = session.get("total_details") or {}
    s.require(session.get("mode") == "subscription" and session.get("currency") == "jpy"
              and session.get("amount_subtotal") == 3000 and session.get("amount_total") == 3000
              and (session.get("automatic_tax") or {}).get("enabled") is False
              and session.get("allow_promotion_codes") is False and not session.get("discounts")
              and (session.get("adaptive_pricing") or {}).get("enabled") is False
              and details.get("amount_tax") == 0 and details.get("amount_discount") == 0
              and details.get("amount_shipping") in (0, None) and not session.get("shipping_cost")
              and not session.get("currency_conversion") and _jpy_presentment(session, amount=True),
              503, "税込総額3,000円の申込条件を照合できませんでした。追加で支払わずお問い合わせください。")


class Stripe:
    """Small synchronous SDK boundary; exceptions contain no provider details."""
    def __init__(self):
        import stripe
        self.live = os.getenv("STRIPE_ENVIRONMENT", "sandbox") == "production"
        self.price = os.environ["STRIPE_PRICE_ID"]
        self.account = os.environ["STRIPE_ACCOUNT_ID"]
        self.client = stripe.StripeClient(os.environ["STRIPE_RESTRICTED_KEY"], stripe_version=API_VERSION,
                                         max_network_retries=0, http_client=stripe.RequestsClient(timeout=12))

    def call(self, operation, *args, mutation=False, **kwargs):
        try:
            return _dict(operation(*args, **kwargs))
        except Exception as error:
            # A 4xx request rejection is definitive; timeouts/5xx remain unknown.
            status = getattr(error, "http_status", None)
            uncertain = bool(mutation and (status is None or status >= 500 or status in (409, 429)))
            raise providers.ProviderError(ERROR, uncertain=uncertain) from None

    def check(self, value):
        s.require(value.get("livemode") is self.live, 503, "決済環境を照合できませんでした。")
        return value

    def validate(self):
        account = self.call(self.client.v1.accounts.retrieve_current)
        s.require(account.get("id") == self.account, 503, "決済先の会社を確認しています。")
        price = self.check(self.call(self.client.v1.prices.retrieve, self.price))
        s.require(price.get("id") == self.price and price.get("active") is True
                  and price.get("currency") == "jpy" and price.get("unit_amount") == 3000
                  and price.get("type") == "recurring" and price.get("billing_scheme") == "per_unit"
                  and price.get("recurring", {}).get("interval") == "month"
                  and price.get("recurring", {}).get("interval_count") == 1
                  and price.get("recurring", {}).get("usage_type") == "licensed"
                  and price.get("tax_behavior") in ("inclusive", "unspecified"), 503, "月額料金の設定を確認しています。")

    def checkout(self, key, user):
        origin = os.getenv("NOTTA_PUBLIC_ORIGIN", "").rstrip("/")
        parsed = urlparse(origin)
        if not (parsed.scheme == "https" and parsed.netloc and not parsed.query
                and not parsed.fragment and not parsed.username and parsed.path in ("", "/")):
            # No Checkout API has run, so this is a definitive local rejection.
            raise providers.ProviderError("決済の戻り先を確認しています。", uncertain=False)
        metadata = {"app": APP, "checkout_id": key, "user_id": user["id"]}
        suffix = "".join(secrets.choice(string.ascii_lowercase) for _ in range(8))
        return self.check(self.call(self.client.v1.checkout.sessions.create, {
            "mode": "subscription", "line_items": [{"price": self.price, "quantity": 1}],
            "client_reference_id": key, "customer_email": user["email"], "metadata": metadata,
            "subscription_data": {"metadata": metadata, "billing_mode": {"type": "flexible"}}, "locale": "ja",
            "automatic_tax": {"enabled": False}, "allow_promotion_codes": False,
            "adaptive_pricing": {"enabled": False},
            "integration_identifier": "notta_monthly_" + suffix,
            "success_url": origin + "/billing/return", "cancel_url": origin + "/workspace",
        }, options={"idempotency_key": "notta-checkout-" + key}, mutation=True))

    def session(self, sid):
        return self.check(self.call(self.client.v1.checkout.sessions.retrieve, sid))

    def subscription(self, sid):
        return self.check(self.call(self.client.v1.subscriptions.retrieve, sid))

    def list_all(self, operation, params):
        values = []
        # Bound the small pilot, but never silently truncate payment/refund state.
        for _ in range(12):
            page = self.call(operation, {**params, "limit": 100})
            rows = page.get("data", [])
            values.extend(rows)
            if not page.get("has_more"):
                return values
            s.require(rows and _id(rows[-1]), 503, "決済情報の追加照合が必要です。")
            params = {**params, "starting_after": _id(rows[-1])}
        raise s.Problem(503, "決済情報の追加照合が必要です。")

    def invoices(self, sid):
        return self.list_all(self.client.v1.invoices.list, {"subscription": sid})

    def invoice(self, iid):
        return self.check(self.call(self.client.v1.invoices.retrieve, iid))

    def invoice_payments(self, iid):
        return self.list_all(self.client.v1.invoice_payments.list, {"invoice": iid, "status": "paid"})

    def intent(self, pid):
        return self.check(self.call(self.client.v1.payment_intents.retrieve, pid))

    def charge(self, cid):
        return self.check(self.call(self.client.v1.charges.retrieve, cid))

    def refunds(self, cid):
        return self.list_all(self.client.v1.refunds.list, {"charge": cid})

    def refund(self, rid):
        return self.call(self.client.v1.refunds.retrieve, rid)

    def cancel(self, sid):
        return self.call(self.client.v1.subscriptions.update, sid, {"cancel_at_period_end": True},
                         options={"idempotency_key": "notta-cancel-" + sid}, mutation=True)


def _resume_url(row):
    stripe = Stripe()
    stripe.validate()
    current = stripe.session(row["link_id"])
    s.require(current.get("id") == row["link_id"] and current.get("status") == "open"
              and current.get("client_reference_id") == row["id"]
              and _metadata(current.get("metadata", {}), row), 409, "前の決済の支払い状態を確認してください。")
    _validate_session(current)
    return _url(current.get("url"))


def checkout(c, user, data):
    s.require(ready(), 503, "月額プランは準備中です。現在は請求されません。")
    s.require(data.get("consent") is True and data.get("terms") == core.TERMS, 400, "料金・自動更新・解約・返金条件をご確認ください。")
    uid = user["id"]
    c.execute("BEGIN IMMEDIATE")
    core.budget(c)
    # Also lock the budget row before counting paid seats and AI commitments;
    # retain this guard if the adapter's global write lock becomes row based.
    c.execute("UPDATE svc_budgets SET spent_yen=spent_yen WHERE month=?", (core.month(),))
    s.require(not core.paid(c, uid), 409, "すでに月額プランを利用中です。")
    old = c.execute("SELECT * FROM svc_checkouts WHERE user_id=? AND status IN ('reserved','pending','unknown') ORDER BY created DESC LIMIT 1", (uid,)).fetchone()
    if old:
        s.require(old["status"] == "pending" and (old["link_id"] or "").startswith("cs_") and old["url"], 409, "前の決済を確認中です。追加で支払わずお問い合わせください。")
        c.commit()
        return {"url": _resume_url(old), "id": old["id"]}
    old_sub = core.subscription(c, uid)
    s.require(not old_sub or old_sub["status"] in ("CANCELED", "DEACTIVATED") and old_sub["paid_until"] <= s.stamp(), 409, "既存の契約を確認してください。新しい契約は作成していません。")
    count = c.execute("SELECT COUNT(DISTINCT user_id) FROM svc_subscriptions WHERE status IN ('ACTIVE','PENDING') OR paid_until>?", (s.stamp(),)).fetchone()[0]
    pending = c.execute("SELECT COUNT(*) FROM svc_checkouts WHERE status IN ('reserved','pending','unknown')").fetchone()[0]
    budget = core.budget(c)
    required = core.commitments(c, exclude_uid=uid, new_paid_uid=uid, reserve_paid_full=True)
    s.require(count + pending < 5 and budget["spent_yen"] + budget["reserved_yen"] + required <= core.MONTH_LIMIT, 429, "現在、月額プランの新規受付を調整しています。保存した企画はそのまま使えます。")
    key = s.ident()
    c.execute("INSERT INTO svc_checkouts(id,user_id,status,created,month,terms) VALUES(?,?,?,?,?,?)", (key, uid, "reserved", s.stamp(), core.month(), core.TERMS))
    core.event(c, uid, "checkout_started")
    c.commit()
    mutation_started = False
    try:
        stripe = Stripe()
        stripe.validate()
        mutation_started = True
        result = stripe.checkout(key, user)
        s.require(isinstance(result.get("id"), str) and result["id"].startswith("cs_"), 502, "決済の照合が必要です。")
        _validate_session(result)
        url = _url(result.get("url"))
        c.execute("UPDATE svc_checkouts SET status='pending',link_id=?,order_id=?,url=? WHERE id=?", (result["id"], result["id"], url, key))
        c.commit()
        return {"url": url, "id": key}
    except Exception as error:
        c.rollback()
        uncertain = mutation_started and (not isinstance(error, providers.ProviderError) or error.uncertain)
        c.execute("UPDATE svc_checkouts SET status=? WHERE id=?", ("unknown" if uncertain else "failed", key))
        c.commit()
        raise


def _metadata(value, row):
    expected = {"app": APP, "checkout_id": row["id"], "user_id": row["user_id"]}
    return all(value.get(key) == val for key, val in expected.items())


def _validate_subscription(current, local, stripe):
    items = current.get("items", {})
    rows = items.get("data", [])
    s.require(current.get("id") == local["id"] and _id(current.get("customer")) == local["customer_id"]
              and current.get("currency") == "jpy"
              and not items.get("has_more") and len(rows) == 1 and rows[0].get("quantity") == 1
              and _id(rows[0].get("price")) == stripe.price and not current.get("trial_end")
              and not current.get("pause_collection") and _no_tax_or_discount(current)
              and not rows[0].get("tax_rates") and not rows[0].get("discounts")
              and _jpy_presentment(current), 503, "契約内容を照合できませんでした。")
    return rows[0]


def link_subscription(c, row, stripe):
    session = stripe.session(row["link_id"])
    s.require(session.get("id") == row["link_id"] and session.get("mode") == "subscription"
              and session.get("client_reference_id") == row["id"] and _metadata(session.get("metadata", {}), row), 503, "申込みの照合ができませんでした。")
    if session.get("status") == "expired":
        c.execute("UPDATE svc_checkouts SET status='expired',url=NULL WHERE id=? AND status='pending'", (row["id"],))
        c.commit()
        return None
    _validate_session(session)
    if session.get("status") != "complete":
        return None
    sid, customer = _id(session.get("subscription")), _id(session.get("customer"))
    s.require(isinstance(sid, str) and sid.startswith("sub_") and isinstance(customer, str) and customer.startswith("cus_")
              and session.get("currency") == "jpy" and session.get("amount_total") == 3000, 503, "契約者と申込額を照合できませんでした。")
    current = stripe.subscription(sid)
    _validate_subscription(current, {"id": sid, "customer_id": customer}, stripe)
    s.require(_metadata(current.get("metadata", {}), row), 503, "契約と申込みを照合できませんでした。")
    c.execute("BEGIN IMMEDIATE")
    old = c.execute("SELECT user_id,customer_id FROM svc_subscriptions WHERE id=?", (sid,)).fetchone()
    s.require(not old or (old[0] == row["user_id"] and old[1] == customer), 409, "別のアカウントの契約です。")
    duplicate = c.execute("SELECT 1 FROM svc_subscriptions WHERE customer_id=? AND user_id!=?", (customer, row["user_id"])).fetchone()
    s.require(not duplicate, 409, "別のアカウントの契約者です。")
    c.execute("INSERT OR IGNORE INTO svc_subscriptions(id,user_id,customer_id,status,checked_at) VALUES(?,?,?,?,?)", (sid, row["user_id"], customer, "PENDING", s.stamp()))
    c.execute("UPDATE svc_checkouts SET status='linked',url=NULL WHERE id=?", (row["id"],))
    c.commit()
    return sid


def _invoice_record(c, invoice, local, stripe, item):
    sid, customer = local["id"], local["customer_id"]
    parent = invoice.get("parent", {}) or {}
    s.require(parent.get("type") == "subscription_details" and _id(parent.get("subscription_details", {}).get("subscription")) == sid
              and _id(invoice.get("customer")) == customer and invoice.get("currency") == "jpy", 503, "請求の契約者を照合できませんでした。")
    s.require(_no_tax_or_discount(invoice) and not invoice.get("total_taxes")
              and not invoice.get("total_discount_amounts") and not invoice.get("total_pretax_credit_amounts")
              and invoice.get("amount_shipping") == 0, 503, "請求に追加の税・割引・送料がないことを確認しています。")
    if invoice.get("status") != "paid":
        return None
    lines = invoice.get("lines", {})
    rows = lines.get("data", [])
    s.require(not lines.get("has_more") and len(rows) == 1, 503, "請求明細を確認しています。")
    line = rows[0]
    line_parent = line.get("parent", {}) or {}
    detail = line_parent.get("subscription_item_details", {})
    price = (line.get("pricing", {}) or {}).get("price_details", {})
    period = line.get("period", {})
    s.require(invoice.get("subtotal") == 3000 and invoice.get("total") == 3000 and invoice.get("amount_paid") == 3000 and invoice.get("amount_remaining") == 0
              and invoice.get("amount_due") == 3000 and not invoice.get("amount_paid_out_of_band", 0)
              and invoice.get("billing_reason") in ("subscription_create", "subscription_cycle")
              and _id(price.get("price")) == stripe.price and line.get("quantity") == 1
              and line.get("currency") == "jpy" and line.get("livemode") is stripe.live
              and line_parent.get("type") == "subscription_item_details" and detail.get("subscription") == sid
              and line.get("amount") == 3000 and _id(detail.get("subscription_item")) == item.get("id")
              and not line.get("taxes") and not line.get("discounts") and not line.get("discount_amounts")
              and not line.get("pretax_credit_amounts")
              and not detail.get("proration") and isinstance(period.get("start"), int)
              and isinstance(period.get("end"), int) and 27 * 86400 <= period["end"] - period["start"] <= 32 * 86400,
              503, "支払額と月額プランの期間が一致しません。")
    payments = stripe.invoice_payments(invoice["id"])
    s.require(len(payments) == 1, 503, "支払情報の追加照合が必要です。")
    payment = payments[0]
    stripe.check(payment)
    reference = payment.get("payment", {})
    s.require(_id(payment.get("invoice")) == invoice["id"] and payment.get("status") == "paid"
              and payment.get("currency") == "jpy" and payment.get("amount_paid") == 3000
              and reference.get("type") == "payment_intent", 503, "請求と支払情報を照合できませんでした。")
    intent = stripe.intent(_id(reference.get("payment_intent")))
    s.require(intent.get("id") == _id(reference.get("payment_intent")) and intent.get("status") == "succeeded"
              and _id(intent.get("customer")) == customer and intent.get("currency") == "jpy"
              and intent.get("amount") == 3000 and intent.get("amount_received") == 3000
              and _jpy_presentment(intent, amount=True),
              503, "入金を確認できませんでした。")
    charge = stripe.charge(_id(intent.get("latest_charge")))
    s.require(charge.get("id") == _id(intent.get("latest_charge")) and _id(charge.get("payment_intent")) == intent["id"]
              and _id(charge.get("customer")) == customer and charge.get("paid") is True
              and charge.get("captured") is True and charge.get("status") == "succeeded"
              and charge.get("amount") == 3000 and charge.get("amount_captured") == 3000
              and charge.get("currency") == "jpy", 503, "入金先と金額を確認しています。")
    refunds = {_id(refund): refund for refund in stripe.refunds(charge["id"])}
    for known in c.execute("SELECT id FROM svc_refunds WHERE payment_id=?", (intent["id"],)):
        if known[0] not in refunds:
            refunds[known[0]] = stripe.refund(known[0])
    for refund in refunds.values():
        s.require(_id(refund.get("charge")) == charge["id"] and _id(refund.get("payment_intent")) == intent["id"]
                  and refund.get("currency") == "jpy" and isinstance(refund.get("amount"), int)
                  and 0 < refund["amount"] <= 3000, 503, "返金情報を照合できませんでした。")
    paid_at = invoice.get("status_transitions", {}).get("paid_at")
    s.require(isinstance(paid_at, int) and paid_at > 0, 503, "入金日時を確認しています。")
    return {"id": intent["id"], "invoice": invoice["id"], "completed": paid_at,
            "end": period["end"], "refunded": bool(charge.get("amount_refunded", 0) or charge.get("disputed")),
            "refunds": list(refunds.values())}


def sync_subscription(c, sid, stripe=None):
    stripe = stripe or Stripe()
    # Stripe has no universal subscription version. Lock BEFORE provider reads,
    # including on Postgres, so an older concurrent read cannot restore access.
    c.execute("BEGIN IMMEDIATE")
    c.execute("UPDATE svc_subscriptions SET checked_at=checked_at WHERE id=?", (sid,))
    local = c.execute("SELECT * FROM svc_subscriptions WHERE id=?", (sid,)).fetchone()
    if not local:
        c.commit()
        return False
    try:
        current = stripe.subscription(sid)
        item = _validate_subscription(current, local, stripe)
        invoices = {_id(value): value for value in stripe.invoices(sid)}
        # A paginated/list snapshot may lag; never forget an already known bill.
        for row in c.execute("SELECT invoice_id FROM svc_payments WHERE subscription_id=?", (sid,)):
            invoices[row[0]] = stripe.invoice(row[0])
        records = []
        for invoice in invoices.values():
            stripe.check(invoice)
            record = _invoice_record(c, invoice, local, stripe, item)
            if record:
                records.append(record)
        for record in records:
            old = c.execute("SELECT user_id,subscription_id,invoice_id FROM svc_payments WHERE id=?", (record["id"],)).fetchone()
            s.require(not old or tuple(old[key] for key in ("user_id", "subscription_id", "invoice_id"))
                      == (local["user_id"], sid, record["invoice"]), 409, "別の契約の支払情報です。")
            c.execute("INSERT OR IGNORE INTO svc_payments(id,subscription_id,user_id,invoice_id,amount,currency,completed,period_end,refunded) VALUES(?,?,?,?,?,?,?,?,?)", (record["id"], sid, local["user_id"], record["invoice"], 3000, "JPY", record["completed"], record["end"], 0))
            for refund in record["refunds"]:
                status = {"succeeded": "COMPLETED", "pending": "PENDING", "requires_action": "PENDING", "failed": "FAILED", "canceled": "FAILED"}.get(refund.get("status"))
                s.require(status, 503, "返金状態の確認が必要です。")
                c.execute("INSERT OR IGNORE INTO svc_refunds(id,payment_id,amount,status,updated) VALUES(?,?,?,?,?)", (refund["id"], record["id"], refund["amount"], status, s.stamp()))
                c.execute("UPDATE svc_refunds SET status=CASE WHEN status='COMPLETED' THEN status ELSE ? END,updated=? WHERE id=?", (status, s.stamp(), refund["id"]))
            held = c.execute("SELECT 1 FROM svc_refunds WHERE payment_id=? AND status IN ('PENDING','COMPLETED') LIMIT 1", (record["id"],)).fetchone()
            c.execute("UPDATE svc_payments SET refunded=? WHERE id=?", (int(record["refunded"] or bool(held)), record["id"]))
            completed = c.execute("SELECT COALESCE(SUM(amount),0) FROM svc_refunds WHERE payment_id=? AND status='COMPLETED'", (record["id"],)).fetchone()[0]
            if completed >= 3000:
                c.execute("UPDATE svc_refund_requests SET status='completed' WHERE payment_id=?", (record["id"],))
        rows = [dict(row) for row in c.execute("SELECT * FROM svc_payments WHERE subscription_id=? ORDER BY period_end DESC", (sid,))]
        valid = [row for row in rows if not row["refunded"]]
        latest = valid[0] if valid else None
        first = min((row["completed"] for row in rows), default=0)
        status = {"active": "ACTIVE", "past_due": "ACTIVE", "unpaid": "ACTIVE", "canceled": "CANCELED", "incomplete_expired": "DEACTIVATED"}.get(current.get("status"), "PENDING")
        cancel_at = current.get("cancel_at") or (item.get("current_period_end") if current.get("cancel_at_period_end") else None)
        c.execute("UPDATE svc_subscriptions SET status=?,paid_until=?,cancel_at=?,first_paid=?,refunded=?,checked_at=?,period=? WHERE id=?", (status, latest["period_end"] if latest else 0, cancel_at, first, int(bool(rows) and not valid), s.stamp(), latest["invoice_id"] if latest else "", sid))
        if first and not local["first_paid"]:
            core.event(c, local["user_id"], "payment_confirmed")
        c.commit()
        return True
    except Exception:
        c.rollback()
        raise


def reconcile(c, uid):
    s.require(ready(), 503, "決済の接続を準備しています。現在は請求されません。")
    stripe = Stripe()
    stripe.validate()
    rows = c.execute("SELECT * FROM svc_checkouts WHERE user_id=? AND status='pending'", (uid,)).fetchall()
    for row in rows:
        s.require((row["link_id"] or "").startswith("cs_"), 409, "以前の決済は運営が確認します。追加で支払わないでください。")
        link_subscription(c, row, stripe)
    for row in c.execute("SELECT id FROM svc_subscriptions WHERE user_id=?", (uid,)).fetchall():
        s.require(row[0].startswith("sub_"), 409, "以前の契約は運営が確認します。")
        sync_subscription(c, row[0], stripe)
    return summary(c, uid)


def summary(c, uid):
    sub = core.subscription(c, uid)
    first = c.execute("SELECT completed,refunded FROM svc_payments WHERE user_id=? ORDER BY completed,id LIMIT 1", (uid,)).fetchone()
    request = c.execute("SELECT requested,status FROM svc_refund_requests WHERE user_id=? ORDER BY requested DESC LIMIT 1", (uid,)).fetchone()
    pending = c.execute("SELECT * FROM svc_checkouts WHERE user_id=? AND status IN ('reserved','pending','unknown') ORDER BY created DESC LIMIT 1", (uid,)).fetchone()
    pending_url = None
    if pending and pending["status"] == "pending" and (pending["link_id"] or "").startswith("cs_") and ready():
        try:
            # The dashboard contains a direct resume link. Never expose a saved
            # URL unless its current Session still matches the agreed amount.
            pending_url = _resume_url(pending)
        except (providers.ProviderError, s.Problem):
            pass  # Keep the dashboard available and retain the unresolved hold.
    return {"paid": core.paid(c, uid), "subscription": {key: sub[key] for key in ("status", "paid_until", "cancel_at", "first_paid", "refunded")} if sub else None,
            "refundEligible": bool(first and not first["refunded"] and s.stamp() <= first["completed"] + 7 * 86400),
            "refundRequest": dict(request) if request else None, "pending": pending["status"] if pending else None,
            "pendingUrl": pending_url}


def cancel(c, uid, data):
    s.require(ready(), 503, "決済の接続を確認しています。")
    s.require(data.get("confirm") is True, 400, "次回の更新を停止することをご確認ください。")
    sub = core.subscription(c, uid)
    s.require(sub and sub["id"].startswith("sub_") and sub["status"] in ("ACTIVE", "CANCELED"), 409, "更新を停止できる契約がありません。")
    stripe = Stripe()
    stripe.validate()
    current = stripe.subscription(sub["id"])
    _validate_subscription(current, sub, stripe)
    if not current.get("cancel_at_period_end") and current.get("status") != "canceled":
        stripe.cancel(sub["id"])
    sync_subscription(c, sub["id"], stripe)
    return summary(c, uid)


def request_refund(c, uid, data):
    s.require(data.get("confirm") is True, 400, "初回の返金申出と更新停止をご確認ください。")
    c.execute("BEGIN IMMEDIATE")
    sub = core.subscription(c, uid)
    payment = c.execute("SELECT id,completed,refunded FROM svc_payments WHERE user_id=? ORDER BY completed,id LIMIT 1", (uid,)).fetchone()
    s.require(sub and payment and not payment["refunded"] and s.stamp() <= payment["completed"] + 7 * 86400, 409, "初回7日以内の返金対象の契約がありません。お問い合わせ先へご連絡ください。")
    old = c.execute("SELECT id FROM svc_refund_requests WHERE payment_id=?", (payment["id"],)).fetchone()
    if not old:
        c.execute("INSERT INTO svc_refund_requests(id,user_id,payment_id,requested,status) VALUES(?,?,?,?,?)", (s.ident(), uid, payment["id"], s.stamp(), "received"))
        core.event(c, uid, "refund_requested")
    c.commit()  # Preserve timely notice even when Stripe is unavailable.
    try:
        cancel(c, uid, {"confirm": True})
    except (providers.ProviderError, s.Problem):
        return {"ok": True, "message": "返金のお申出を受け付けました。MAORAが返金と更新停止を確認し、メールでご連絡します。", "needsReview": True}
    return {"ok": True, "message": "返金のお申出と更新停止を受け付けました。MAORAが返金処理を確認し、メールでご連絡します。"}


def _event(raw, signature):
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    s.require(secret.startswith("whsec_") and isinstance(signature, str), 403, "通知の署名が一致しません。")
    values = {}
    for part in signature.split(","):
        key, _, value = part.partition("=")
        values.setdefault(key, []).append(value)
    stamps = values.get("t", [])
    s.require(len(stamps) == 1 and re.fullmatch(r"[0-9]{1,12}", stamps[0]), 403, "通知の署名が一致しません。")
    timestamp = int(stamps[0])
    s.require(abs(s.stamp() - timestamp) <= 300, 403, "通知の有効期限が切れています。")
    expected = hmac.new(secret.encode(), str(timestamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
    s.require(any(hmac.compare_digest(expected, value) for value in values.get("v1", [])), 403, "通知の署名が一致しません。")
    try:
        event = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise s.Problem(400, "通知形式が不正です。")
    s.require(isinstance(event, dict) and event.get("object") == "event", 400, "通知形式が不正です。")
    return event


def webhook(c, raw, signature):
    event = _event(raw, signature)
    live = os.getenv("STRIPE_ENVIRONMENT", "sandbox") == "production"
    # Direct account integrations only. Connect/organization forwarded events
    # cannot be accepted by this account's endpoint.
    s.require(event.get("livemode") is live and not event.get("account") and not event.get("context"), 403, "通知の決済環境が一致しません。")
    s.require(isinstance(event.get("created"), int) and event["created"] <= s.stamp() + 300, 400, "通知日時が不正です。")
    key = s.text(event.get("id"), 100)
    s.require(key.startswith("evt_"), 400, "通知IDが不正です。")
    kind = s.text(event.get("type"), 100)
    digest = hashlib.sha256(raw).hexdigest()
    c.execute("BEGIN IMMEDIATE")
    old = c.execute("SELECT * FROM svc_webhooks WHERE id=?", (key,)).fetchone()
    s.require(not old or old["digest"] == digest, 409, "通知IDが重複しています。")
    if old and old["status"] == "completed":
        c.commit()
        return {"ok": True, "duplicate": True}
    c.execute("INSERT OR IGNORE INTO svc_webhooks(id,event_type,digest,status,created,updated) VALUES(?,?,?,?,?,?)", (key, kind, digest, "received", s.stamp(), s.stamp()))
    c.commit()
    if kind.startswith(("checkout.session.", "customer.subscription.", "invoice.", "payment_intent.", "charge.", "refund.")):
        # Five-member pilot: traverse only our own saved Session/subscription IDs.
        # Event metadata or a Mobby customer with the same email never maps users.
        users = {row[0] for row in c.execute("SELECT user_id FROM svc_checkouts WHERE status='pending' AND link_id LIKE 'cs_%'")}
        users.update(row[0] for row in c.execute("SELECT user_id FROM svc_subscriptions WHERE id LIKE 'sub_%'"))
        for uid in users:
            reconcile(c, uid)
    c.execute("UPDATE svc_webhooks SET status='completed',updated=? WHERE id=?", (s.stamp(), key))
    c.commit()
    return {"ok": True}
