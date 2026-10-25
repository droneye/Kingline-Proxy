"""Consistent read-only business snapshot; technical logs and live sessions excluded."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

SKIP = {'web_sessions', 'web_admin_2fa_challenges', 'web_email_link_requests',
        'web_email_verifications', 'web_link_codes', 'web_login_attempts', 'web_magic_links',
        'web_password_resets', 'web_pending_registrations', 'web_support_typing',
        'subscription_delivery_outbox', 'subscription_panel_sync', 'web_account_jobs',
        'web_telegram_link_reminders'}
result = {'files': {}, 'counts': {}}
for name, path in [('vpn_bot.db', '/root/kingline/vpn_bot.db'), ('ref.db', '/root/kingline/ref.db'),
                   ('promos.db', '/root/kingline/promos.db'), ('site-ref.db', '/root/kingline_site/ref.db')]:
    if not Path(path).exists():
        continue
    source = sqlite3.connect('file:' + path + '?mode=ro', uri=True, timeout=10)
    source.execute('PRAGMA query_only=ON')
    source.execute('BEGIN')
    dest = sqlite3.connect(':memory:')
    tables = source.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    for table, sql in tables:
        dest.execute(sql)
        if table in SKIP or table.startswith('web_xui_'):
            continue
        quoted = '"' + table.replace('"', '""') + '"'
        cursor = source.execute('SELECT * FROM ' + quoted)
        placeholders = ','.join('?' for _ in cursor.description)
        count = 0
        while rows := cursor.fetchmany(1000):
            dest.executemany('INSERT INTO ' + quoted + ' VALUES (' + placeholders + ')', rows)
            count += len(rows)
        result['counts'][name + ':' + table] = count
    for sql, in source.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"):
        dest.execute(sql)
    dest.commit()
    assert dest.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    content = dest.serialize()
    result['files'][name] = {'sha256': hashlib.sha256(content).hexdigest(),
                             'bytes': len(content), 'data': base64.b64encode(content).decode()}
    source.rollback()
    source.close()
    dest.close()
sys.stdout.buffer.write(gzip.compress(json.dumps(result).encode()))
