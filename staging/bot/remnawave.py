"""Native Remnawave v3 integration; all writes restricted to mapped staging users."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request
import uuid


@contextmanager
def connection():
    c = sqlite3.connect(os.environ['DB_PATH'], timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=15000')
    c.execute('''CREATE TABLE IF NOT EXISTS remna_links (
        sub_id TEXT PRIMARY KEY, remna_id INTEGER UNIQUE NOT NULL, username TEXT UNIQUE NOT NULL,
        short_uuid TEXT NOT NULL, vless_uuid TEXT NOT NULL, updated_at REAL NOT NULL)''')
    try:
        with c:
            yield c
    finally:
        c.close()


class Remnawave:
    def __init__(self):
        self.base = os.getenv('REMNA_API_URL', 'http://remnawave:3000/api').rstrip('/')
        self.token = Path(os.environ['REMNA_TOKEN_FILE']).read_text().strip()
        self.squad = os.environ['REMNA_SQUAD_UUID']

    def call(self, path, method='GET', body=None):
        request = urllib.request.Request(self.base + '/' + path.lstrip('/'), method=method,
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.token,
                     'X-Forwarded-Proto': 'https', 'X-Forwarded-For': '127.0.0.1'},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=8) as r:
                content = r.read()
            return json.loads(content).get('response') if content else None
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            raise RuntimeError('Remnawave HTTP ' + str(e.code)) from None
        except (OSError, ValueError):
            raise RuntimeError('Remnawave temporarily unavailable') from None

    def users(self):
        users, start = [], 0
        while True:
            response = self.call('users?start=' + str(start) + '&size=100') or {}
            rows = response.get('users', [])
            users.extend(u for u in rows if u['username'].startswith('stg_') and u.get('tag') == 'STAGING')
            start += len(rows)
            if not rows or start >= response.get('total', start): return users

    def find(self, sub_id):
        with connection() as c:
            row = c.execute('SELECT * FROM remna_links WHERE sub_id=?', (sub_id,)).fetchone()
        if not row: return None
        user = self.call('users/' + str(row['remna_id']))
        if user and (user['username'] != row['username'] or user.get('tag') != 'STAGING'):
            raise RuntimeError('Remnawave ownership mismatch')
        return user

    def ensure(self, sub_id, username, expires_ms, *, enabled=True, client_id=None):
        if not sub_id or not expires_ms: raise ValueError('Missing subscription identity or expiry')
        stable_name = 'stg_' + hashlib.sha256(sub_id.encode()).hexdigest()[:20]
        user = self.find(sub_id) or self.call('users/by-username/' + stable_name)
        if user and (user['username'] != stable_name or user.get('tag') != 'STAGING'):
            raise RuntimeError('Remnawave ownership mismatch')
        values = {'status': 'ACTIVE' if enabled else 'DISABLED',
            'expireAt': datetime.fromtimestamp(expires_ms / 1000, timezone.utc).isoformat(),
            'trafficLimitBytes': 0, 'trafficLimitStrategy': 'NO_RESET', 'activeInternalSquads': [self.squad], 'hwidDeviceLimit': 3}
        expired = expires_ms <= int(time.time() * 1000)
        if expired:
            values['status'] = 'DISABLED'
        if user:
            # v3 permits importing past dates on create, but rejects them on PATCH.
            # The original historical expiry remains authoritative in our ledger.
            if expired: values.pop('expireAt')
            user = self.call('users', 'PATCH', {'id': user['id'], **values})
        else:
            cid = client_id or str(uuid.uuid5(uuid.NAMESPACE_URL, 'kingline-staging:' + sub_id))
            user = self.call('users', 'POST', {'username': stable_name, 'tag': 'STAGING',
                'vlessUuid': cid, 'description': 'Staging copy of legacy subscription', **values})
        assert user and user['username'] == stable_name
        with connection() as c:
            c.execute('''INSERT INTO remna_links VALUES (?,?,?,?,?,?) ON CONFLICT(sub_id)
                DO UPDATE SET remna_id=excluded.remna_id,username=excluded.username,
                short_uuid=excluded.short_uuid,vless_uuid=excluded.vless_uuid,updated_at=excluded.updated_at''',
                (sub_id, user['id'], stable_name, user['shortUuid'], user['vlessUuid'], time.time()))
        return user

    def disable(self, sub_id, enabled=False):
        user = self.find(sub_id)
        if not user: return 0
        self.call('users', 'PATCH', {'id': user['id'], 'status': 'ACTIVE' if enabled else 'DISABLED'})
        return 1

    def legacy_client(self, user):
        with connection() as c:
            row = c.execute('''SELECT l.sub_id,u.username,u.tg_id FROM remna_links l
                LEFT JOIN users u ON u.sub_id=l.sub_id WHERE l.remna_id=?''', (user['id'],)).fetchone()
        if not row: return None
        expiry = int(datetime.fromisoformat(user['expireAt'].replace('Z', '+00:00')).timestamp() * 1000)
        traffic = user.get('userTraffic') or {}
        return {'id': user['vlessUuid'], 'email': (row['username'] or user['username']) + ' #1',
            'subId': row['sub_id'], 'tgId': row['tg_id'] or 0, 'flow': 'xtls-rprx-vision',
            'expiryTime': expiry, 'enable': user['status'] == 'ACTIVE', 'totalGB': user['trafficLimitBytes'],
            'up': 0, 'down': traffic.get('usedTrafficBytes', 0),
            'total': traffic.get('lifetimeUsedTrafficBytes', 0), 'onlineAt': traffic.get('onlineAt')}


class RemnawaveClient:
    """Keep the migrated handler interface while using only native Remnawave endpoints."""
    def __init__(self, *args, **kwargs): self.native = Remnawave()
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None

    async def list_inbounds(self):
        users = await asyncio.to_thread(self.native.users)
        clients = [c for u in users if (c := self.native.legacy_client(u))]
        return [{'id': 1, 'remark': 'Remnawave', 'protocol': 'vless',
                 'settings': json.dumps({'clients': clients}), 'clients': clients, 'clientStats': clients}]

    async def server_status(self):
        stat = os.statvfs('/data' if Path('/data').exists() else '.')
        return {'disk': {'total': stat.f_blocks * stat.f_frsize,
                         'current': (stat.f_blocks - stat.f_bavail) * stat.f_frsize}}

    async def add_client_append(self, inbound_id, email, flow='', sub_id='', expiry_time=0, **kwargs):
        await asyncio.to_thread(self.native.ensure, sub_id, email.split(' #')[0], expiry_time,
                                client_id=kwargs.get('client_id') or kwargs.get('uuid'))
        return {'success': True}

    async def update_client(self, inbound_id, client_id, client_obj):
        with connection() as c:
            row = c.execute('SELECT sub_id FROM remna_links WHERE vless_uuid=?', (client_id,)).fetchone()
        if not row or row['sub_id'] != client_obj['subId']:
            raise RuntimeError('Remnawave ownership mismatch')
        await asyncio.to_thread(self.native.ensure, row['sub_id'], client_obj['email'].split(' #')[0],
            client_obj['expiryTime'], enabled=bool(client_obj.get('enable', True)))
        return {'success': True}

    @staticmethod
    def _normalize_client_for_update(client, *, enabled): return {**client, 'enable': bool(enabled)}

    async def set_user_clients_enabled(self, inbound, username_clean, sub_id, *, enabled):
        return await asyncio.to_thread(self.native.disable, sub_id, enabled)

    async def delete_user_clients(self, inbound, username_clean, sub_id, **kwargs):
        return await asyncio.to_thread(self.native.disable, sub_id)

    async def restart_xray(self):
        return {'success': True, 'msg': 'Remnawave applies user changes without restarting nodes'}

    @staticmethod
    def _panel_api_url(path): return path

    async def _post_form(self, path, data):
        if path.endswith('/onlines'):
            users = await asyncio.to_thread(self.native.users)
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=3)
            online = []
            for user in users:
                if user['status'] != 'ACTIVE': continue
                at = (user.get('userTraffic') or {}).get('onlineAt')
                if at and datetime.fromisoformat(at.replace('Z', '+00:00')) > cutoff:
                    client = self.native.legacy_client(user)
                    if client: online.append(client['email'])
            return 200, {'success': True, 'obj': online}
        if '/clientIps/' in path: return 200, {'success': True, 'obj': []}
        raise RuntimeError('Unsupported legacy API operation')

    async def _get_json(self, path): return await self._post_form(path, {})
