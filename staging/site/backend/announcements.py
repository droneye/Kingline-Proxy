"""One service announcement, delivered with existing subscription responses."""
from datetime import datetime, timezone
import sqlite3
import unicodedata
from urllib.parse import urlparse

import server

PATH = '/api/admin/messages/announcement'
MAX_LENGTH = 500


def schema(connection):
    connection.execute('''CREATE TABLE IF NOT EXISTS subscription_announcement (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        text TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL)''')


def read(connection):
    row = connection.execute('SELECT text,updated_at,revision FROM subscription_announcement WHERE singleton=1').fetchone()
    return {'text': row[0] if row else '', 'updatedAt': row[1] if row else None,
            'revision': row[2] if row else 0, 'managed': bool(row), 'maxLength': MAX_LENGTH}


def save(connection, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('text'), str):
        raise ValueError('invalid_announcement')
    text = payload['text'].replace('\r\n', '\n').replace('\r', '\n').strip()
    if len(text) > MAX_LENGTH or any(unicodedata.category(c) == 'Cc' and c not in '\n\t' for c in text):
        raise ValueError('invalid_announcement')
    revision = payload.get('revision')
    if type(revision) is not int or revision < 0:
        raise ValueError('invalid_announcement')
    connection.execute('BEGIN IMMEDIATE')
    try:
        current = read(connection)
        if current['revision'] != revision:
            raise ValueError('announcement_changed')
        now = datetime.now(timezone.utc).isoformat()
        connection.execute('''INSERT INTO subscription_announcement VALUES(1,?,?,?)
            ON CONFLICT(singleton) DO UPDATE SET text=excluded.text,
            updated_at=excluded.updated_at,revision=excluded.revision''', (text, now, revision + 1))
        result = read(connection)
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise


def handle(handler, method='GET'):
    if urlparse(handler.path).path != PATH:
        return False
    if method == 'POST' and handler.validate_post_request(PATH) is not True:
        return True
    account = handler.require_admin_permission('broadcast')
    if not account:
        return True
    try:
        with server.connect() as connection:
            result = save(connection, handler.read_json()) if method == 'POST' else read(connection)
        if method == 'POST':
            server.log_admin_action(account, 'subscription_announcement_change', target='subscription',
                summary={'enabled': bool(result['text']), 'length': len(result['text']), 'revision': result['revision']},
                client_ip=handler.client_ip(), user_agent=handler.headers.get('User-Agent', ''))
        handler.json_response({'ok': True, 'announcement': result})
    except ValueError as error:
        code = str(error) if str(error) == 'announcement_changed' else 'invalid_announcement'
        handler.json_response({'ok': False, 'error': code}, 409 if code == 'announcement_changed' else 400)
    except sqlite3.Error as error:
        from diagnostics import event
        event('subscription_announcement_failed', 'ERROR', error=type(error).__name__)
        handler.json_response({'ok': False, 'error': 'operation_failed'}, 503)
    return True
