"""Transactional order/event/entitlement ledger. No provider requests or live route.

Callers must supply an authenticated buyer and server-approved immutable price.
Use a dedicated connection; this module owns BEGIN/commit/rollback for delivery.
"""
import hashlib,json,secrets,time
import payments
class Rejected(Exception):pass
def schema(c):
 c.executescript('''CREATE TABLE IF NOT EXISTS payment_orders(id TEXT PRIMARY KEY,buyer TEXT NOT NULL REFERENCES users(id),provider TEXT NOT NULL,product TEXT NOT NULL,amount INTEGER NOT NULL CHECK(amount>0),currency TEXT NOT NULL,merchant TEXT NOT NULL,location TEXT NOT NULL,idempotency TEXT UNIQUE NOT NULL,provider_order TEXT UNIQUE,state TEXT NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS payment_events(event TEXT PRIMARY KEY,payment TEXT NOT NULL UNIQUE,order_id TEXT NOT NULL UNIQUE REFERENCES payment_orders(id),body_hash TEXT NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS entitlements(order_id TEXT PRIMARY KEY REFERENCES payment_orders(id),buyer TEXT NOT NULL REFERENCES users(id),product TEXT NOT NULL,payment TEXT UNIQUE NOT NULL,state TEXT NOT NULL,created INTEGER NOT NULL);''')
def prepare_order(c,buyer,price,idempotency_key):
 # buyer is the trusted server session id; never accepted from checkout browser data.
 if not c.execute('SELECT 1 FROM users WHERE id=?',(buyer,)).fetchone():raise Rejected('Authenticated buyer required')
 if not isinstance(price,dict)or set(price)!={'provider','product','amount','currency','merchant','location'}:raise Rejected('Approved server price required')
 if price['provider']not in payments.PROVIDERS or type(price['amount'])is not int or price['amount']<=0 or price['currency']!='JPY'or not all(isinstance(price[k],str)and 1<=len(price[k])<=150 for k in ['product','merchant','location']):raise Rejected('Invalid server price')
 if not isinstance(idempotency_key,str)or not 16<=len(idempotency_key)<=100:raise Rejected('Invalid idempotency key')
 c.execute('BEGIN IMMEDIATE')
 old=c.execute('SELECT * FROM payment_orders WHERE idempotency=?',(idempotency_key,)).fetchone()
 if old:
  if old['buyer']!=buyer or any(old[k]!=price[v]for k,v in [('provider','provider'),('product','product'),('amount','amount'),('currency','currency'),('merchant','merchant'),('location','location')]):raise Rejected('Idempotency conflict')
  return old['id']
 oid=secrets.token_hex(12);c.execute('INSERT INTO payment_orders(id,buyer,provider,product,amount,currency,merchant,location,idempotency,state,created)VALUES(?,?,?,?,?,?,?,?,?,?,?)',(oid,buyer,price['provider'],price['product'],price['amount'],price['currency'],price['merchant'],price['location'],idempotency_key,'prepared',int(time.time())));return oid
def bind_provider_order(c,oid,provider_order):
 # Only a future trusted provider adapter may call this, never a return URL.
 if not isinstance(provider_order,str)or not 1<=len(provider_order)<=150:raise Rejected('Invalid provider order')
 changed=c.execute("UPDATE payment_orders SET provider_order=?,state='awaiting' WHERE id=?AND state='prepared'AND provider_order IS NULL",(provider_order,oid)).rowcount
 if changed!=1:raise Rejected('Order already bound or unavailable')
def deliver_square(c,raw_body,signature,*,notification_url,signature_key):
 if not payments.verify_square_signature(raw_body,signature,notification_url,signature_key):raise Rejected('Invalid signature')
 try:e=json.loads(raw_body);p=e['data']['object']['payment'];provider_order=p['order_id']
 except (ValueError,KeyError,TypeError):raise Rejected('Invalid event')from None
 try:
  c.execute('BEGIN IMMEDIATE')
  order=c.execute('SELECT * FROM payment_orders WHERE provider_order=?',(provider_order,)).fetchone()
  if not order or order['provider']!='square':raise Rejected('Unknown order')
  expected={'buyer_id':order['buyer'],'order_id':order['provider_order'],'amount':order['amount'],'currency':order['currency'],'merchant_id':order['merchant'],'location_id':order['location']}
  if not payments.square_completion(raw_body,signature,notification_url=notification_url,signature_key=signature_key,order=expected):raise Rejected('Unmatched completed payment')
  if not all(isinstance(v,str)and 1<=len(v)<=150 for v in [e['event_id'],p['id']]):raise Rejected('Invalid event identity')
  previous=c.execute('SELECT * FROM payment_events WHERE event=? OR payment=? OR order_id=?',(e['event_id'],p['id'],order['id'])).fetchall()
  if previous:
   if len(previous)!=1 or previous[0]['payment']!=p['id']or previous[0]['order_id']!=order['id']:raise Rejected('Payment identity conflict')
   grant=c.execute('SELECT * FROM entitlements WHERE order_id=?',(order['id'],)).fetchone()
   if not grant or grant['state']!='active'or order['state']!='paid':raise Rejected('Ledger inconsistent')
   c.commit();return {'duplicate':True,'granted':False,'order':order['id']}
  if order['state']!='awaiting':raise Rejected('Order not awaiting payment')
  now=int(time.time());c.execute('INSERT INTO payment_events(event,payment,order_id,body_hash,created)VALUES(?,?,?,?,?)',(e['event_id'],p['id'],order['id'],hashlib.sha256(raw_body).hexdigest(),now))
  c.execute('INSERT INTO entitlements(order_id,buyer,product,payment,state,created)VALUES(?,?,?,?,?,?)',(order['id'],order['buyer'],order['product'],p['id'],'active',now))
  if c.execute("UPDATE payment_orders SET state='paid' WHERE id=?AND state='awaiting'",(order['id'],)).rowcount!=1:raise Rejected('Concurrent order change')
  c.commit();return {'duplicate':False,'granted':True,'order':order['id']}
 except Exception:
  c.rollback();raise
