"""Billing security boundary fixtures. No Stripe account or real charge is used."""
import copy
import hashlib
import hmac
import json
import os
import secrets
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server as s
import service_core as core
import service_providers as providers
import service_stripe as billing
from database import HybridRow

try:
    import stripe as stripe_sdk
    HAS_SDK = callable(getattr(stripe_sdk, "StripeClient", None))
except ImportError:
    HAS_SDK = False

NOW = 1792717200
END = NOW + 31 * 86400


class FakeStripe:
    price, account, live = "price_notta", "acct_test", False

    def __init__(self):
        self.sessions, self.subscriptions, self.bills, self.payments = {}, {}, {}, {}
        self.intents, self.charges, self.refund_objects = {}, {}, {}
        self.new_count, self.cancel_count = 0, 0
        self.failure = None
        self.no_refunds_in_list = False

    def check(self, value):
        s.require(value.get("livemode") is False, 503, "wrong environment")
        return value

    def validate(self): pass

    def checkout(self, key, user):
        self.new_count += 1
        if self.failure:
            raise self.failure
        session = {"id": "cs_test_" + key, "livemode": False, "mode": "subscription", "status": "open",
                   "currency": "jpy", "amount_subtotal": 3000, "amount_total": 3000,
                   "automatic_tax": {"enabled": False}, "adaptive_pricing": {"enabled": False}, "allow_promotion_codes": False,
                   "total_details": {"amount_tax": 0, "amount_discount": 0, "amount_shipping": 0},
                   "client_reference_id": key, "metadata": {"app": billing.APP, "checkout_id": key, "user_id": user["id"]},
                   "url": "https://checkout.stripe.com/c/pay/test_" + key}
        self.sessions[session["id"]] = session
        return copy.deepcopy(session)

    def complete(self, sid, paid=True):
        session = self.sessions[sid]
        session.update(status="complete", subscription="sub_notta", customer="cus_notta", currency="jpy", amount_total=3000)
        self.subscriptions["sub_notta"] = {"id": "sub_notta", "livemode": False, "customer": "cus_notta", "status": "active", "currency": "jpy",
            "automatic_tax": {"enabled": False}, "default_tax_rates": [], "discounts": [],
            "metadata": copy.deepcopy(session["metadata"]), "cancel_at_period_end": False, "items": {"has_more": False, "data": [
                {"id": "si_notta", "price": {"id": self.price}, "quantity": 1, "current_period_end": END}]}}
        self.add_bill("in_first", NOW, END, paid)

    def add_bill(self, iid, start, end, paid=True):
        pid, cid = "pi_" + iid, "ch_" + iid
        self.bills[iid] = {"id": iid, "livemode": False, "customer": "cus_notta", "currency": "jpy", "status": "paid" if paid else "open",
            "parent": {"type": "subscription_details", "subscription_details": {"subscription": "sub_notta"}},
            "subtotal": 3000, "total": 3000, "amount_paid": 3000 if paid else 0, "amount_remaining": 0 if paid else 3000, "amount_due": 3000,
            "automatic_tax": {"enabled": False}, "default_tax_rates": [], "discounts": [], "amount_shipping": 0,
            "billing_reason": "subscription_create" if iid == "in_first" else "subscription_cycle",
            "status_transitions": {"paid_at": start}, "lines": {"has_more": False, "data": [
                {"id": "il_" + iid, "livemode": False, "currency": "jpy", "amount": 3000, "quantity": 1, "pricing": {"price_details": {"price": self.price}},
                 "parent": {"type": "subscription_item_details", "subscription_item_details": {"subscription": "sub_notta", "subscription_item": "si_notta", "proration": False}}, "period": {"start": start, "end": end}}]}}
        self.payments[iid] = [{"id": "inpay_" + iid, "livemode": False, "invoice": iid, "status": "paid", "currency": "jpy", "amount_paid": 3000,
                               "payment": {"type": "payment_intent", "payment_intent": pid}}]
        self.intents[pid] = {"id": pid, "livemode": False, "customer": "cus_notta", "status": "succeeded", "currency": "jpy",
                             "amount": 3000, "amount_received": 3000, "latest_charge": cid}
        self.charges[cid] = {"id": cid, "livemode": False, "customer": "cus_notta", "payment_intent": pid, "status": "succeeded",
                             "paid": True, "captured": True, "currency": "jpy", "amount": 3000, "amount_captured": 3000, "amount_refunded": 0}

    def add_refund(self, rid="re_first", status="pending", amount=3000, iid="in_first"):
        self.refund_objects[rid] = {"id": rid, "charge": "ch_" + iid, "payment_intent": "pi_" + iid,
                                     "amount": amount, "currency": "jpy", "status": status}

    def session(self, sid): return self.check(copy.deepcopy(self.sessions[sid]))
    def subscription(self, sid): return self.check(copy.deepcopy(self.subscriptions[sid]))
    def invoices(self, sid): return copy.deepcopy(list(self.bills.values()))
    def invoice(self, iid): return self.check(copy.deepcopy(self.bills[iid]))
    def invoice_payments(self, iid): return copy.deepcopy(self.payments[iid])
    def intent(self, pid): return self.check(copy.deepcopy(self.intents[pid]))
    def charge(self, cid): return self.check(copy.deepcopy(self.charges[cid]))
    def refunds(self, cid):
        return [] if self.no_refunds_in_list else copy.deepcopy([r for r in self.refund_objects.values() if r["charge"] == cid])
    def refund(self, rid): return copy.deepcopy(self.refund_objects[rid])
    def cancel(self, sid):
        self.cancel_count += 1
        self.subscriptions[sid].update(cancel_at_period_end=True)
        return self.subscription(sid)


class StripeBillingTest(unittest.TestCase):
    def setUp(self):
        folder = Path(__file__).resolve().parents[1] / "data"
        folder.mkdir(exist_ok=True)
        self.path = folder / ("stripe-test-" + s.ident() + ".sqlite3")
        s.init_db(self.path)
        self.c = s.connect()
        self.env = patch.dict(os.environ, {"STRIPE_ENVIRONMENT": "sandbox", "STRIPE_WEBHOOK_SECRET": "whsec_fixture",
            "STRIPE_ACCOUNT_ID": "acct_test", "STRIPE_PRICE_ID": "price_notta", "NOTTA_PUBLIC_ORIGIN": "https://notta.example"})
        self.env.start()
        self.clock_patch = patch.object(s, "stamp", return_value=NOW)
        self.clock = self.clock_patch.start()
        self.ready_patch = patch.object(billing, "ready", return_value=True)
        self.ready_patch.start()
        self.fake = FakeStripe()
        self.provider_patch = patch.object(billing, "Stripe", return_value=self.fake)
        self.provider_patch.start()
        self.user = self.make_user()
        self.other = self.make_user()

    def tearDown(self):
        self.c.close()
        self.provider_patch.stop(); self.ready_patch.stop(); self.clock_patch.stop(); self.env.stop()
        for path in (self.path, Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")):
            path.unlink(missing_ok=True)

    def make_user(self):
        uid = s.ident()
        self.c.execute("INSERT INTO users VALUES(?,?,?,?,?,?)", (uid, uid + "@example.test", "契約者", "creator", secrets.token_hex(16), "disabled"))
        self.c.execute("INSERT INTO svc_accounts(user_id,verified_at,created) VALUES(?,?,?)", (uid, NOW, NOW))
        self.c.commit()
        return {"id": uid, "email": uid + "@example.test"}

    def checkout(self):
        result = billing.checkout(self.c, self.user, {"consent": True, "terms": core.TERMS})
        return self.c.execute("SELECT link_id FROM svc_checkouts WHERE id=?", (result["id"],)).fetchone()[0]

    def paid(self):
        sid = self.checkout(); self.fake.complete(sid)
        billing.reconcile(self.c, self.user["id"])
        return sid

    def problem(self, status, call):
        with self.assertRaises(s.Problem) as caught:
            call()
        self.assertEqual(status, caught.exception.status)
        self.c.rollback()

    def signed(self, event, at=None):
        raw = json.dumps(event).encode()
        at = NOW if at is None else at
        digest = hmac.new(b"whsec_fixture", str(at).encode() + b"." + raw, hashlib.sha256).hexdigest()
        return raw, "t=" + str(at) + ",v1=" + digest

    def event(self, kind="invoice.paid"):
        return {"id": "evt_fixture", "object": "event", "type": kind, "created": NOW, "livemode": False,
                "data": {"object": {"customer": "cus_wrong", "metadata": {"user_id": self.other["id"]}}}}

    def test_checkout_requires_consent_and_reuses_one_saved_link(self):
        self.problem(400, lambda: billing.checkout(self.c, self.user, {"consent": False, "terms": core.TERMS}))
        sid = self.checkout()
        again = billing.checkout(self.c, self.user, {"consent": True, "terms": core.TERMS})
        self.assertEqual(again["url"], self.fake.sessions[sid]["url"])
        self.assertEqual(self.fake.new_count, 1)
        self.assertFalse(billing.summary(self.c, self.user["id"])["paid"])
        self.assertIsNone(billing.summary(self.c, self.other["id"])["pendingUrl"])

    def test_summary_only_returns_fresh_verified_resume_url_without_new_session(self):
        sid = self.checkout()
        session = self.fake.sessions[sid]
        self.assertEqual(billing.summary(self.c, self.user["id"])["pendingUrl"], session["url"])
        changes = [(session, "amount_total", 3300),
                   (session["total_details"], "amount_tax", 300),
                   (session, "allow_promotion_codes", True),
                   (session, "adaptive_pricing", {"enabled": True}),
                   (session, "url", "https://attacker.test/pay"),
                   (session["metadata"], "user_id", self.other["id"]),
                   (session, "status", "complete"), (session, "status", "expired")]
        for obj, field, value in changes:
            with self.subTest(field=field, value=value):
                old = obj[field]; obj[field] = value
                result = billing.summary(self.c, self.user["id"])
                self.assertIsNone(result["pendingUrl"])
                self.assertEqual(result["pending"], "pending")
                self.assertFalse(result["paid"])
                obj[field] = old
        session["url"] = "https://checkout.stripe.com/c/pay/current_verified_url"
        self.assertEqual(billing.summary(self.c, self.user["id"])["pendingUrl"], session["url"])
        self.assertEqual(self.fake.new_count, 1)
        with patch.object(self.fake, "session", side_effect=providers.ProviderError("unavailable")):
            result = billing.summary(self.c, self.user["id"])
            self.assertIsNone(result["pendingUrl"])
            self.assertEqual(result["pending"], "pending")
        with patch.object(billing, "ready", return_value=False), patch.object(self.fake, "session") as read:
            self.assertIsNone(billing.summary(self.c, self.user["id"])["pendingUrl"])
            read.assert_not_called()

    def test_unknown_checkout_keeps_reservation_and_does_not_blindly_retry(self):
        self.fake.failure = providers.ProviderError("unknown", uncertain=True)
        with self.assertRaises(providers.ProviderError): self.checkout()
        row = self.c.execute("SELECT status FROM svc_checkouts").fetchone()
        self.assertEqual(row[0], "unknown")
        self.problem(409, self.checkout)
        self.assertEqual(self.fake.new_count, 1)

    def test_rejected_checkout_releases_reservation(self):
        self.fake.failure = providers.ProviderError("rejected", uncertain=False)
        with self.assertRaises(providers.ProviderError): self.checkout()
        self.assertEqual(self.c.execute("SELECT status FROM svc_checkouts").fetchone()[0], "failed")

    def test_validation_failure_releases_capacity_and_allows_fixed_configuration(self):
        with patch.object(self.fake, "validate", side_effect=s.Problem(503, "price mismatch")):
            self.problem(503, self.checkout)
        self.assertEqual(self.fake.new_count, 0)
        self.assertEqual(self.c.execute("SELECT status FROM svc_checkouts").fetchone()[0], "failed")
        self.assertIsNone(billing.summary(self.c, self.user["id"])["pending"])
        self.assertTrue(self.checkout().startswith("cs_test_"))
        self.assertEqual(self.fake.new_count, 1)

    def test_active_and_checkout_complete_do_not_grant_without_settled_invoice(self):
        sid = self.checkout(); self.fake.complete(sid, paid=False)
        summary = billing.reconcile(self.c, self.user["id"])
        self.assertFalse(summary["paid"])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM svc_payments").fetchone()[0], 0)

    def test_adjusted_session_never_returns_checkout_link_and_keeps_unknown(self):
        original = self.fake.checkout
        def adjusted(key, user):
            result = original(key, user)
            result["total_details"]["amount_tax"] = 300
            result["amount_total"] = 3300
            return result
        self.fake.checkout = adjusted
        self.problem(503, self.checkout)
        row = self.c.execute("SELECT status,url FROM svc_checkouts").fetchone()
        self.assertEqual(tuple(row), ("unknown", None))
        self.assertEqual(self.fake.new_count, 1)

    def test_reused_and_completed_session_reject_extra_tax_discount_fx_or_wrong_total(self):
        sid = self.checkout()
        session = self.fake.sessions[sid]
        changes = [(session, "automatic_tax", {"enabled": True}),
                   (session, "automatic_tax", None),
                   (session, "adaptive_pricing", {"enabled": True}),
                   (session, "allow_promotion_codes", True),
                   (session, "discounts", [{"coupon": "coupon_other"}]),
                   (session, "amount_total", 3300), (session, "amount_subtotal", 2999),
                   (session, "currency", "usd"),
                   (session, "presentment_details", {"presentment_currency": "usd", "presentment_amount": 20}),
                   (session["total_details"], "amount_tax", 300),
                   (session["total_details"], "amount_discount", 300),
                   (session["total_details"], "amount_shipping", 100)]
        absent = object()
        for obj, field, value in changes:
            with self.subTest(field=field, value=value):
                old = obj.get(field, absent); obj[field] = value
                self.problem(503, self.checkout)  # Reusing a saved link also re-reads Stripe.
                self.assertEqual(self.fake.new_count, 1)
                if old is absent: obj.pop(field)
                else: obj[field] = old
        self.fake.complete(sid)
        session["total_details"]["amount_discount"] = 1
        self.problem(503, lambda: billing.reconcile(self.c, self.user["id"]))
        self.assertFalse(core.paid(self.c, self.user["id"]))

    def test_subscription_and_item_tax_rates_discounts_and_fx_fail_closed(self):
        sid = self.checkout(); self.fake.complete(sid)
        sub = self.fake.subscriptions["sub_notta"]
        item = sub["items"]["data"][0]
        changes = [(sub, "automatic_tax", {"enabled": True}), (sub, "default_tax_rates", ["txr_extra"]),
                   (sub, "discounts", ["di_extra"]), (item, "tax_rates", ["txr_extra"]), (item, "discounts", ["di_extra"]),
                   (sub, "presentment_details", {"presentment_currency": "usd"})]
        absent = object()
        for obj, field, value in changes:
            with self.subTest(field=field):
                old = obj.get(field, absent); obj[field] = value
                self.problem(503, lambda: billing.reconcile(self.c, self.user["id"]))
                self.assertFalse(core.paid(self.c, self.user["id"]))
                if old is absent: obj.pop(field)
                else: obj[field] = old

    def test_invoice_tax_discount_shipping_and_fx_rejected_even_if_totals_cancel_out(self):
        sid = self.checkout(); self.fake.complete(sid)
        invoice = self.fake.bills["in_first"]
        line = invoice["lines"]["data"][0]
        changes = [(invoice, "automatic_tax", {"enabled": True}), (invoice, "default_tax_rates", ["txr_extra"]),
                   (invoice, "discounts", ["di_extra"]), (invoice, "total_taxes", [{"amount": 300}]),
                   (invoice, "total_discount_amounts", [{"amount": 300}]), (invoice, "amount_shipping", 100),
                   (line, "taxes", [{"amount": 300}]), (line, "discount_amounts", [{"amount": 300}]),
                   (line, "discounts", ["di_extra"]), (invoice, "subtotal", 2700),
                   (self.fake.intents["pi_in_first"], "presentment_details", {"presentment_currency": "usd", "presentment_amount": 20})]
        absent = object()
        for obj, field, value in changes:
            with self.subTest(field=field):
                old = obj.get(field, absent); obj[field] = value
                self.problem(503, lambda: billing.reconcile(self.c, self.user["id"]))
                self.assertFalse(core.paid(self.c, self.user["id"]))
                if old is absent: obj.pop(field)
                else: obj[field] = old

    def test_settled_payment_grants_exact_paid_period_and_is_idempotent(self):
        self.paid()
        result = billing.reconcile(self.c, self.user["id"])
        self.assertTrue(result["paid"])
        self.assertEqual(result["subscription"]["paid_until"], END)
        self.assertTrue(result["refundEligible"])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM svc_payments").fetchone()[0], 1)
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM svc_events WHERE event='payment_confirmed'").fetchone()[0], 1)
        self.assertFalse(billing.summary(self.c, self.other["id"])["paid"])

    def test_reconciliation_accepts_existing_payment_with_postgres_dict_row_shape(self):
        self.paid()
        inner = self.c
        class DictPaymentRows:
            def __getattr__(self, name): return getattr(inner, name)
            def execute(self, sql, params=()):
                cursor = inner.execute(sql, params)
                if sql.startswith("SELECT user_id,subscription_id,invoice_id FROM svc_payments"):
                    row = cursor.fetchone()
                    return SimpleNamespace(fetchone=lambda: HybridRow(dict(row)) if row else None)
                return cursor
        result = billing.reconcile(DictPaymentRows(), self.user["id"])
        self.assertTrue(result["paid"])
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM svc_payments").fetchone()[0], 1)
        self.c.execute("UPDATE svc_payments SET user_id=?", (self.other["id"],))
        self.c.commit()
        self.problem(409, lambda: billing.reconcile(DictPaymentRows(), self.user["id"]))

    def test_price_customer_invoice_payment_and_charge_mismatches_fail_closed(self):
        sid = self.checkout(); self.fake.complete(sid)
        fields = [(self.fake.subscriptions["sub_notta"]["items"]["data"][0], "price", {"id": "price_mobby"}),
                  (self.fake.bills["in_first"], "customer", "cus_other"),
                  (self.fake.bills["in_first"], "amount_paid", 2999),
                  (self.fake.payments["in_first"][0], "invoice", "in_other"),
                  (self.fake.intents["pi_in_first"], "customer", "cus_other"),
                  (self.fake.charges["ch_in_first"], "amount_captured", 0)]
        for obj, field, value in fields:
            with self.subTest(field=field):
                old = obj[field]; obj[field] = value
                self.problem(503, lambda: billing.reconcile(self.c, self.user["id"]))
                self.assertFalse(core.paid(self.c, self.user["id"]))
                obj[field] = old

    def test_session_metadata_cannot_be_swapped_to_other_user(self):
        sid = self.checkout(); self.fake.complete(sid)
        self.fake.sessions[sid]["metadata"]["user_id"] = self.other["id"]
        self.problem(503, lambda: billing.reconcile(self.c, self.user["id"]))
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM svc_subscriptions").fetchone()[0], 0)

    def test_provider_environment_mismatch_rejected(self):
        sid = self.checkout(); self.fake.complete(sid)
        self.fake.sessions[sid]["livemode"] = True
        self.problem(503, lambda: billing.reconcile(self.c, self.user["id"]))

    def test_only_verified_expired_session_releases_pending_capacity(self):
        sid = self.checkout()
        self.fake.sessions[sid]["status"] = "expired"
        result = billing.reconcile(self.c, self.user["id"])
        self.assertIsNone(result["pending"])
        self.assertEqual(self.c.execute("SELECT status FROM svc_checkouts").fetchone()[0], "expired")

    def test_paid_capacity_and_ai_budget_reserved_before_second_checkout(self):
        self.checkout()
        self.problem(429, lambda: billing.checkout(self.c, self.other, {"consent": True, "terms": core.TERMS}))
        self.assertEqual(self.fake.new_count, 1)

    def test_cancel_at_period_end_preserves_paid_access_and_deduplicates(self):
        self.paid()
        result = billing.cancel(self.c, self.user["id"], {"confirm": True})
        self.assertTrue(result["paid"])
        self.assertEqual(result["subscription"]["cancel_at"], END)
        billing.cancel(self.c, self.user["id"], {"confirm": True})
        self.assertEqual(self.fake.cancel_count, 1)
        self.clock.return_value = END
        self.assertFalse(core.paid(self.c, self.user["id"]))

    def test_failed_renewal_never_extends_access_then_settled_renewal_does(self):
        self.paid()
        self.fake.add_bill("in_second", END, END + 30 * 86400, paid=False)
        self.fake.subscriptions["sub_notta"]["status"] = "past_due"
        result = billing.reconcile(self.c, self.user["id"])
        self.assertEqual(result["subscription"]["paid_until"], END)
        self.fake.add_bill("in_second", END, END + 30 * 86400, paid=True)
        result = billing.reconcile(self.c, self.user["id"])
        self.assertEqual(result["subscription"]["paid_until"], END + 30 * 86400)

    def test_pending_refund_suspends_failed_refund_restores_access(self):
        self.paid(); self.fake.add_refund()
        self.assertFalse(billing.reconcile(self.c, self.user["id"])["paid"])
        self.fake.refund_objects["re_first"]["status"] = "failed"
        self.assertTrue(billing.reconcile(self.c, self.user["id"])["paid"])

    def test_failed_refund_does_not_ignore_other_pending_refund(self):
        self.paid(); self.fake.add_refund("re_failed", "failed", 1000); self.fake.add_refund("re_pending", "pending", 2000)
        self.assertFalse(billing.reconcile(self.c, self.user["id"])["paid"])
        self.fake.refund_objects["re_pending"]["status"] = "failed"
        self.assertTrue(billing.reconcile(self.c, self.user["id"])["paid"])

    def test_full_refund_completes_request_and_late_paid_event_does_not_restore(self):
        self.paid()
        billing.request_refund(self.c, self.user["id"], {"confirm": True})
        self.fake.add_refund(status="succeeded")
        self.assertFalse(billing.reconcile(self.c, self.user["id"])["paid"])
        self.assertEqual(billing.summary(self.c, self.user["id"])["refundRequest"]["status"], "completed")
        self.fake.no_refunds_in_list = True
        self.fake.refund_objects["re_first"]["status"] = "pending"
        self.assertFalse(billing.reconcile(self.c, self.user["id"])["paid"])
        self.assertEqual(self.c.execute("SELECT status FROM svc_refunds").fetchone()[0], "COMPLETED")

    def test_refund_notice_saved_once_even_when_cancellation_fails(self):
        self.paid()
        with patch.object(billing, "cancel", side_effect=providers.ProviderError("outage")):
            self.assertTrue(billing.request_refund(self.c, self.user["id"], {"confirm": True})["needsReview"])
            billing.request_refund(self.c, self.user["id"], {"confirm": True})
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM svc_refund_requests").fetchone()[0], 1)
        self.clock.return_value = NOW + 7 * 86400 + 1
        self.problem(409, lambda: billing.request_refund(self.c, self.user["id"], {"confirm": True}))

    def test_webhook_signature_expiry_environment_duplicate_and_payload_not_ownership(self):
        self.paid(); event = self.event()
        raw, signature = self.signed(event)
        with patch.object(billing, "reconcile", wraps=billing.reconcile) as reconcile:
            self.assertTrue(billing.webhook(self.c, raw, signature)["ok"])
            self.assertEqual([call.args[1] for call in reconcile.call_args_list], [self.user["id"]])
            self.assertTrue(billing.webhook(self.c, raw, signature)["duplicate"])
            self.assertEqual(reconcile.call_count, 1)
        self.problem(403, lambda: billing.webhook(self.c, raw, "t=" + str(NOW) + ",v1=wrong"))
        stale, stale_sig = self.signed(event, NOW - 301)
        self.problem(403, lambda: billing.webhook(self.c, stale, stale_sig))
        event.update(id="evt_other", livemode=True)
        self.problem(403, lambda: billing.webhook(self.c, *self.signed(event)))
        event.update(livemode=False, account="acct_other")
        self.problem(403, lambda: billing.webhook(self.c, *self.signed(event)))

    def test_webhook_out_of_order_event_reads_current_refund(self):
        self.paid(); self.fake.add_refund(status="succeeded")
        event = self.event("invoice.paid"); event["created"] = NOW - 86400 * 3
        billing.webhook(self.c, *self.signed(event))
        self.assertFalse(core.paid(self.c, self.user["id"]))

    def test_invalid_checkout_url_is_not_returned_and_retains_unknown(self):
        original = self.fake.checkout
        def unsafe(key, user):
            result = original(key, user); result["url"] = "https://checkout.stripe.com.attacker.test/pay"
            return result
        self.fake.checkout = unsafe
        self.problem(502, self.checkout)
        self.assertEqual(self.c.execute("SELECT status FROM svc_checkouts").fetchone()[0], "unknown")


@unittest.skipUnless(HAS_SDK, "Stripe SDK is optional for the local disabled-provider workflow")
class StripeSDKBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"STRIPE_ENVIRONMENT": "sandbox", "STRIPE_RESTRICTED_KEY": "rk_test_fixture",
            "STRIPE_ACCOUNT_ID": "acct_test", "STRIPE_PRICE_ID": "price_notta", "NOTTA_PUBLIC_ORIGIN": "https://notta.example"})
        self.env.start()
        # Replace the transport rather than the Stripe SDK: verify real SDK
        # serialization, endpoint naming, API-version and idempotency headers.
        self.transport_patch = patch.object(stripe_sdk.RequestsClient, "request")
        self.transport = self.transport_patch.start()
        self.provider = billing.Stripe()

    def tearDown(self):
        self.transport_patch.stop(); self.env.stop()

    @staticmethod
    def response(value, status=200):
        return json.dumps(value), status, {"content-type": "application/json"}

    def test_real_sdk_serializes_hosted_monthly_checkout_without_card_trial_or_tax(self):
        self.transport.return_value = self.response({"id": "cs_test_sdk", "object": "checkout.session", "livemode": False})
        self.provider.checkout("local-checkout", {"id": "owner", "email": "owner@example.test"})
        args = self.transport.call_args.args
        self.assertEqual(args[0:2], ("post", "https://api.stripe.com/v1/checkout/sessions"))
        headers, encoded = args[2], args[3]
        self.assertEqual(headers["Stripe-Version"], billing.API_VERSION)
        self.assertEqual(headers["Idempotency-Key"], "notta-checkout-local-checkout")
        params = parse_qs(encoded)
        self.assertEqual(params["mode"], ["subscription"])
        self.assertEqual(params["line_items[0][price]"], ["price_notta"])
        self.assertEqual(params["subscription_data[billing_mode][type]"], ["flexible"])
        self.assertEqual(params["success_url"], ["https://notta.example/billing/return"])
        self.assertEqual(params["cancel_url"], ["https://notta.example/workspace"])
        self.assertRegex(params["integration_identifier"][0], r"^notta_monthly_[a-z]{8}$")
        self.assertNotIn("payment_method_types", encoded)
        self.assertNotIn("trial", encoded)
        self.assertEqual(params["automatic_tax[enabled]"], ["false"])
        self.assertEqual(params["allow_promotion_codes"], ["false"])
        self.assertEqual(params["adaptive_pricing[enabled]"], ["false"])
        self.assertNotIn("tax_rates", encoded)
        self.assertNotIn("discounts", encoded)

    def test_current_account_and_exact_inclusive_price_are_required(self):
        price = {"id": "price_notta", "object": "price", "livemode": False, "active": True, "currency": "jpy", "unit_amount": 3000,
                 "type": "recurring", "billing_scheme": "per_unit", "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"}, "tax_behavior": "inclusive"}
        self.transport.side_effect = [self.response({"id": "acct_test", "object": "account"}), self.response(price)]
        self.provider.validate()
        self.assertEqual(self.transport.call_args_list[0].args[1], "https://api.stripe.com/v1/account")
        price["unit_amount"] = 3001
        self.transport.side_effect = [self.response({"id": "acct_test", "object": "account"}), self.response(price)]
        with self.assertRaises(s.Problem): self.provider.validate()

    def test_unspecified_or_inclusive_price_allowed_exclusive_unknown_and_missing_rejected(self):
        price = {"id": "price_notta", "object": "price", "livemode": False, "active": True, "currency": "jpy", "unit_amount": 3000,
                 "type": "recurring", "billing_scheme": "per_unit", "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"}}
        for behavior in ("inclusive", "unspecified", "exclusive", "unknown", None):
            with self.subTest(behavior=behavior):
                price["tax_behavior"] = behavior
                self.transport.side_effect = [self.response({"id": "acct_test", "object": "account"}), self.response(price)]
                if behavior in ("inclusive", "unspecified"):
                    self.provider.validate()
                else:
                    with self.assertRaises(s.Problem): self.provider.validate()
        self.transport.side_effect = [self.response({"id": "acct_wrong", "object": "account"})]
        with self.assertRaises(s.Problem): self.provider.validate()

    def test_invoice_payment_mapping_uses_new_invoice_payments_endpoint(self):
        self.transport.return_value = self.response({"object": "list", "data": [], "has_more": False})
        self.assertEqual(self.provider.invoice_payments("in_sdk"), [])
        args = self.transport.call_args.args
        self.assertIn("/v1/invoice_payments?", args[1])
        self.assertIn("invoice=in_sdk", args[1])
        self.assertIn("status=paid", args[1])

    def test_invalid_local_origin_is_definitive_and_makes_no_checkout_request(self):
        with patch.dict(os.environ, {"NOTTA_PUBLIC_ORIGIN": "https://notta.example/unsafe/path"}):
            with self.assertRaises(providers.ProviderError) as caught:
                self.provider.checkout("bad-origin", {"id": "owner", "email": "owner@example.test"})
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(self.transport.call_count, 0)

    def test_sdk_errors_never_expose_provider_secrets_or_enable_automatic_retry(self):
        for status, uncertain in ((400, False), (429, True), (500, True)):
            with self.subTest(status=status):
                self.transport.return_value = self.response({"error": {"type": "invalid_request_error", "message": "sensitive_value_fixture"}}, status)
                self.transport.reset_mock()
                with self.assertRaises(providers.ProviderError) as caught:
                    self.provider.checkout("sdk-fail", {"id": "owner", "email": "owner@example.test"})
                self.assertNotIn("sensitive_value_fixture", str(caught.exception))
                self.assertEqual(caught.exception.uncertain, uncertain)
                self.assertEqual(self.transport.call_count, 1)


if __name__ == "__main__":
    unittest.main()
