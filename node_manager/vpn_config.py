"""Versioned VPN drafts. Secrets and recovery journals stay encrypted on the controller.

The editor deliberately keeps the listener contract (RAW/REALITY/443) used by our
firewall, subscriptions and DNS. Per-node profiles inherit the same client identity.
"""
import copy
import hashlib
import json
import re
import threading
import time
import uuid

from remna_api import RemnaAPI
from . import diagnostics
from .ssh import SafeError
from .store import atomic_json
from .cover import certificate_lock

KEEP = '__KINGLINE_KEEP_SECRET__'
SECRET = re.compile(r'privatekey|password|secret|token|seed|(^id$)|certificate|(^key$)', re.I)
HOST_FIELDS = ('uuid', 'inbound', 'nodes', 'port', 'sni', 'fingerprint', 'alpn', 'isDisabled')
FINGERPRINTS = ('firefox', 'chrome', 'safari', 'edge', 'randomized')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def masked(value):
    if isinstance(value, dict):
        return {k: KEEP if SECRET.search(k) and v not in (None, '', []) else masked(v) for k, v in value.items()}
    if isinstance(value, list):
        return [masked(v) for v in value]
    return value


def restore(value, original, depth=0):
    if depth > 30:
        raise SafeError('vpn_config_too_deep')
    if value == KEEP:
        if original is None:
            raise SafeError('vpn_secret_placeholder_invalid')
        return copy.deepcopy(original)
    if isinstance(value, dict):
        return {k: restore(v, original.get(k) if isinstance(original, dict) else None, depth+1) for k, v in value.items()}
    if isinstance(value, list):
        return [restore(v, original[i] if isinstance(original, list) and i < len(original) else None, depth+1) for i, v in enumerate(value)]
    return value


def differences(old, new, prefix='config'):
    if old == new:
        return []
    if isinstance(old, dict) and isinstance(new, dict):
        result = []
        for key in sorted(old.keys() | new.keys()):
            if not SECRET.search(key):
                result += differences(old.get(key), new.get(key), prefix+'.'+key)
        return result
    if isinstance(old, list) and isinstance(new, list):
        result = []
        for i in range(max(len(old), len(new))):
            result += differences(old[i] if i < len(old) else None, new[i] if i < len(new) else None, f'{prefix}[{i}]')
        return result
    return [{'path': prefix, 'before': masked(old), 'after': masked(new)}]


def secret_values(value, path=()):
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if SECRET.search(k):
                result[path+(k,)] = v
            else:
                result.update(secret_values(v, path+(k,)))
        return result
    if isinstance(value, list):
        result = {}
        for i, v in enumerate(value):
            result.update(secret_values(v, path+(i,)))
        return result
    return {}


def inbound(config):
    return next((i for i in config.get('inbounds', []) if i.get('protocol') == 'vless' and i.get('port') == 443
                 and i.get('streamSettings', {}).get('security') == 'reality'), None)


def rename_tags(value, mapping):
    if isinstance(value, dict):
        return {k: rename_tags(v, mapping) for k, v in value.items()}
    if isinstance(value, list):
        return [rename_tags(v, mapping) for v in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def aligned(config, template):
    """Panel inbound tags are globally unique; preserve each target profile's own tags."""
    incoming, current = config.get('inbounds', []), template.get('inbounds', [])
    if len(incoming) != len(current):
        raise SafeError('vpn_listener_contract_locked')
    return rename_tags(config, {a['tag']: b['tag'] for a, b in zip(incoming, current)})


def contract(config):
    i = inbound(config) or {}
    stream = i.get('streamSettings', {})
    reality = stream.get('realitySettings', {})
    return {k: copy.deepcopy(v) for k, v in {
        'protocol': i.get('protocol'), 'port': i.get('port'), 'listen': i.get('listen'),
        'network': stream.get('network'), 'security': stream.get('security'),
        'flow': i.get('settings', {}).get('flow'), 'privateKey': reality.get('privateKey'),
        'serverNames': reality.get('serverNames'), 'shortIds': reality.get('shortIds')}.items()}


class ConfigPanel:
    """The restricted onboarding token cannot manage squads; use the local admin login.

    Its credentials never enter Docker, browser responses or diagnostics.
    """
    def __init__(self):
        self.api = None

    def call(self, path, method='GET', body=None):
        if self.api is None:
            self.api = RemnaAPI()
        try:
            return self.api.call(path, method, body)
        except RuntimeError as exc:
            if 'HTTP 401' not in str(exc):
                raise
            self.api = RemnaAPI()
            return self.api.call(path, method, body)

    def items(self, path, key):
        value = self.call(path)
        return value.get(key, value.get('items', [])) if isinstance(value, dict) else value


class VPNConfig:
    def __init__(self, manager, api=None):
        self.m = manager
        self.api = api or ConfigPanel()
        self.thread = None
        with self.m.store.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS vpn_changes(
                id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, state TEXT NOT NULL,
                phase TEXT NOT NULL, error TEXT, payload BLOB NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL)''')

    def busy(self):
        with self.m.store.db() as db:
            return bool(db.execute("SELECT 1 FROM vpn_changes WHERE state IN ('queued_check','checking','queued_apply','applying','rollback_failed') LIMIT 1").fetchone())

    def installation_guard(self):
        with self.m.store.db() as db:
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
                raise SafeError('vpn_installation_busy')

    def start(self):
        self.thread = threading.Thread(target=self.worker, daemon=True, name='vpn-config')
        self.thread.start()

    def read(self, change_id):
        with self.m.store.db() as db:
            row = db.execute('SELECT * FROM vpn_changes WHERE id=?', (change_id,)).fetchone()
        if row is None:
            raise KeyError(change_id)
        return dict(row), json.loads(self.m.store.cipher.decrypt(row['payload']))

    def write(self, change_id, state, phase, data=None, error=None):
        with self.m.store.db() as db:
            if data is None:
                db.execute('UPDATE vpn_changes SET state=?,phase=?,error=?,updated=? WHERE id=?',
                           (state, phase, error, time.time(), change_id))
            else:
                db.execute('UPDATE vpn_changes SET state=?,phase=?,error=?,payload=?,updated=? WHERE id=?',
                           (state, phase, error, self.m.store.cipher.encrypt(json.dumps(data).encode()), time.time(), change_id))

    def public(self, change_id):
        row, data = self.read(change_id)
        return {k: row[k] for k in ('id', 'state', 'phase', 'error', 'created', 'updated')} | {
            'profile_name': data['profile_name'], 'scope': data['scope'], 'note': data['note'],
            'nodes': data['labels'], 'paths': data['paths'], 'expires': row['created']+600,
            'checks': data.get('checks', []), 'diff': data.get('diff', []),
            'kind': data.get('kind', 'vpn_config'), 'action': data.get('network_action'),
            'can_rollback': row['state'] in ('ready', 'rollback_failed') and bool(data.get('undo'))}

    def history(self):
        with self.m.store.db() as db:
            ids = [r[0] for r in db.execute('SELECT id FROM vpn_changes ORDER BY created DESC LIMIT 30')]
        return [self.public(i) for i in ids]

    def snapshot(self):
        nodes = self.api.items('nodes', 'nodes')
        # Never store rawInbound (contains keys) inside status objects or public output.
        nodes = [{k: n.get(k) for k in ('uuid', 'name', 'isConnected', 'isDisabled', 'xrayUptime')} |
                 {'xrayVersion': (n.get('versions') or {}).get('xray'), 'version': (n.get('versions') or {}).get('node')} |
                 {'binding': {'activeConfigProfileUuid': n.get('configProfile', {}).get('activeConfigProfileUuid'),
                              'activeInbounds': [i['uuid'] if isinstance(i, dict) else i for i in n.get('configProfile', {}).get('activeInbounds', [])]}}
                 for n in nodes]
        managed = {n['uuid']: n for n in self.m.store.nodes() if n.get('provisioned', True)}
        nodes = [n | {'ip': managed[n['uuid']]['public_ip']} for n in nodes if n['uuid'] in managed]
        profiles = self.api.items('config-profiles', 'configProfiles')
        # Lists in some panel versions omit config; always get the canonical resource.
        profiles = [self.api.call('config-profiles/'+p['uuid']) for p in profiles]
        profiles = [{k: p[k] for k in ('uuid', 'name', 'config', 'inbounds')} for p in profiles]
        hosts = [{k: h.get(k) for k in HOST_FIELDS} for h in self.api.items('hosts', 'hosts')]
        squads = [{'uuid': s['uuid'], 'inbounds': [i['uuid'] if isinstance(i, dict) else i for i in s.get('inbounds', [])]}
                  for s in self.api.items('internal-squads', 'internalSquads')]
        owner = json.loads((self.m.control/'owner-profile.json').read_text())
        snap = {'profiles': profiles, 'nodes': nodes, 'hosts': hosts, 'squads': squads, 'owner': owner}
        # Inbound metadata contains rawInbound too: do not let transient metadata affect revisions.
        for p in profiles:
            p['inbounds'] = [{'uuid': i['uuid'], 'tag': i['tag']} for i in p['inbounds']]
        return snap

    @staticmethod
    def revision(snap):
        stable = copy.deepcopy(snap)
        for n in stable['nodes']:
            for key in ('isConnected', 'xrayUptime', 'xrayVersion', 'version'):
                n.pop(key, None)
        for key in ('profiles', 'nodes', 'hosts', 'squads'):
            stable[key].sort(key=lambda r: r['uuid'])
        return digest(stable)

    def catalog(self, profile_id=None):
        snap = self.snapshot()
        profiles = [{'uuid': p['uuid'], 'name': p['name'],
                     'editable': inbound(p['config']) is not None,
                     'nodes': [n['uuid'] for n in snap['nodes'] if n['binding']['activeConfigProfileUuid'] == p['uuid']]}
                    for p in snap['profiles']]
        result = {'ok': True, 'profiles': profiles, 'nodes': snap['nodes'],
                  'default_profile': snap['owner']['profile_uuid'], 'history': self.history()}
        if profile_id:
            p = next((p for p in snap['profiles'] if p['uuid'] == profile_id), None)
            if p is None:
                raise KeyError(profile_id)
            h = next((h for h in snap['hosts'] if (h.get('inbound') or {}).get('configProfileUuid') == profile_id), {})
            result['profile'] = {'uuid': p['uuid'], 'name': p['name'], 'config': masked(p['config']),
                                 'client': {'sni': h.get('sni') or snap['owner']['sni'],
                                            'fingerprint': h.get('fingerprint') or snap['owner']['fingerprint']},
                                 'revision': self.revision(snap)}
        return result
    def prepare(self, body):
        self.installation_guard()
        snap = self.snapshot()
        if body['revision'] != self.revision(snap):
            raise SafeError('vpn_revision_changed')
        source = next((p for p in snap['profiles'] if p['uuid'] == body['profile_uuid']), None)
        if source is None or not inbound(source['config']):
            raise SafeError('vpn_profile_not_supported')
        if body['scope'] == 'all':
            targets = snap['nodes']
        else:
            targets = [n for n in snap['nodes'] if n['binding']['activeConfigProfileUuid'] == source['uuid']]
            if body['scope'] == 'selected':
                targets = [n for n in targets if n['uuid'] in body['nodes']]
                if len(targets) != len(set(body['nodes'])):
                    raise SafeError('vpn_node_profile_changed')
        if not targets:
            raise SafeError('vpn_select_nodes')
        candidate = restore(body['config'], source['config'])
        old, new = inbound(source['config']), inbound(candidate)
        if new is None:
            raise SafeError('vpn_listener_contract_locked')
        # Additional listeners, protocol migrations and port changes require firewall/onboarding support.
        immutable = lambda c: [(i.get('tag'), i.get('protocol'), i.get('listen'), i.get('port')) for i in c.get('inbounds', [])]
        if immutable(candidate) != immutable(source['config']):
            raise SafeError('vpn_listener_contract_locked')
        for key in ('api', 'metrics'):
            if candidate.get(key) != source['config'].get(key):
                raise SafeError('vpn_management_contract_locked')
        c = contract(candidate)
        if not (c['protocol'] == 'vless' and c['port'] == 443 and c['network'] in ('tcp', 'raw') and
                c['security'] == 'reality' and c['flow'] == 'xtls-rprx-vision'):
            raise SafeError('vpn_listener_contract_locked')
        # No manually supplied users, keys or credentials. Panel is the only source of users.
        if secret_values(candidate) != secret_values(source['config']) or any(i.get('settings', {}).get('clients') for i in candidate.get('inbounds', [])):
            raise SafeError('vpn_secret_change_forbidden')
        log = candidate.get('log', {})
        if any(log.get(k) not in (None, '', 'none', '/dev/stdout', '/dev/stderr') for k in ('access', 'error')) or candidate.get('env'):
            raise SafeError('vpn_file_logging_forbidden')
        reality = new['streamSettings']['realitySettings']
        names, shorts = reality.get('serverNames', []), reality.get('shortIds', [])
        client = body['client']
        if client['fingerprint'] not in FINGERPRINTS or client['sni'] not in names:
            raise SafeError('vpn_client_mismatch')
        if not shorts or any(not isinstance(s, str) or not re.fullmatch(r'(?:[a-f0-9]{2}){0,8}', s) for s in shorts):
            raise SafeError('vpn_short_ids_invalid')
        default = next(p for p in snap['profiles'] if p['uuid'] == snap['owner']['profile_uuid'])
        touches_default = body['scope'] == 'all' or (source['uuid'] == default['uuid'] and {n['uuid'] for n in targets} ==
            {n['uuid'] for n in snap['nodes'] if n['binding']['activeConfigProfileUuid'] == default['uuid']})
        # The DNS alias has one client configuration. A node-specific edit cannot change that identity.
        if not touches_default and (contract(candidate) != contract(default['config']) or client['sni'] != snap['owner']['sni']):
            raise SafeError('vpn_smart_identity_shared')
        # Updating the common identity requires every node, including existing isolated profiles.
        if touches_default and body['scope'] != 'all' and (contract(candidate) != contract(default['config']) or client['sni'] != snap['owner']['sni']) and len(targets) != len(snap['nodes']):
            raise SafeError('vpn_identity_requires_all_nodes')
        if self.m.cover and (client['sni'] != self.m.cover.settings['domain'] or
                            reality.get('target', reality.get('dest')) != self.m.cover.settings['target']):
            raise SafeError('owned_target_profile_drift')
        for n in targets:
            current = next(p for p in snap['profiles'] if p['uuid'] == n['binding']['activeConfigProfileUuid'])
            if immutable(current['config']) != immutable(aligned(source['config'], current['config'])):
                raise SafeError('vpn_profile_not_supported')
        diff = []
        for pid in sorted({n['binding']['activeConfigProfileUuid'] for n in targets}):
            current = next(p for p in snap['profiles'] if p['uuid'] == pid)
            h = next((h for h in snap['hosts'] if (h.get('inbound') or {}).get('configProfileUuid') == pid), {})
            previous_client = {k: h.get(k) or snap['owner'][k] for k in ('sni', 'fingerprint')}
            changes = differences(current['config'], aligned(candidate, current['config'])) + differences(previous_client, client, 'client')
            diff.append({'profile': current['name'], 'changes': changes})
        paths = sorted({c['path'] for d in diff for c in d['changes']})
        return {'snapshot': snap, 'revision': body['revision'], 'profile_uuid': source['uuid'],
                'profile_name': source['name'], 'config': candidate, 'client': client,
                'scope': body['scope'], 'note': body.get('note', ''), 'targets': [n['uuid'] for n in targets],
                'labels': [n['name'] for n in targets], 'paths': paths, 'undo': [],
                'touches_default': touches_default, 'checks': [], 'diff': diff}

    def submit(self, body):
        with self.m.lock:
            with self.m.store.db() as db:
                old = db.execute('SELECT id FROM vpn_changes WHERE request_id=?', (body['request_id'],)).fetchone()
            if old:
                _, data = self.read(old[0])
                if data.get('request_digest') != digest(body):
                    raise SafeError('vpn_request_conflict')
                return self.public(old[0])
            if self.busy():
                raise SafeError('vpn_operation_busy')
            data = self.prepare(body)
            data['request_digest'] = digest(body)
            change_id, now = str(uuid.uuid4()), time.time()
            with self.m.store.db() as db:
                db.execute('INSERT INTO vpn_changes VALUES(?,?,?, ?,NULL,?,?,?)',
                           (change_id, body['request_id'], 'queued_check', 'queued',
                            self.m.store.cipher.encrypt(json.dumps(data).encode()), now, now))
                # Bounded encrypted history; never prune an unfinished or applicable draft.
                db.execute("DELETE FROM vpn_changes WHERE state NOT IN ('queued_check','checking','queued_apply','applying','validated') AND id NOT IN (SELECT id FROM vpn_changes ORDER BY created DESC LIMIT 30)")
                db.execute("UPDATE vpn_changes SET state='expired',phase='expired' WHERE state='validated' AND created<?", (now-600,))
            diagnostics.event('vpn_draft_created', change_id=change_id, node_count=len(data['targets']))
            return self.public(change_id)

    def commit(self, change_id):
        with self.m.lock:
            row, data = self.read(change_id)
            if row['state'] in ('queued_apply', 'applying', 'ready', 'noop'):
                return self.public(change_id)
            if row['state'] != 'validated' or time.time()-row['created'] > 600:
                raise SafeError('vpn_validation_required')
            if self.busy():
                raise SafeError('vpn_operation_busy')
            self.installation_guard()
            if data['revision'] != self.revision(self.snapshot()):
                raise SafeError('vpn_revision_changed')
            self.write(change_id, 'queued_apply', 'queued')
            return self.public(change_id)

    def validate_core(self, node, config):
        meta = next(n for n in self.m.store.nodes() if n['uuid'] == node['uuid'])
        request = {'ip': meta['public_ip'], 'ssh_port': meta.get('ssh_port', 22), 'host_key_sha256': meta.get('host_key_sha256')}
        ssh = self.m.ssh_factory(self.m.root, request)
        try:
            # -test parses only. No port binding, restart, disk file or active configuration change.
            ssh.run('docker exec -i remnanode xray run -test -config stdin:', json.dumps(config), timeout=40)
        except SafeError:
            raise SafeError('vpn_xray_validation_failed') from None
        finally:
            ssh.close()

    def check(self, change_id, data):
        self.write(change_id, 'checking', 'checking')
        for node in data['snapshot']['nodes']:
            if node['uuid'] not in data['targets']:
                continue
            try:
                self.validate_core(node, data['config'])
            except Exception:
                data['checks'].append({'node': node['name'], 'ok': False, 'check': 'xray_test'})
                self.write(change_id, 'checking', 'checking', data)
                raise
            data['checks'].append({'node': node['name'], 'ok': True, 'check': 'xray_test'})
            self.write(change_id, 'checking', 'checking', data)
        self.write(change_id, 'validated', 'validated', data)

    def journal(self, change_id, data, entry):
        data['undo'].append(entry)
        self.write(change_id, 'applying', 'applying', data)

    def patch(self, change_id, data, path, value, before):
        self.journal(change_id, data, {'path': path, 'before': before})
        return self.api.call(path, 'PATCH', value)

    def apply(self, change_id, data):
        self.installation_guard()
        if data['revision'] != self.revision(self.snapshot()):
            raise SafeError('vpn_revision_changed')
        snap = data['snapshot']
        source = next(p for p in snap['profiles'] if p['uuid'] == data['profile_uuid'])
        targets = [n for n in snap['nodes'] if n['uuid'] in data['targets']]
        involved = {n['binding']['activeConfigProfileUuid'] for n in targets}
        if all(next(p for p in snap['profiles'] if p['uuid'] == pid)['config'] == aligned(data['config'], next(p for p in snap['profiles'] if p['uuid'] == pid)['config']) for pid in involved) and all(
                h.get('sni') == data['client']['sni'] and h.get('fingerprint') == data['client']['fingerprint']
                for h in snap['hosts'] if h.get('inbound', {}).get('configProfileUuid') in involved):
            self.write(change_id, 'noop', 'unchanged')
            return
        # A subset gets its own profile; the original stays untouched on all other servers.
        partial = data['scope'] == 'selected' and len(targets) < sum(n['binding']['activeConfigProfileUuid'] == source['uuid'] for n in snap['nodes'])
        clone = None
        if partial:
            name = 'KL-'+change_id.replace('-', '')[:26]
            self.journal(change_id, data, {'clone_name': name})
            tags = {i['tag']: 'KL_'+change_id.replace('-', '')[:20]+'_'+str(index) for index, i in enumerate(data['config']['inbounds'])}
            clone = self.api.call('config-profiles', 'POST', {'name': name, 'config': rename_tags(data['config'], tags)})
            clone = self.api.call('config-profiles/'+clone['uuid'])
            mapping = {old['uuid']: next(i['uuid'] for i in clone['inbounds'] if i['tag'] == tags[old['tag']]) for old in source['inbounds']}
            for squad in snap['squads']:
                extra = [mapping[i] for i in squad['inbounds'] if i in mapping]
                if extra:
                    self.patch(change_id, data, 'internal-squads', {'uuid': squad['uuid'], 'inbounds': squad['inbounds']+extra}, squad)
            for node in targets:
                binding = {'activeConfigProfileUuid': clone['uuid'], 'activeInbounds': [mapping[i] for i in node['binding']['activeInbounds']]}
                self.patch(change_id, data, 'nodes', {'uuid': node['uuid'], 'configProfile': binding},
                           {'uuid': node['uuid'], 'configProfile': node['binding']})
        else:
            for pid in sorted(involved):
                old = next(p for p in snap['profiles'] if p['uuid'] == pid)
                self.patch(change_id, data, 'config-profiles', {'uuid': pid, 'config': aligned(data['config'], old['config'])}, {'uuid': pid, 'config': old['config']})
        for host in snap['hosts']:
            attached = set(n if isinstance(n, str) else n['uuid'] for n in host.get('nodes', []))
            if partial:
                if not attached or not attached.issubset(set(data['targets'])):
                    continue
            elif (host.get('inbound') or {}).get('configProfileUuid') not in involved:
                continue
            value = {'uuid': host['uuid'], 'sni': data['client']['sni'], 'fingerprint': data['client']['fingerprint']}
            if clone:
                old_id = host['inbound']['configProfileInboundUuid']
                if old_id not in mapping:
                    raise SafeError('vpn_host_binding_mismatch')
                value['inbound'] = {'configProfileUuid': clone['uuid'], 'configProfileInboundUuid': mapping[old_id]}
            self.patch(change_id, data, 'hosts', value, {k: host[k] for k in value})
        self.write(change_id, 'applying', 'verifying', data)
        state = copy.deepcopy(snap['owner'])
        reality = inbound(data['config'])['streamSettings']['realitySettings']
        state.update(sni=data['client']['sni'], fingerprint=data['client']['fingerprint'], short_id=reality['shortIds'][0],
                     reality_target=reality.get('target', reality.get('dest')))
        for node in targets:
            expected = clone['uuid'] if clone else node['binding']['activeConfigProfileUuid']
            for _ in range(45):
                status = self.api.call('nodes/'+node['uuid'])
                if status.get('isConnected') and status.get('xrayUptime', 0) > 0 and status.get('configProfile', {}).get('activeConfigProfileUuid') == expected:
                    break
                if self.m.stop.wait(2):
                    raise SafeError('worker_stopping')
            else:
                raise SafeError('vpn_node_delivery_failed')
            # Give the panel's config delivery a moment, then require HTTPS and UDP through the tunnel.
            for attempt in range(3):
                try:
                    self.m.probe(self.m.control, node['ip'], state_override=state)
                    break
                except Exception:
                    if attempt == 2:
                        raise SafeError('vpn_tunnel_verification_failed') from None
                    if self.m.stop.wait(3):
                        raise SafeError('worker_stopping')
            data['checks'].append({'node': node['name'], 'ok': True, 'check': 'https_udp'})
        if data['touches_default']:
            self.journal(change_id, data, {'owner': snap['owner']})
            atomic_json(self.m.control/'owner-profile.json', state)
        data['after_revision'] = self.revision(self.snapshot())
        self.write(change_id, 'ready', 'ready', data)
        diagnostics.event('vpn_config_applied', change_id=change_id, node_count=len(targets))

    def undo(self, change_id, data):
        self.write(change_id, 'applying', 'rolling_back', data)
        # Repeatable recovery: never remove a journal entry until its compensation succeeded.
        while data['undo']:
            step = data['undo'][-1]
            if getattr(self.m, 'network', None) and self.m.network.restore_step(step):
                pass
            elif 'owner' in step:
                atomic_json(self.m.control/'owner-profile.json', step['owner'])
            elif 'clone_name' in step:
                for p in self.api.items('config-profiles', 'configProfiles'):
                    if p['name'] == step['clone_name']:
                        self.api.call('config-profiles/'+p['uuid'], 'DELETE')
            else:
                self.api.call(step['path'], 'PATCH', step['before'])
            data['undo'].pop()
            self.write(change_id, 'applying', 'rolling_back', data)

    def rollback(self, change_id, revision):
        with self.m.lock:
            self.installation_guard()
            row, data = self.read(change_id)
            with self.m.store.db() as db:
                if db.execute("SELECT 1 FROM vpn_changes WHERE id!=? AND state IN ('queued_check','checking','queued_apply','applying','rollback_failed')", (change_id,)).fetchone():
                    raise SafeError('vpn_operation_busy')
            if row['state'] not in ('ready', 'rollback_failed') or not data.get('undo'):
                raise SafeError('vpn_rollback_unavailable')
            current = self.revision(self.snapshot())
            if current != revision or (row['state'] == 'ready' and current != data.get('after_revision')):
                raise SafeError('vpn_rollback_revision_changed')
            self.write(change_id, 'queued_apply', 'rollback_requested', data)
            return self.public(change_id)

    def worker(self):
        # Recover a process interruption before accepting new mutations.
        with self.m.store.db() as db:
            interrupted = [r[0] for r in db.execute("SELECT id FROM vpn_changes WHERE state IN ('checking','applying')")]
        for change_id in interrupted:
            self.process(change_id, recover=True)
        while not self.m.stop.is_set():
            try:
                with self.m.store.db() as db:
                    row = db.execute("SELECT id FROM vpn_changes WHERE state IN ('queued_check','queued_apply') ORDER BY created LIMIT 1").fetchone()
                if row:
                    self.process(row[0])
                else:
                    self.m.stop.wait(1)
            except Exception:
                diagnostics.event('vpn_worker_failed', 'ERROR')
                self.m.stop.wait(5)

    def process(self, change_id, recover=False):
        with self.m.lock, certificate_lock(self.m.root):
            row, data = self.read(change_id)
            try:
                if recover or row['phase'] == 'rollback_requested':
                    if data.get('undo'):
                        self.undo(change_id, data)
                        self.write(change_id, 'rolled_back', 'rolled_back', data)
                    else:
                        self.write(change_id, 'failed', 'interrupted', error='vpn_operation_interrupted')
                elif row['state'] == 'queued_check':
                    if data.get('kind') == 'node_network': self.m.network.check(change_id, data)
                    else: self.check(change_id, data)
                elif row['state'] == 'queued_apply':
                    self.write(change_id, 'applying', 'applying', data)
                    self.apply(change_id, data)
            except Exception as exc:
                code = str(exc) if isinstance(exc, SafeError) else 'vpn_operation_failed'
                if data.get('undo'):
                    try:
                        self.undo(change_id, data)
                        state = 'rolled_back'
                    except Exception:
                        state = 'rollback_failed'
                else:
                    state = 'failed'
                self.write(change_id, state, state, data, error=code)
                diagnostics.event('vpn_config_failed', 'ERROR', change_id=change_id, error=code, state=state)
