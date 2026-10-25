"""An immutable provisioning intent and its charge commit in one SQLite transaction.

The external API can time out after accepting a write. Reconciliation therefore
uses the saved identity and exact expiry; it never recomputes or charges again.
"""
import asyncio
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
import json
import logging
import secrets
import time

from config import db, FULL_TARIFF_PRICE, ADMIN_ID
from .billing_db import BillingDB
from .subscription_delivery import queue_receipt

request_key = ContextVar('subscription_request_key', default=None)
logger = logging.getLogger(__name__)

class SubscriptionPending(RuntimeError):
    pass

def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS subscription_operations (
        operation_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
        tg_id INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
        plan TEXT NOT NULL, result TEXT, attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at REAL NOT NULL DEFAULT 0, last_error TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL)''')
    c.execute('CREATE INDEX IF NOT EXISTS provisioning_due ON subscription_operations(status,next_attempt_at)')
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS provisioning_one_pending ON subscription_operations(tg_id) WHERE status!='ready'")

def operation_stage(key, fallback=None):
    with closing(db._connect()) as c:
        schema(c)
        r = c.execute('SELECT status FROM subscription_operations WHERE request_key=?', (key,)).fetchone()
        c.commit()
    return r[0] if r else ('ready' if fallback == 'succeeded' else 'checking')

def request_row(key):
    with closing(db._connect()) as c:
        schema(c)
        c.row_factory = __import__('sqlite3').Row
        row = c.execute('SELECT * FROM subscription_operations WHERE request_key=?', (key,)).fetchone()
        c.commit()
        return dict(row) if row else None

async def reconcile(row):
    from .subscription_service import receipt_text
    plan = json.loads(row['plan'])
    try:
        with closing(db._connect()) as c, c:
            c.execute("UPDATE subscription_operations SET status='provisioning',updated_at=? WHERE operation_id=?", (time.time(), row['operation_id']))
        clients, expiry = await BillingDB.create_clients_for_user(plan['sub_id'], plan['username'], plan['expires_ms'])
        if abs(expiry - plan['expires_ms']) > 1500:
            raise RuntimeError('Expiry confirmation mismatch')
        user = plan['user']
        expires = datetime.fromtimestamp(plan['expires_ms'] / 1000, timezone.utc)
        result = {'user': {**user, 'sub_id': plan['sub_id'], 'username': plan['username'],
            'key_created_at': plan['created_iso'], 'key_expires_at': plan['expires_iso'],
            'subscription_started_at': plan['created_iso'], 'subscription_expires_at': plan['expires_iso'],
            'tariff': plan['tariff'], 'free_used': plan['free_used'], 'balance_rub': plan['balance']},
            'receipt_id': None, 'first_sub_ever': not bool(user.get('sub_id')),
            'first_paid_activation': not bool(user.get('sub_id') or user.get('key_expires_at'))}
        with closing(db._connect()) as c:
            c.execute('BEGIN IMMEDIATE')
            # Fenced by the same cross-process flock as creation and every retry.
            if c.execute('SELECT status FROM subscription_operations WHERE operation_id=?', (row['operation_id'],)).fetchone()[0] == 'ready':
                return json.loads(c.execute('SELECT result FROM subscription_operations WHERE operation_id=?', (row['operation_id'],)).fetchone()[0])
            db.upsert_after_create(tg_id=row['tg_id'], tg_name=plan['tg_name'], username=plan['username'],
                sub_id=plan['sub_id'], sub_name=plan['sub_id'], key_created_at=plan['created_iso'],
                key_expires_at=plan['expires_iso'], client_ids=clients, tariff=plan['tariff'],
                free_used=plan['free_used'], connection=c)
            if plan['chat_id'] == ADMIN_ID:
                result['receipt_id'] = queue_receipt(c, plan['chat_id'], receipt_text(row['kind'], plan['is_extension'], expires, plan['sub_id'], plan['balance']))
            c.execute("UPDATE subscription_operations SET status='ready',result=?,last_error=NULL,updated_at=? WHERE operation_id=?", (json.dumps(result), time.time(), row['operation_id']))
            if row['request_key'].startswith('web:'):
                c.execute("UPDATE web_account_jobs SET status='succeeded',error=NULL,updated_at=CURRENT_TIMESTAMP,finished_at=CURRENT_TIMESTAMP WHERE job_id=?", (row['request_key'][4:],))
            c.commit()
        return result
    except Exception as error:
        attempts = row['attempts'] + 1
        with closing(db._connect()) as c, c:
            c.execute("UPDATE subscription_operations SET status='retrying',attempts=?,next_attempt_at=?,last_error=?,updated_at=? WHERE operation_id=? AND status!='ready'",
                (attempts, time.time() + min(3600, 10 * 2 ** min(attempts, 8)), type(error).__name__, time.time(), row['operation_id']))
        logger.warning('Provisioning deferred operation=%s error=%s', row['operation_id'], type(error).__name__)
        raise SubscriptionPending('subscription_pending') from None

async def activate_subscription(*, tg_id, tg_name, username, kind, chat_id=None):
    from .subscription_service import expiry_datetime
    if kind not in {'trial', 'premium', 'referral'}:
        raise ValueError('invalid_tariff')
    key = request_key.get() or 'internal:' + secrets.token_urlsafe(24)
    if key.startswith('telegram:'):
        key += ':' + str(tg_id) + ':' + kind
    async with BillingDB.subscription_update_lock('provision:' + str(tg_id)):
        old = request_row(key)
        if old:
            if old['tg_id'] != tg_id or old['kind'] != kind:
                raise ValueError('permission_denied')
            return json.loads(old['result']) if old['status'] == 'ready' else await reconcile(old)
        BillingDB.ensure_user_row(tg_id, tg_name, username)
        user = db.get_user(tg_id) or {}
        if user.get('is_blocked'):
            raise ValueError('permission_denied')
        now = datetime.now(timezone.utc)
        current = expiry_datetime(user)
        active = str(user.get('tariff') or '').lower() == 'premium' and current and current > now
        if kind == 'trial':
            if active: raise ValueError('premium_active')
            if user.get('free_used'): raise ValueError('trial_used')
        base = max(now, current) if current and (active or kind == 'referral') else now
        expires = base + (timedelta(hours=24) if kind == 'trial' else timedelta(days=30))
        plan = {'user': user, 'tg_name': tg_name, 'username': user.get('username') or username,
            'sub_id': user.get('sub_id') or BillingDB.generate_sub_id(tg_id, tg_name, username),
            'expires_ms': int(expires.timestamp() * 1000), 'expires_iso': expires.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'created_iso': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'tariff': 'free' if kind == 'trial' else 'premium',
            'free_used': 1 if kind == 'trial' else int(user.get('free_used') or 0),
            'is_extension': bool(active), 'chat_id': chat_id if chat_id is not None else tg_id}
        with closing(db._connect()) as c:
            c.execute('BEGIN IMMEDIATE')
            schema(c)
            c.row_factory = __import__('sqlite3').Row
            pending = c.execute("SELECT * FROM subscription_operations WHERE tg_id=? AND status!='ready'", (tg_id,)).fetchone()
            if pending:
                c.rollback()
                raise SubscriptionPending('subscription_pending')
            if kind == 'premium':
                cur = c.execute('UPDATE users SET balance_rub=COALESCE(balance_rub,0)-? WHERE tg_id=? AND COALESCE(balance_rub,0)>=?', (FULL_TARIFF_PRICE, tg_id, FULL_TARIFF_PRICE))
                if not cur.rowcount:
                    raise ValueError('insufficient_balance')
            plan['balance'] = c.execute('SELECT COALESCE(balance_rub,0) FROM users WHERE tg_id=?', (tg_id,)).fetchone()[0]
            op = secrets.token_urlsafe(24)
            c.execute('INSERT INTO subscription_operations(operation_id,request_key,tg_id,kind,status,plan,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
                (op, key, tg_id, kind, 'payment_confirmed', json.dumps(plan), time.time(), time.time()))
            c.commit()
        return await reconcile(request_row(key))

async def retry_panel_sync(context=None):
    with closing(db._connect()) as c, c:
        schema(c)
        keys = [r[0] for r in c.execute("SELECT request_key FROM subscription_operations WHERE status!='ready' AND next_attempt_at<=? ORDER BY created_at LIMIT 5", (time.time(),))]
    for key in keys:
        async with BillingDB.subscription_update_lock('provision-retry'):
            row = request_row(key)
            if row['status'] == 'ready' or row['next_attempt_at'] > time.time(): continue
            try:
                await reconcile(row)
            except SubscriptionPending:
                pass
