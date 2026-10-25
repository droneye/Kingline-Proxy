"""Explicit sandbox ledger; never calls or impersonates a payment provider."""
import json
import os
import secrets
import sqlite3
from storage import connect as connect_sqlite

def connection():
    c = connect_sqlite(os.environ['DB_PATH'], timeout=15)
    c.execute('''CREATE TABLE IF NOT EXISTS sandbox_payments(
        id TEXT PRIMARY KEY, tg_id INTEGER NOT NULL, amount INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending', created_at TEXT DEFAULT CURRENT_TIMESTAMP)''')
    return c

class TestPaymentGateway:
    def __init__(self, *args, **kwargs): pass
    async def create_payment(self, amount, description, tg_id, currency='RUB', return_url=None):
        if str(tg_id) not in os.environ.get('SANDBOX_ALLOWED_TG_IDS','').split(','):
            raise ValueError('sandbox_payments_restricted')
        if currency != 'RUB' or amount not in (300, 600, 900): raise ValueError('invalid_amount')
        pid = 'stg_' + secrets.token_urlsafe(24)
        with connection() as c:
            c.execute('INSERT INTO sandbox_payments(id,tg_id,amount) VALUES(?,?,?)', (pid, tg_id, amount))
        return os.environ['KINGLINE_SITE_URL'] + '/test-payment/' + pid, pid
    async def get_payment(self, payment_id):
        with connection() as c:
            row = c.execute('SELECT id,tg_id,amount,status FROM sandbox_payments WHERE id=?', (payment_id,)).fetchone()
        if not row: raise ValueError('payment_not_found')
        return {'id': row[0], 'metadata': {'tg_id': row[1]}, 'amount': {'value': f'{row[2]}.00', 'currency': 'RUB'}, 'status': row[3], 'paid': row[3] == 'succeeded', 'test': True}
