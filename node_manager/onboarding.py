"""An unpublished candidate must pass RF review and an explicit human decision.

Candidate profiles/squads isolate phone credentials from existing VPN nodes. Secrets
are encrypted in the manager DB; ordinary job status never contains a test key.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from urllib.parse import quote, urlencode

from . import diagnostics, domains
from .ssh import SafeError
from .store import atomic_json
from .vpn_config import rename_tags


def revision(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Onboarding:
    def __init__(self, manager, user_api=None):
        self.m, self.store = manager, manager.store
        self._user_api = user_api or manager.vpn_config.api
        with self.store.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS install_candidates(job TEXT PRIMARY KEY, data BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS install_actions(id TEXT PRIMARY KEY,job TEXT NOT NULL,
                    operation TEXT NOT NULL,created REAL NOT NULL);
            ''')

    def users_api(self):
        # The node-scoped token deliberately cannot create/modify users. Use the
        # existing local root API login only for our fixed SERVICE_PROBE / NODE_TEST
        # operations. No credential or arbitrary user-ID API crosses the web bridge.
        return self._user_api

    def data(self, job):
        with self.store.db() as db:
            row = db.execute('SELECT data FROM install_candidates WHERE job=?', (job,)).fetchone()
        return json.loads(self.store.cipher.decrypt(row[0])) if row else {}

    def save(self, job, data):
        sealed = self.store.cipher.encrypt(json.dumps(data).encode())
        with self.store.db() as db:
            db.execute('INSERT OR REPLACE INTO install_candidates VALUES (?,?)', (job, sealed))

    def standard_revision(self, state):
        return revision({'config': self.m.api.call('config-profiles/' + state['profile_uuid'])['config'],
                         'sni': state['sni'], 'fingerprint': state['fingerprint'],
                         'xhttp': self.m.settings.get('xhttp')})

    def profile(self, job, state):
        """Retry-safe isolated clone: existing customers never receive this inbound."""
        data, api = self.data(job), self.users_api()
        name = 'KL-pending-' + job.replace('-', '')[:19]  # Remnawave name limit: 30.
        current_revision = self.standard_revision(state)
        if data.get('profile') and data['standard_revision'] != current_revision:
            self.revoke_phone(job); data = self.data(job)
            source = api.call('config-profiles/' + state['profile_uuid'])
            tags = {i['tag']: 'P_' + job.replace('-', '')[:16] + '_' + str(n)
                    for n, i in enumerate(source['config']['inbounds'])}
            api.call('config-profiles', 'PATCH', {'uuid': data['profile'],
                                                'config': rename_tags(source['config'], tags)})
            data.update(standard_revision=current_revision, state=state)
            self.save(job, data)
        if not data.get('profile'):
            source = api.call('config-profiles/' + state['profile_uuid'])
            tags = {i['tag']: 'P_' + job.replace('-', '')[:16] + '_' + str(n)
                    for n, i in enumerate(source['config']['inbounds'])}
            clone = next((p for p in api.items('config-profiles', 'configProfiles') if p['name'] == name), None)
            clone = clone or api.call('config-profiles', 'POST',
                                      {'name': name, 'config': rename_tags(source['config'], tags)})
            clone = api.call('config-profiles/' + clone['uuid'])
            active = [state['inbound_uuid']] + ([self.m.settings['xhttp']['inbound_uuid']]
                                               if self.m.settings.get('xhttp') else [])
            mapping = {i['uuid']: next(v['uuid'] for v in clone['inbounds'] if v['tag'] == tags[i['tag']])
                       for i in source['inbounds'] if i['uuid'] in active}
            data.update(profile=clone['uuid'], inbounds=[mapping[i] for i in active],
                        standard_revision=current_revision)
            self.save(job, data)
        if not data.get('squad'):
            squad = next((s for s in api.items('internal-squads', 'internalSquads') if s['name'] == name), None)
            squad = squad or api.call('internal-squads', 'POST', {'name': name, 'inbounds': data['inbounds']})
            data['squad'] = squad['uuid']; self.save(job, data)
        probe = json.loads((self.m.control / 'probe-user.json').read_text())
        users = self.users_api()
        user = users.call('users/' + str(probe['id']))
        squads = [s['uuid'] if isinstance(s, dict) else s for s in user['activeInternalSquads']]
        if data['squad'] not in squads:
            users.call('users', 'PATCH', {'id': probe['id'], 'activeInternalSquads': [*squads, data['squad']]})
        return {'activeConfigProfileUuid': data['profile'], 'activeInbounds': data['inbounds']}

    def prepared(self, job, candidate, state):
        data = self.data(job)
        data.update(candidate=candidate, state=state)
        self.save(job, data)
        self.store.phase(job, 'checking_from_ru', candidate_prepared=True)
        self.sync_pending_dns()
        self.check(job)

    def sync_pending_dns(self):
        pending = []
        with self.store.db() as db:
            rows = db.execute("SELECT c.job,c.data FROM install_candidates c JOIN jobs j ON j.id=c.job "
                              "WHERE j.state NOT IN ('ready','removed','cancelled')").fetchall()
        for row in rows:
            candidate = json.loads(self.store.cipher.decrypt(row['data'])).get('candidate')
            if candidate:
                pending.append({k: candidate[k] for k in ('uuid', 'public_ip')})
        atomic_json(self.m.control / 'pending-nodes.json', pending)
        if domains.enabled(self.m):
            domains.sync(self.m.control, self.store.nodes(), self.m.network.state())

    def check(self, job):
        data = self.data(job)
        self.store.phase(job, 'checking_from_ru')
        try:
            result = self.remote_check(data)
        except Exception as error:
            result = {'ok': False, 'source_ok': False, 'error':
                      str(error) if isinstance(error, SafeError) else 'ru_check_unavailable', 'transports': {}}
        data['report'] = {**result, 'at': time.time()}
        data['review_revision'] = revision({'candidate': data['candidate']['uuid'],
                                          'standard': data['standard_revision'], 'report': data['report']})
        data.pop('action', None)
        self.save(job, data)
        self.store.phase(job, 'awaiting_approval')
        self.store.pause(job)
        diagnostics.event('node_candidate_checked', job_id=job, ok=result.get('ok'), error=result.get('error'))

    def remote_check(self, data):
        config = self.m.reviews.config()
        if not config.get('enabled') or not config.get('probe'):
            raise SafeError('ru_check_unavailable')
        state, node = data['state'], data['candidate']
        probe = json.loads((self.m.control / 'probe-user.json').read_text())
        sample = hashlib.shake_256(b'Kingline connectivity probe v1').digest(1024 * 1024)
        manifest = {'sample_url': 'https://test.edge.kingline.online/api/connectivity/probe.bin',
                    'sample_bytes': len(sample), 'sample_sha256': hashlib.sha256(sample).hexdigest(),
                    'sample_resolve': 'test.edge.kingline.online:443:198.51.100.10',
                    'expected_source_ip': self.m.reviews.source_ip()}
        if not manifest['expected_source_ip']:
            raise SafeError('ru_source_unknown')
        target = {'id': node['uuid'], 'revision': data['standard_revision'], 'address': node['public_ip'],
                  'ip': node['public_ip'], 'port': 443, 'probe_uuid': probe['vlessUuid'],
                  'sni': state['sni'], 'fingerprint': state['fingerprint'],
                  'public_key': state['reality']['publicKey'], 'short_id': state['short_id']}
        ssh = self.m.ssh_factory(self.m.root, config['probe'])
        try:
            raw = json.loads(ssh.python(Path(__file__).with_name('onboarding_probe_remote.py').read_text(),
                {'target': target, 'manifest': manifest, 'xhttp_path': self.m.settings.get('xhttp', {}).get('path')},
                timeout=540))
        finally:
            ssh.close()
        # The remote worker returns only fixed codes and bounded numeric observations.
        from .reviews import clean_observation
        clean = {}
        for transport in ('raw', 'xhttp'):
            value = raw.get('transports', {}).get(transport)
            if value:
                observation = clean_observation(value)
                if not observation: raise SafeError('ru_report_invalid')
                clean[transport] = observation
        expected = {'raw', 'xhttp'} if self.m.settings.get('xhttp') else {'raw'}
        source_ok = raw.get('source_ok') is True
        return {'ok': source_ok and set(clean) == expected and all(v['ok'] for v in clean.values()),
                'source_ok': source_ok, 'error': raw.get('error') if raw.get('error') in
                {'ru_probe_busy', 'ru_source_invalid', 'ru_probe_unavailable'} else None, 'transports': clean}

    def public(self, job):
        data, row = self.data(job), self.store.get(job)
        if not data.get('candidate'): return None
        return {'pending': row['state'] not in ('ready', 'removed', 'cancelled'),
                'revision': data.get('review_revision'), 'report': data.get('report'),
                'test_key_expires': data.get('phone_expires'),
                'cancel_failed': data.get('action') == 'cancel' and row['state'] == 'failed'}

    def action(self, body):
        with self.m.lock:
            row, data = self.store.get(body.job_id), self.data(body.job_id)
            with self.store.db() as db:
                old = db.execute('SELECT * FROM install_actions WHERE id=?', (body.request_id,)).fetchone()
            if old:
                if old['job'] != body.job_id or old['operation'] != body.operation:
                    raise SafeError('idempotency_key_conflict')
                return {'ok': True, 'job': self.store.public_job(row)}
            if row['state'] == 'cancelled' and body.operation == 'cancel':
                return {'ok': True, 'job': self.store.public_job(row)}
            if row['state'] not in ('waiting_review', 'failed') or not data.get('candidate'):
                raise SafeError('candidate_not_waiting')
            if data.get('action') == 'cancel' and body.operation != 'cancel':
                raise SafeError('candidate_cancellation_pending')
            if body.operation == 'test-key':
                if row['state'] != 'waiting_review': raise SafeError('candidate_not_waiting')
                return self.phone_key(body.job_id, data)
            if body.operation == 'approve':
                if not body.revision or body.revision != data.get('review_revision'):
                    raise SafeError('candidate_review_changed')
                if self.standard_revision(self.m.standard()) != data['standard_revision']:
                    raise SafeError('candidate_standard_changed')
                # A human may approve after an RF failure, based on their phone test.
                # Core verification is mandatory again before exposing any host.
            if self.m.ru_installer.active() or self.m.vpn_config.busy():
                raise SafeError('vpn_operation_busy')
            data['action'] = body.operation; self.save(body.job_id, data)
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO install_actions VALUES (?,?,?,?)',
                           (body.request_id, body.job_id, body.operation, time.time()))
                db.execute("UPDATE jobs SET state='queued',phase=?,error=NULL,updated=? WHERE id=?",
                           ('cancelling' if body.operation == 'cancel' else 'approval_queued' if body.operation == 'approve'
                            else 'checking_from_ru', time.time(), body.job_id))
            diagnostics.event('node_candidate_decision', job_id=body.job_id, operation=body.operation)
            return {'ok': True, 'job': self.store.public_job(self.store.get(body.job_id))}

    def phone_key(self, job, data):
        api, state = self.users_api(), data['state']
        if not domains.enabled(self.m): raise SafeError('node_domains_required')
        name = 'candidate_' + job.replace('-', '')[:20]
        if data.get('phone') and data.get('phone_expires', 0) < time.time() + 60:
            self.delete('users', data['phone']['id']); data.pop('phone'); self.save(job, data)
        if not data.get('phone'):
            try: user = api.call('users/by-username/' + name)
            except RuntimeError as error:
                if not str(error).endswith('HTTP 404'): raise
                user = None
            if user and (user.get('tag') != 'NODE_TEST' or
                         [s['uuid'] if isinstance(s, dict) else s for s in user['activeInternalSquads']] != [data['squad']]):
                raise SafeError('candidate_user_collision')
            if not user:
                expires = datetime.now(timezone.utc) + timedelta(hours=2)
                user = api.call('users', 'POST', {'username': name, 'tag': 'NODE_TEST',
                    'description': 'Temporary candidate phone check; never a customer subscription.',
                    'expireAt': expires.isoformat(), 'trafficLimitBytes': 1024**3,
                    'trafficLimitStrategy': 'NO_RESET', 'hwidDeviceLimit': 1,
                    'activeInternalSquads': [data['squad']]})
            data['phone'] = {'id': user['id'], 'vlessUuid': user['vlessUuid']}
            data['phone_expires'] = datetime.fromisoformat(user['expireAt'].replace('Z', '+00:00')).timestamp()
            self.save(job, data)
        node = data['candidate']
        # DNS was verified before the gate. Never fall back to a bare IP test key.
        address = domains.hostname(node)
        query = urlencode({'encryption': 'none', 'security': 'reality', 'type': 'tcp',
                           'flow': 'xtls-rprx-vision', 'sni': state['sni'], 'fp': state['fingerprint'],
                           'pbk': state['reality']['publicKey'], 'sid': state['short_id'], 'spx': '/'})
        link = 'vless://' + data['phone']['vlessUuid'] + '@' + address + ':443?' + query
        return {'ok': True, 'test_key': link + '#' + quote('Тест · ' + node['label']),
                'expires': data['phone_expires'], 'traffic_bytes': 1024**3}

    def delete(self, kind, identity):
        api = self.users_api() if kind in ('users', 'internal-squads', 'config-profiles') else self.m.api
        try: api.call(kind + '/' + str(identity), 'DELETE')
        except RuntimeError as error:
            if not str(error).endswith('HTTP 404'): raise

    def revoke_phone(self, job):
        data = self.data(job)
        phone = data.get('phone')
        if not phone:
            # Recover a crash after API creation but before the encrypted journal write.
            try: phone = self.users_api().call('users/by-username/candidate_' + job.replace('-', '')[:20])
            except RuntimeError as error:
                if not str(error).endswith('HTTP 404'): raise
            if phone and (phone.get('tag') != 'NODE_TEST' or
                          [s['uuid'] if isinstance(s, dict) else s for s in phone['activeInternalSquads']] != [data['squad']]):
                raise SafeError('candidate_user_collision')
        if phone: self.delete('users', phone['id'])
        data.pop('phone', None); data.pop('phone_expires', None); self.save(job, data)

    def cleanup_profile(self, job):
        data = self.data(job)
        self.revoke_phone(job); data = self.data(job)
        # Deleting this job's private squad also removes its SERVICE_PROBE membership.
        if data.get('squad'): self.delete('internal-squads', data['squad'])
        if data.get('profile'): self.delete('config-profiles', data['profile'])

    def execute(self, job):
        data = self.data(job)
        if data.get('action') == 'cancel': return self.cancel(job)
        if data.get('action') != 'approve':
            # A failed publication may already have rebound the node to the main
            # profile. Restore isolation before another phone/review round.
            binding = self.profile(job, self.m.standard())
            self.m.api.call('nodes', 'PATCH', {'uuid': data['candidate']['uuid'], 'configProfile': binding})
            self.m.wait_node(data['candidate']['uuid'])
            return self.check(job)
        state = self.m.standard()
        if self.standard_revision(state) != data['standard_revision']:
            raise SafeError('candidate_standard_changed')
        self.store.phase(job, 'verifying_tunnel')
        self.revoke_phone(job)
        node = data['candidate']
        self.m.api.call('nodes', 'PATCH', {'uuid': node['uuid'], 'configProfile': {
            'activeConfigProfileUuid': state['profile_uuid'], 'activeInbounds': [state['inbound_uuid']] +
            ([self.m.settings['xhttp']['inbound_uuid']] if self.m.settings.get('xhttp') else [])}})
        self.m.wait_node(node['uuid'])
        check = self.m.verify_new_node(node['public_ip'])
        self.m.publish(job, node, state, check)
        try: self.cleanup_profile(job)
        except Exception:
            diagnostics.event('node_candidate_cleanup_pending', 'WARNING', job_id=job)
        self.sync_pending_dns()

    def cancel(self, job):
        data, row = self.data(job), self.store.get(job)
        node = data['candidate']
        if any(n['uuid'] == node['uuid'] and n.get('provisioned', True) for n in self.store.nodes()):
            raise SafeError('cannot_cancel_published_node')
        self.store.phase(job, 'cancelling')
        self.revoke_phone(job)
        # A failed approved publication can have created a disabled manual host.
        host_id = row['context'].get('host_uuid')
        for host in self.m.api.items('hosts', 'hosts'):
            attached = [n if isinstance(n, str) else n['uuid'] for n in host.get('nodes', [])]
            if host['uuid'] == host_id:
                if attached != [node['uuid']]: raise SafeError('manual_host_node_mismatch')
                self.delete('hosts', host_id)
            elif node['uuid'] in attached:
                self.m.api.call('hosts', 'PATCH', {'uuid': host['uuid'],
                                                 'nodes': [i for i in attached if i != node['uuid']]})
        from .ru_egress import RUEgress
        RUEgress(self.m.root, self.m.control, self.m.ssh_factory).remove(node)
        ssh = self.m.ssh_factory(self.m.root, {**row['request'], 'host_key_sha256': node['host_key_sha256']})
        try:
            ssh.python(Path(__file__).with_name('onboarding_cancel_remote.py').read_text(),
                       {'job_id': job}, timeout=240)
        finally: ssh.close()
        self.m.remove_core_peer(node)
        self.delete('nodes', node['uuid'])
        self.cleanup_profile(job)
        if any(n['uuid'] == node['uuid'] for n in self.store.nodes()):
            self.store.delete_node(node)
            self.m.export_inventory()
        # Keep the reservation and history. Re-enrolling a retired IP needs an explicit reset.
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='cancelled',phase='cancelled',error=NULL,secret=NULL,updated=? WHERE id=?",
                       (time.time(), job))
        self.sync_pending_dns()
        diagnostics.event('node_candidate_cancelled', job_id=job)
