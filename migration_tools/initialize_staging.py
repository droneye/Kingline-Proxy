"""Executed inside the staging container once, before public tests."""
from datetime import datetime, timezone, timedelta
import json
import os
import sqlite3
import server
from remnawave import Remnawave

server.init_db()
access = json.load(open('/tmp/test-access.json'))
with server.connect() as c:
    c.execute('''INSERT OR IGNORE INTO web_accounts(email,name,password_hash,email_verified,web_tg_id)
        VALUES(?,?,?,1,-900000001)''', (access['test_email'], 'Тестовый владелец', server.hash_password(access['test_password'])))
    c.execute('INSERT OR IGNORE INTO web_admins(email,is_owner) VALUES(?,1)', (access['test_email'],))
    c.commit()
    account = c.execute('SELECT ' + server.account_select() + ' FROM web_accounts WHERE email=?', (access['test_email'],)).fetchone()
    users = c.execute('SELECT sub_id,username,key_expires_at,subscription_expires_at,tg_id FROM users WHERE sub_id IS NOT NULL AND sub_id!=""').fetchall()
server.ensure_web_user(account)
native = Remnawave()
copied = 0
for row in users:
    value = row['key_expires_at'] or row['subscription_expires_at']
    try:
        expiry = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        if expiry.tzinfo is None: expiry = expiry.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        expiry = datetime.now(timezone.utc) - timedelta(days=1)
    # Copies never become active for real customers during acceptance testing.
    native.ensure(row['sub_id'], row['username'] or 'legacy', int(expiry.timestamp()*1000), enabled=False)
    copied += 1
print(json.dumps({'legacy_subscription_mappings': copied, 'test_account_ready': True, 'customers_enabled': 0}))
