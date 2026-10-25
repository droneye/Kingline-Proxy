"""Small node controls: subscription order, SNI/target and encrypted TCP paths.

Transactions share the encrypted journal and single manager lock with onboarding.
There is no relay user/UUID: authentication and user accounting remain at the exit.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import calendar
import json
from pathlib import Path
import threading
import time
import uuid

from . import diagnostics
from .ssh import SafeError
from .store import atomic_json
from .vpn_config import digest, inbound, rename_tags
from . import domains

REMOTE = Path(__file__).with_name('relay_remote.py').read_text()


def route_paths(via, ids):
    ids = set(ids)
    if set(via) != ids or any(v is not None and v not in ids for v in via.values()):
        raise SafeError('network_nodes_changed')
    result = {}
    for exit_id in ids:
        seen, chain, current = set(), [], exit_id
        while current is not None:
            if current in seen:
                raise SafeError('network_route_cycle')
            seen.add(current); chain.append(current); current = via[current]
            if len(chain) > 8:
                raise SafeError('network_route_too_long')
        result[exit_id] = list(reversed(chain))
    return result


def compile_relays(state, nodes):
    ids = {n['uuid']: n for n in nodes}
    paths = route_paths({i: state.get('via', {}).get(i) for i in ids}, ids)
    listeners = {i: [] for i in ids}
    for exit_id, path in paths.items():
        if len(path) < 2:
            continue
        port = state['ports'][exit_id]
        for index, gateway in enumerate(path[:-1]):
            next_id = path[index+1]
            listeners[gateway].append({'exit': exit_id, 'port': port,
                'address': ids[next_id]['public_ip'], 'next_port': 443 if next_id == exit_id else port})
    result = {}
    for node_id, entries in listeners.items():
        entries.sort(key=lambda e: e['port'])
        lines = ['global', '  maxconn 2048', '  nbthread 2', '  mworker-max-reloads 8',
                 'defaults', '  mode tcp', '  timeout connect 5s', '  timeout client 3h',
                 '  timeout server 3h', '  option clitcpka', '  option srvtcpka']
        for item in entries:
            lines += ['listen exit_'+item['exit'].replace('-', ''),
                      '  bind 0.0.0.0:'+str(item['port']),
                      '  server hop '+item['address']+':'+str(item['next_port'])]
        result[node_id] = {'ports': [e['port'] for e in entries], 'config': '\n'.join(lines)+'\n'}
    return paths, result


def period(day, now):
    def reset(year, month):
        return datetime(year, month, min(day, calendar.monthrange(year, month)[1]), tzinfo=timezone.utc)
    start = reset(now.year, now.month)
    if now < start:
        previous = start-timedelta(days=1); start = reset(previous.year, previous.month)
    following = (start.replace(day=28)+timedelta(days=4)).replace(day=1)
    end = reset(following.year, following.month)
    return start.isoformat(), end.timestamp()


def usage_sample(old, sample, reset_day, panel_used, now=None):
    now = now or datetime.now(timezone.utc)
    key, until = period(reset_day, now)
    if not old:
        total = panel_used
    elif old['period'] != key:
        total = 0
    else:
        delta = sample['counter']-old['counter'] if sample['boot_id'] == old['boot_id'] else sample['counter']
        total = old['used_bytes']+max(0, delta)
    return {**sample, 'period': key, 'period_until': until, 'reset_day': reset_day,
            'used_bytes': max(0, int(total)), 'updated_at': time.time()}


class NodeNetwork:
    def __init__(self, manager, vpn):
        self.m, self.vpn, self.api = manager, vpn, vpn.api
        self.path = manager.root/'node-network.json'
        self.thread = None

    def state(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {'via': {}, 'ports': {}, 'connections': {}, 'order': [], 'tracked': []}

    def nodes(self):
        return [n for n in self.m.store.nodes() if n.get('provisioned', True)]

    def remote(self, node, payload, timeout=120):
        request = {'ip': node['public_ip'], 'ssh_port': node.get('ssh_port', 22), 'host_key_sha256': node.get('host_key_sha256')}
        ssh = self.m.ssh_factory(self.m.root, request)
        try:
            return json.loads(ssh.python(REMOTE, payload, timeout=timeout))
        finally:
            ssh.close()

    def snapshot(self):
        snap = self.vpn.snapshot()
        fields = ('uuid', 'inbound', 'nodes', 'port', 'sni', 'fingerprint', 'isDisabled', 'viewPosition', 'address', 'remark')
        snap['hosts'] = [{k: h.get(k) for k in fields} for h in self.api.items('hosts', 'hosts')]
        snap['network'] = self.state()
        return snap

    def revision(self, snap):
        stable=copy.deepcopy(snap)
        stable['network'].pop('tracked',None)
        return self.vpn.revision(stable)

    def enrich(self, nodes):
        from .visibility import hidden_paths
        state = self.state()
        common = json.loads((self.m.control/'owner-profile.json').read_text()).get('sni')
        active = [n for n in nodes if n.get('provisioned', True)]
        paths, _ = compile_relays(state, active)
        by_id = {n['uuid']:n for n in active}
        blockers = hidden_paths(active,state.get('via',{}))
        def manual(n):
            path=paths.get(n['uuid'],[n['uuid']])
            return {'sni':state.get('connections',{}).get(n['uuid'],{}).get('sni',common),
                    'target':state.get('connections',{}).get(n['uuid'],{}).get('target'),
                    'connect_address':by_id.get(path[0],n)['public_ip'],
                    'port':state['ports'][n['uuid']] if len(path)>1 else 443,'path':path}
        return [n | {'via_uuid': state.get('via', {}).get(n['uuid']),
                     'hidden':bool(n.get('hidden',False)), 'effective_hidden':bool(blockers.get(n['uuid'])),
                     'hidden_by':blockers.get(n['uuid'],[]),
                     'network_auto_eligible': not state.get('via', {}).get(n['uuid']) and state.get('connections', {}).get(n['uuid'], {}).get('sni', common) == common,
                     'relay_accounted': n['uuid'] in state.get('tracked', []),
                     'path_revision':digest(manual(n)), 'manual_connection':manual(n)} for n in nodes]

    def export(self):
        nodes, state = self.nodes(), self.state()
        if not nodes:
            return
        paths, _ = compile_relays(state, nodes)
        by_id = {n['uuid']: n for n in nodes}
        common = json.loads((self.m.control/'owner-profile.json').read_text())
        profiles = {}
        for node in nodes:
            path = paths[node['uuid']]
            profiles[node['public_ip']] = {
                'sni': state.get('connections', {}).get(node['uuid'], {}).get('sni', common['sni']),
                'connect_address': by_id[path[0]]['public_ip'],
                'server_port': state['ports'][node['uuid']] if len(path) > 1 else 443,
                'path': path}
        atomic_json(self.m.control/'node-paths.json', profiles)

    @staticmethod
    def sorted_hosts(snap):
        return sorted(snap['hosts'], key=lambda h: (h.get('viewPosition') or 0, h['uuid']))

    def keys(self, snap, nodes):
        ids = {n.get('host_uuid') for n in nodes}
        ids |= {h['uuid'] for h in snap['hosts'] if h['address'] == 'auto.edge.kingline.online'}
        return [h for h in self.sorted_hosts(snap) if h['uuid'] in ids and not h.get('isDisabled')]

    def expected_endpoint(self, node_id):
        nodes=self.nodes();by_id={n['uuid']:n for n in nodes};state=self.state()
        paths,_=compile_relays(state,nodes)
        path=paths.get(node_id)
        if not path:return None
        return {'address':domains.published_address(self.m, by_id[node_id], by_id[path[0]]['public_ip']),
                'port':state['ports'][node_id] if len(path)>1 else 443}

    def catalog(self):
        snap, nodes = self.snapshot(), self.nodes()
        paths, _ = compile_relays(snap['network'], nodes)
        by_id = {n['uuid']: n for n in nodes}
        display = []
        from .ru_egress import RUEgress
        ru=RUEgress(self.m.root,self.m.control,self.m.ssh_factory)
        ru_state=json.loads(ru.path.read_text()) if ru.path.exists() else {}
        for n in nodes:
            panel = next(p for p in snap['nodes'] if p['uuid'] == n['uuid'])
            profile = next(p for p in snap['profiles'] if p['uuid'] == panel['binding']['activeConfigProfileUuid'])
            raw = inbound(profile['config'])
            h = next((h for h in snap['hosts'] if h['uuid'] == n.get('host_uuid')), {})
            reality = raw['streamSettings']['realitySettings'] if raw else {}
            path = paths[n['uuid']]
            display.append({'uuid': n['uuid'], 'ru_enabled':ru.selected(n,ru_state), 'label': n.get('label', n['name']), 'ip': n['public_ip'],
                'domain': h.get('address') if domains.enabled(self.m) else None,
                'sni': h.get('sni') or reality.get('serverNames', [snap['owner']['sni']])[0],
                'target': reality.get('target', reality.get('dest', '')), 'via': snap['network'].get('via', {}).get(n['uuid']),
                'path': path, 'entry_ip': by_id[path[0]]['public_ip'],
                'entry_port': snap['network']['ports'][n['uuid']] if len(path) > 1 else 443,
                'auto_eligible': not snap['network'].get('via', {}).get(n['uuid']) and (h.get('sni') or snap['owner']['sni']) == snap['owner']['sni']})
        jobs = [h for h in self.vpn.history() if h.get('kind') == 'node_network']
        return {'ok': True, 'revision': self.revision(snap), 'nodes': display,'ru_egress':ru.public(),
                'common_sni':snap['owner']['sni'],
                'keys': [{'uuid': h['uuid'], 'label': h['remark'], 'smart': h['address'] == 'auto.edge.kingline.online'} for h in self.keys(snap, nodes)],
                'jobs': jobs[:10]}

    def submit(self, action, body):
        with self.m.lock:
            with self.m.store.db() as db:
                old = db.execute('SELECT id FROM vpn_changes WHERE request_id=?', (body['request_id'],)).fetchone()
            if old:
                _, data = self.vpn.read(old[0])
                if data.get('request_digest') != digest({'action': action, **body}):
                    raise SafeError('vpn_request_conflict')
                return self.vpn.public(old[0])
            if self.vpn.busy():
                raise SafeError('vpn_operation_busy')
            self.vpn.installation_guard()
            snap = self.snapshot()
            if body['revision'] != self.revision(snap):
                raise SafeError('vpn_revision_changed')
            nodes = self.nodes(); ids = {n['uuid'] for n in nodes}; next_state = copy.deepcopy(snap['network'])
            patches, noop = [], False
            if action == 'connection':
                if body['uuid'] not in ids:
                    raise SafeError('network_nodes_changed')
                row = next(n for n in snap['nodes'] if n['uuid'] == body['uuid'])
                source = next(p for p in snap['profiles'] if p['uuid'] == row['binding']['activeConfigProfileUuid'])
                config = copy.deepcopy(source['config']); primary = inbound(config)
                if primary is None: raise SafeError('vpn_profile_not_supported')
                reality = primary['streamSettings']['realitySettings']
                host = next(h for h in snap['hosts'] if h['uuid'] == next(n['host_uuid'] for n in nodes if n['uuid'] == body['uuid']))
                noop = host['sni'] == body['sni'] and reality.get('target', reality.get('dest')) == body['target']
                # Preserve recent accepted names for clients holding a cached subscription
                # or DNS answer. The published manual SNI still becomes the new one.
                old_names=reality.get('serverNames',[])
                common=[snap['owner']['sni']] if snap['owner']['sni'] in old_names else []
                reality['serverNames'] = list(dict.fromkeys([body['sni'],*common,*old_names]))[:8]
                reality['target' if 'target' in reality else 'dest'] = body['target']
                next_state.setdefault('connections', {})[body['uuid']] = {'sni': body['sni'], 'target': body['target']}
                if not noop: patches.append({'node': body['uuid'], 'profile': source['uuid'], 'config': config})
                targets = [body['uuid']]
            elif action == 'routes':
                route_paths(body['via'], ids)
                noop = body['via'] == {i: next_state['via'].get(i) for i in ids}
                next_state['via'] = body['via']
                for exit_id, parent in sorted(body['via'].items()):
                    if parent and exit_id not in next_state['ports']:
                        used = set(next_state['ports'].values())
                        next_state['ports'][exit_id] = next(p for p in range(24000, 25000) if p not in used)
                targets = [i for i in ids if route_paths({j: snap['network']['via'].get(j) for j in ids}, ids)[i] != route_paths(body['via'], ids)[i]]
            elif action == 'order':
                current = [h['uuid'] for h in self.keys(snap, nodes)]
                if len(body['hosts']) != len(set(body['hosts'])) or set(body['hosts']) != set(current):
                    raise SafeError('network_keys_changed')
                noop = body['hosts'] == current
                next_state['order'] = body['hosts']; targets = []
            else:
                raise SafeError('invalid_request')
            _, before_relays = compile_relays(snap['network'], nodes)
            paths, after_relays = compile_relays(next_state, nodes)
            changed_relays = [i for i in ids if before_relays[i] != after_relays[i]]
            next_state['tracked'] = sorted(set(next_state.get('tracked', [])) | {i for i in ids if after_relays[i]['ports']})
            data = {'kind': 'node_network', 'network_action': action, 'snapshot': snap, 'revision': body['revision'],
                'state': next_state, 'patches': patches, 'relay_plan': {i: after_relays[i] for i in changed_relays},
                'paths_map': paths, 'targets': targets, 'scope': 'selected', 'profile_name': {'order':'Порядок ключей','routes':'Маршруты','connection':'SNI и target'}[action],
                'labels': [n.get('label', n['name']) for n in nodes if n['uuid'] in targets],
                'note': '', 'paths': [], 'diff': [], 'checks': [], 'undo': [], 'noop': noop,
                'request_digest': digest({'action': action, **body})}
            change_id, now = str(uuid.uuid4()), time.time()
            with self.m.store.db() as db:
                db.execute('INSERT INTO vpn_changes VALUES(?,?,?, ?,NULL,?,?,?)', (change_id, body['request_id'],
                    'queued_check', 'queued', self.m.store.cipher.encrypt(json.dumps(data).encode()), now, now))
                db.execute("DELETE FROM vpn_changes WHERE state NOT IN ('queued_check','checking','queued_apply','applying','validated','rollback_failed') AND id NOT IN (SELECT id FROM vpn_changes ORDER BY created DESC LIMIT 30)")
            diagnostics.event('node_network_queued', change_id=change_id, operation=action)
            return self.vpn.public(change_id)

    def check(self, change_id, data):
        if data['noop']:
            self.vpn.write(change_id, 'noop', 'unchanged', data); return
        self.vpn.write(change_id, 'checking', 'checking', data)
        nodes = {n['uuid']: n for n in self.nodes()}
        for p in data['patches']:
            self.vpn.validate_core({'uuid': p['node']}, p['config'])
            data['checks'].append({'node': nodes[p['node']].get('label', nodes[p['node']]['name']), 'check': 'xray_test', 'ok': True})
        data['relay_before'] = {}
        for node_id, plan in data['relay_plan'].items():
            data['relay_before'][node_id] = self.remote(nodes[node_id], {'action': 'snapshot'})
            self.remote(nodes[node_id], {'action': 'check', **plan}, timeout=400)
        self.vpn.write(change_id, 'checking', 'checked', data)
        self.apply(change_id, data)

    def apply(self, change_id, data):
        if self.revision(self.snapshot()) != data['revision']:
            raise SafeError('vpn_revision_changed')
        self.vpn.installation_guard()
        snap, state, api = data['snapshot'], data['state'], self.api
        nodes = {n['uuid']: n for n in self.nodes()}
        self.vpn.write(change_id, 'applying', 'configuring', data)
        self.vpn.journal(change_id, data, {'network_state': snap['network']})
        binding_updates = {}
        for patch in data['patches']:
            source = next(p for p in snap['profiles'] if p['uuid'] == patch['profile'])
            shared = source['uuid'] == snap['owner']['profile_uuid'] or sum(n['binding']['activeConfigProfileUuid'] == source['uuid'] for n in snap['nodes']) > 1
            node = next(n for n in snap['nodes'] if n['uuid'] == patch['node'])
            if shared:
                name = 'KL-'+change_id.replace('-', '')[:26]
                self.vpn.journal(change_id, data, {'clone_name': name})
                tags = {i['tag']: 'KL_'+change_id.replace('-', '')[:20]+'_'+str(index) for index, i in enumerate(patch['config']['inbounds'])}
                profile = api.call('config-profiles', 'POST', {'name': name, 'config': rename_tags(patch['config'], tags)})
                profile = api.call('config-profiles/'+profile['uuid'])
                mapping = {old['uuid']: next(i['uuid'] for i in profile['inbounds'] if i['tag'] == tags[old['tag']]) for old in source['inbounds']}
                for squad in snap['squads']:
                    added = [mapping[i] for i in squad['inbounds'] if i in mapping]
                    if added: self.vpn.patch(change_id, data, 'internal-squads', {'uuid': squad['uuid'], 'inbounds': squad['inbounds']+added}, squad)
                self.vpn.patch(change_id, data, 'nodes', {'uuid': node['uuid'], 'configProfile': {
                    'activeConfigProfileUuid': profile['uuid'], 'activeInbounds': [mapping[i] for i in node['binding']['activeInbounds']]}},
                    {'uuid': node['uuid'], 'configProfile': node['binding']})
                old_host = next(h for h in snap['hosts'] if h['uuid'] == nodes[node['uuid']]['host_uuid'])
                binding_updates[node['uuid']] = {'configProfileUuid': profile['uuid'], 'configProfileInboundUuid': mapping[old_host['inbound']['configProfileInboundUuid']]}
            else:
                self.vpn.patch(change_id, data, 'config-profiles', {'uuid': source['uuid'], 'config': patch['config']}, {'uuid': source['uuid'], 'config': source['config']})
        for node_id in sorted(data['relay_plan'], key=lambda i: len(data['paths_map'][i]), reverse=True):
            self.vpn.write(change_id, 'applying', 'relays', data)
            self.vpn.journal(change_id, data, {'relay_node': nodes[node_id], 'before': data['relay_before'][node_id]})
            self.remote(nodes[node_id], {'action': 'apply', **data['relay_plan'][node_id]}, timeout=400)
            self.record_usage(nodes[node_id], data['relay_before'][node_id]['usage'])
        self.vpn.write(change_id, 'applying', 'verifying', data)
        for node_id in data['targets']:
            node = nodes[node_id]; path = data['paths_map'][node_id]
            candidate = {**snap['owner'], 'sni': state['connections'].get(node_id, {}).get('sni', snap['owner']['sni'])}
            for attempt in range(4):
                try:
                    self.m.probe(self.m.control, node['public_ip'], state_override=candidate,
                        connect_address=nodes[path[0]]['public_ip'], server_port=state['ports'][node_id] if len(path)>1 else 443)
                    break
                except Exception:
                    if attempt == 3: raise SafeError('network_tunnel_check_failed') from None
                    if self.m.stop.wait(3): raise SafeError('worker_stopping')
            data['checks'].append({'node': node.get('label', node['name']), 'check': 'https_udp', 'ok': True})
        self.vpn.write(change_id, 'applying', 'publishing', data)
        if domains.enabled(self.m):
            domains.sync(self.m.control, list(nodes.values()), state)
        for node_id in data['targets']:
            node, path = nodes[node_id], data['paths_map'][node_id]
            host = next(h for h in snap['hosts'] if h['uuid'] == node['host_uuid'])
            value = {'uuid': host['uuid'], 'address': domains.published_address(self.m, node, nodes[path[0]]['public_ip']),
                'port': state['ports'][node_id] if len(path)>1 else 443,
                'sni': state['connections'].get(node_id, {}).get('sni', host['sni'])}
            if node_id in binding_updates: value['inbound'] = binding_updates[node_id]
            if any(value[k] != host[k] for k in value):
                self.vpn.patch(change_id, data, 'hosts', value, {k:host[k] for k in value})
        if data['network_action'] == 'order':
            before = {'hosts': [{'uuid':h['uuid'],'viewPosition':h.get('viewPosition') or 0} for h in snap['hosts']]}
            ordered = self.reorder_body(snap, state['order'])
            self.vpn.journal(change_id, data, {'post_path': 'hosts/actions/reorder', 'before': before})
            api.call('hosts/actions/reorder', 'POST', ordered)
        atomic_json(self.path, state); self.m.export_inventory()
        data['after_network_revision'] = self.revision(self.snapshot())
        self.vpn.write(change_id, 'ready', 'ready', data)
        diagnostics.event('node_network_applied', change_id=change_id, operation=data['network_action'])

    def reorder_body(self, snap, desired):
        result, sequence = [], iter(desired)
        chosen = set(desired)
        for host in self.sorted_hosts(snap):
            result.append(next(sequence) if host['uuid'] in chosen else host['uuid'])
        return {'hosts': [{'uuid': v, 'viewPosition': i} for i, v in enumerate(result)]}

    def restore_step(self, step):
        if 'relay_node' in step:
            self.remote(step['relay_node'], {'action':'restore', 'before':step['before']}, timeout=180)
        elif 'network_state' in step:
            previous=copy.deepcopy(step['network_state'])
            ledger=self.m.control/'relay-usage.json'
            tracked=set(json.loads(ledger.read_text())) if ledger.exists() else set()
            previous['tracked']=sorted(set(previous.get('tracked',[]))|set(self.state().get('tracked',[]))|tracked)
            atomic_json(self.path, previous); self.m.export_inventory()
            if domains.enabled(self.m):
                domains.sync(self.m.control, self.nodes(), previous)
        elif 'post_path' in step:
            self.api.call(step['post_path'], 'POST', step['before'])
        else:
            return False
        return True

    def rollback(self, change_id, revision):
        with self.m.lock:
            row, data = self.vpn.read(change_id)
            if data.get('kind') != 'node_network' or row['state'] not in ('ready','rollback_failed') or not data.get('undo'):
                raise SafeError('vpn_rollback_unavailable')
            with self.m.store.db() as db:
                if db.execute("SELECT 1 FROM vpn_changes WHERE id!=? AND state IN ('queued_check','checking','queued_apply','applying','rollback_failed')",(change_id,)).fetchone():
                    raise SafeError('vpn_operation_busy')
            self.vpn.installation_guard()
            current = self.revision(self.snapshot())
            if current != revision or (row['state']=='ready' and current != data['after_network_revision']):
                raise SafeError('vpn_rollback_revision_changed')
            self.vpn.write(change_id,'queued_apply','rollback_requested',data)
            return self.vpn.public(change_id)

    def removal_guard(self, node_id):
        via = self.state().get('via', {})
        if node_id in via.values(): raise SafeError('network_node_has_dependents')
        if via.get(node_id): raise SafeError('network_disconnect_before_remove')

    def on_node_added(self, host_id):
        state = self.state()
        if not state['order'] or host_id in state['order']: return
        snap = self.snapshot()
        visible = [h['uuid'] for h in self.keys(snap, self.nodes())]
        desired = [i for i in state['order'] if i in visible]+[i for i in visible if i not in state['order']]
        self.api.call('hosts/actions/reorder','POST',self.reorder_body(snap,desired))
        state['order']=desired;atomic_json(self.path,state)

    def record_usage(self, node, sample):
        path = self.m.control/'relay-usage.json'
        report = json.loads(path.read_text()) if path.exists() else {}
        # Never derive bandwidth usage from customer URLs or payload logs.
        panel = self.api.call('nodes/'+node['uuid'])
        report[node['uuid']] = usage_sample(report.get(node['uuid']),sample,node['reset_day'],panel['trafficUsedBytes'])
        atomic_json(path, report)

    def start(self):
        def loop():
            while not self.m.stop.wait(60):
                try:
                    ids=set(self.state().get('tracked',[]))
                    tracked=[n for n in self.nodes() if n['uuid'] in ids]
                    def poll(node):
                        try: return node,self.remote(node,{'action':'stats'},timeout=30)
                        except Exception: return node,None
                    with ThreadPoolExecutor(max_workers=3) as pool:
                        for node,sample in pool.map(poll,tracked):
                            if sample:
                                with self.m.lock: self.record_usage(node,sample)
                except Exception:
                    diagnostics.event('relay_accounting_failed','ERROR')
        self.thread=threading.Thread(target=loop,daemon=True,name='relay-accounting');self.thread.start()
