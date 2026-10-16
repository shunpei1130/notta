"""Provider-neutral payment boundary. Disabled until explicit production setup.

No HTTP client, secrets lookup, checkout generation or entitlement writes here.
Only authenticated, server-priced orders may enter a future adapter.
"""
import base64,hashlib,hmac,json
class PaymentBlocked(Exception):pass
PROVIDERS=('square','stripe')
def checkout(provider,*,buyer_id,price_id,idempotency_key):
 if provider not in PROVIDERS:raise PaymentBlocked('Unknown provider')
 raise PaymentBlocked('Price/refund terms and authenticated production provider are not configured')
def verify_square_signature(raw_body,signature,notification_url,signature_key):
 if not all(isinstance(v,str)and v for v in [signature,notification_url,signature_key]):return False
 if not isinstance(raw_body,bytes):return False
 expected=base64.b64encode(hmac.new(signature_key.encode(),notification_url.encode()+raw_body,hashlib.sha256).digest()).decode()
 return hmac.compare_digest(expected,signature)
def square_completion(raw_body,signature,*,notification_url,signature_key,order):
 """Pure predicate only; caller must deduplicate event/payment in a DB transaction.

No return URL can call this. Provider retrieval and production acceptance untested.
"""
 if not verify_square_signature(raw_body,signature,notification_url,signature_key):return False
 try:
  e=json.loads(raw_body);p=e['data']['object']['payment'];m=p['amount_money']
  return bool(e['type']=='payment.updated'and e.get('event_id')and p.get('id')and p['status']=='COMPLETED'and order['buyer_id']and order['order_id']==p['order_id']and type(m['amount'])is int and m['amount']==order['amount']and m['currency']==order['currency']and p['location_id']==order['location_id']and e['merchant_id']==order['merchant_id'])
 except (ValueError,KeyError,TypeError):return False
