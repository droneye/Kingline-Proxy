import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import copy
import uuid

from fastapi.testclient import TestClient
import pytest

from node_manager.app import create_app
from node_manager.manager import Manager
from node_manager.models import AddNode, Credentials, Capacity, RemoveNode, InstallationAction
from node_manager.ssh import SafeError
from node_manager.store import Store


def request(**changes):
    return AddNode.model_validate({'ip': '8.8.4.4', 'name': 'FI-Test-01', 'label': 'Финляндия',
        'country': 'FI', 'quota_tb': 5, 'speed_mbps': 500, 'credentials': {'password': 'never-log-this'}, **changes})


def test_payment_reminders_private_api_keeps_admin_and_owner_checks(manager):
    from node_manager.store import atomic_json
    atomic_json(manager.control/'ru-monitor.json',{'owner_id':42,'enabled':False})
    client=TestClient(create_app(manager,run_worker=False))
    for path in ('claim','ack','callback'):
        assert client.post('/v1/node-billing/bot/'+path,json={'actor_id':42}).status_code==401
    headers={'Authorization':'Bearer admin-test-token'}
    assert client.post('/v1/node-billing/bot/claim',json={'actor_id':43},headers=headers).status_code==409
    assert client.post('/v1/node-billing/bot/claim',json={'actor_id':42},headers=headers).json()['items']==[]
    assert client.post('/v1/node-billing/bot/callback',json={'actor_id':42,'data':'kb:'+'x'*24},headers=headers).status_code==409


class FakeAPI:
    def __init__(self):
        self.nodes, self.hosts, self.creates = [], [{'uuid': 'smart', 'address': 'auto.edge.kingline.online', 'nodes': []}], []
        self.fail_publish = False
        self.profiles = [{'uuid':'shared-profile', 'name':'Standard', 'config': {'inbounds': [
            {'tag':'KINGLINE_XHTTP_REALITY','port':443,'settings':{'flow':'xtls-rprx-vision'},
             'streamSettings':{'network':'tcp','security':'reality','realitySettings':{
                 'serverNames':['www.samsung.com'],'target':'www.samsung.com:443'}}},
            {'tag':'KINGLINE_XHTTP_TLS','port':10080,'settings':{},'streamSettings':{'network':'xhttp'}}]},
            'inbounds':[{'tag':'KINGLINE_XHTTP_REALITY','uuid':'shared-inbound'},
                        {'tag':'KINGLINE_XHTTP_TLS','uuid':'xhttp-inbound'}]}]
        self.squads=[]
        self.users=[{'id':1,'username':'service_probe_fixture','vlessUuid':str(uuid.uuid4()),
                     'activeInternalSquads':['standard-squad']}]
        self.collections={'nodes':self.nodes,'hosts':self.hosts,'config-profiles':self.profiles,
                          'internal-squads':self.squads,'users':self.users}

    def items(self, path, key):
        # Tests sometimes replace these two collections directly.
        if path=='nodes':return self.nodes
        if path=='hosts':return self.hosts
        return self.collections[path]

    def call(self, path, method='GET', body=None):
        kind, _, identity = path.partition('/')
        items = self.items(kind, '') if kind in self.collections else []
        if path.startswith('users/by-username/'):
            user=next((u for u in self.users if u['username']==path.rsplit('/',1)[1]),None)
            if user is None:raise RuntimeError('Remnawave API GET failed with HTTP 404')
            return copy.deepcopy(user)
        if method == 'DELETE':
            items[:] = [item for item in items if str(item.get('uuid',item.get('id'))) != identity]
            return None
        if path == 'keygen':return {'secretKey':'panel-node-secret'}
        if method=='GET' and identity:
            return copy.deepcopy(next(n for n in items if str(n.get('uuid',n.get('id'))) == identity))
        if method=='GET':return items
        if method=='POST':
            if kind in ('config-profiles','internal-squads'):assert len(body['name'])<=30
            item=copy.deepcopy(body)
            if kind=='nodes':
                self.creates.append('node')
                item.update(uuid='11111111-1111-4111-a111-111111111111',isConnected=True,xrayUptime=20)
            elif kind=='hosts':self.creates.append('host');item['uuid']='manual'
            elif kind=='config-profiles':
                item.update(uuid=str(uuid.uuid4()),inbounds=[{'uuid':str(uuid.uuid4()),'tag':i['tag']} for i in item['config']['inbounds']])
            elif kind=='users':item.update(id=max(u['id'] for u in self.users)+1,vlessUuid=str(uuid.uuid4()))
            else:item['uuid']=str(uuid.uuid4())
            items.append(item);return copy.deepcopy(item)
        if method=='PATCH':
            if body.get('uuid')=='smart' and self.fail_publish:raise RuntimeError('publish failed')
            key='id' if kind=='users' else 'uuid'
            item=next(i for i in items if i[key]==body[key]);item.update(copy.deepcopy(body));return copy.deepcopy(item)
        raise AssertionError((path,method))


class FakeSSH:
    fingerprint = 'SHA256:' + 'a' * 43
    fail_preflight = False

    def __init__(self, *args): pass
    def close(self): pass
    def upload(self, *args): pass
    def python(self, source, payload):
        return json.dumps({'ok': not self.fail_preflight, 'error': 'existing_service_requires_manual_migration', 'ubuntu': '24.04'})
    def run(self, command, payload=None, timeout=None):
        if command == 'id -u': return '0'
        if command.endswith(' base'): return json.dumps({'public_key': 'a' * 43 + '=', 'reboot_required': False})
        if command.endswith(' configure'): return json.dumps({'configured': True, 'reboot_required': False})
        return ''


@pytest.fixture
def manager(tmp_path, monkeypatch):
    root, control = tmp_path / 'data', tmp_path / 'control'
    root.mkdir(); control.mkdir()
    (root / 'settings.json').write_text(json.dumps({'control_directory': str(control),
        'core_public_ip': '198.51.100.10', 'protected_ips': ['198.51.100.10'], 'node_image': 'pinned'}))
    (root / 'admin.token').write_text('admin-test-token')
    (root / 'id_ed25519.pub').write_text('ssh-ed25519 test-pub')
    (control / 'owner-profile.json').write_text(json.dumps({'profile_uuid': 'shared-profile',
        'inbound_uuid': 'shared-inbound', 'fingerprint': 'firefox', 'sni': 'www.samsung.com',
        'reality':{'publicKey':'fixture-public-key'},'short_id':'abcd'}))
    api = FakeAPI()
    (control / 'probe-user.json').write_text(json.dumps({'id':1,'vlessUuid':api.users[0]['vlessUuid']}))
    result = Manager(root, api=api, ssh_factory=FakeSSH,
                     probe=lambda *_: {'https': True, 'udp_dns': True, 'source': 'simulated'})
    result.core_peer = lambda *args: None
    result.onboarding.remote_check = lambda _: {'ok':True,'source_ok':True,'transports':{}}
    real_read = Path.read_text
    monkeypatch.setattr(Path, 'read_text', lambda p, *a, **kw:
                        'test-core-pub' if str(p) == '/etc/wireguard/control.pub' else real_read(p, *a, **kw))
    return result


def approve(manager, identifier):
    review=manager.onboarding.public(identifier)
    manager.onboarding.action(InstallationAction(job_id=identifier,operation='approve',
        request_id=str(uuid.uuid4()),revision=review['revision']))
    manager.execute(identifier)


def complete(manager, identifier):
    manager.execute(identifier)
    if manager.store.get(identifier)['state']=='waiting_review':approve(manager,identifier)


def test_success_publishes_only_after_tunnel_check_and_retains_standard(manager):
    observed = []
    def probe(*args):
        assert not manager.store.nodes()
        assert len(manager.api.hosts) == 1
        observed.append(True)
        return {'https': True, 'udp_dns': True}
    manager.probe = probe
    job = manager.submit(request(), 'success-1')
    assert manager.store.claim() == job['id']
    complete(manager, job['id'])
    result = manager.store.get(job['id'])
    assert result['state'] == 'ready' and result['secret'] is None
    assert observed and len(manager.store.nodes()) == 1
    assert manager.api.nodes[0]['configProfile']['activeInbounds'] == ['shared-inbound']
    assert manager.api.hosts[-1]['fingerprint'] == 'firefox'
    assert manager.api.hosts[-1]['isDisabled'] is False
    assert manager.api.hosts[0]['nodes'] == [manager.store.nodes()[0]['uuid']]
    assert manager.submit(request(), 'success-1')['id'] == job['id']
    assert manager.api.creates == ['node', 'host']


def test_failed_vpn_never_publishes_then_retry_reuses_resources(manager):
    manager.probe = lambda *_: (_ for _ in ()).throw(SafeError('encrypted_https_exit_check_failed'))
    job = manager.submit(request(), 'retry-test')
    complete(manager, job['id'])
    result = manager.store.get(job['id'])
    assert result['state'] == 'failed' and result['secret'] is None
    assert not manager.store.nodes() and len(manager.api.hosts) == 1
    manager.probe = lambda *_: {'https': True, 'udp_dns': True}
    manager.store.retry(job['id'], Credentials())
    complete(manager, job['id'])
    assert manager.store.get(job['id'])['state'] == 'ready'
    assert manager.api.creates == ['node', 'host']


def test_partial_publish_failure_keeps_host_disabled_and_retry_does_not_duplicate(manager):
    manager.api.fail_publish = True
    job = manager.submit(request(), 'publish-retry')
    complete(manager, job['id'])
    assert manager.store.get(job['id'])['state'] == 'failed'
    assert manager.api.hosts[-1]['isDisabled'] is True
    assert not manager.store.nodes()
    manager.api.fail_publish = False
    manager.store.retry(job['id'], Credentials())
    complete(manager, job['id'])
    assert manager.store.get(job['id'])['state'] == 'ready'
    assert manager.api.creates == ['node', 'host']


def test_credentials_encrypted_and_idempotency_reserves_unique_address(manager):
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        jobs = list(executor.map(lambda _: manager.submit(request(), 'same-request'), range(4)))
    assert len({j['id'] for j in jobs}) == 1
    raw = b''.join(p.read_bytes() for p in manager.root.glob('manager.sqlite3*'))
    assert b'never-log-this' not in raw
    with pytest.raises(ValueError, match='idempotency_key_conflict'):
        manager.submit(request(speed_mbps=1000), 'same-request')
    with pytest.raises(ValueError):
        manager.submit(request(), 'other-request')


def test_api_requires_auth_sanitizes_errors_and_probe_token_has_no_admin_rights(manager):
    app = create_app(manager, run_worker=False)
    client = TestClient(app)
    assert client.get('/v1/nodes').status_code == 401
    token = {'Authorization': 'Bearer admin-test-token'}
    invalid = request().model_dump(mode='json')
    invalid['credentials'] = {'password': 'NEVER-RETURN-THIS', 'invalid': 'NEVER-RETURN-THIS'}
    r = client.post('/v1/nodes', json=invalid, headers={**token, 'Idempotency-Key': 'request-1'})
    assert r.status_code == 422 and 'NEVER-RETURN-THIS' not in r.text
    for ip in ['127.0.0.1', '169.254.169.254', '10.77.0.2', '0.0.0.0', '::1', 'example.com']:
        invalid['ip'] = ip
        r = client.post('/v1/nodes', json=invalid, headers={**token, 'Idempotency-Key': 'request-1'})
        assert r.status_code == 422
    with manager.store.db() as db:
        db.execute('INSERT INTO probes VALUES (?,?,?,?)', ('pi', hashlib.sha256(b'probe-only').hexdigest(), 'RU-home', 0))
    probe_headers = {'Authorization': 'Bearer probe-only'}
    assert client.get('/v1/probes/targets', headers=probe_headers).status_code == 200
    assert client.get('/v1/nodes', headers=probe_headers).status_code == 401
    assert client.get('/v1/openapi.json', headers=token).status_code == 200
    assert client.get('/v1/openapi.json').status_code == 401


def test_probe_sequence_protection_and_capacity_preserves_usage(manager):
    job = manager.submit(request(), 'capacity-test')
    complete(manager, job['id'])
    node = manager.store.nodes()[0]
    manager.api.nodes[0]['trafficUsedBytes'] = 1000
    updated = manager.capacity(node['uuid'], Capacity(speed_mbps=1000, quota_tb=8, reset_day=7))
    assert updated['speed_mbps'] == 1000 and updated['quota_bytes'] == 8 * 10**12
    assert manager.api.nodes[0]['trafficUsedBytes'] == 1000
    assert updated['reset_day'] == 7
    with manager.store.db() as db:
        db.execute('INSERT INTO probes VALUES (?,?,?,?)', ('pi', hashlib.sha256(b'probe-only').hexdigest(), 'RU-home', 1))
    client = TestClient(create_app(manager, run_worker=False))
    for sequence in range(1, 4):
        r = client.post('/v1/probes/reports', headers={'Authorization': 'Bearer probe-only'},
                        json={'node_uuid': node['uuid'], 'sequence': sequence, 'vpn_ok': False, 'error': 'timeout'})
        assert r.status_code == 200
    r = client.post('/v1/probes/reports', headers={'Authorization': 'Bearer probe-only'},
                    json={'node_uuid': node['uuid'], 'sequence': 2, 'vpn_ok': True})
    assert r.status_code == 409
    observations = json.loads((manager.control / 'regional-probes.json').read_text())['observations']
    assert observations[0]['failures'] == 3


def test_standard_drift_stops_before_ssh_or_install(manager):
    file = manager.control / 'owner-profile.json'
    state = json.loads(file.read_text()); state['fingerprint'] = 'unsupported'; file.write_text(json.dumps(state))
    job = manager.submit(request(), 'drift-test')
    complete(manager, job['id'])
    assert manager.store.get(job['id'])['error'] == 'standard_profile_drift'
    assert not manager.api.creates


def test_declared_chain_endpoint_does_not_block_future_standard_onboarding(manager):
    from node_manager.store import atomic_json
    job=manager.submit(request(),'chain-standard');complete(manager, job['id'])
    node=manager.store.nodes()[0]
    other={'uuid':'22222222-2222-4222-a222-222222222222','name':'Other','public_ip':'9.9.9.9','provisioned':True}
    manager.store.save_node(other)
    state=manager.network.state();state.update(via={node['uuid']:other['uuid'],other['uuid']:None},ports={node['uuid']:24000})
    atomic_json(manager.network.path,state)
    manager.settings['enforce_uniform_profiles']=True
    host=manager.api.hosts[1];host.update(address='9.9.9.9',port=24000)
    assert manager.standard()['profile_uuid']=='shared-profile'
    host['port']=24001
    with pytest.raises(SafeError,match='published_profile_drift'):manager.standard()


def test_target_drift_blocks_installation_even_with_unchanged_sni(manager):
    file = manager.control / 'owner-profile.json'
    state = json.loads(file.read_text())
    state['reality_target'] = 'unexpected.example:443'
    file.write_text(json.dumps(state))
    job = manager.submit(request(), 'target-drift')
    complete(manager, job['id'])
    assert manager.store.get(job['id'])['error'] == 'standard_target_drift'
    assert not manager.api.creates


def test_standard_exposes_actual_remote_target_after_owned_https_rollback(manager):
    standard = manager.standard_public()
    assert standard['target'] == 'www.samsung.com:443'
    assert not standard['owned_https']


def test_uniform_policy_rejects_one_host_with_different_port_sni_or_fingerprint(manager):
    manager.settings['enforce_uniform_profiles'] = True
    common = {'uuid': 'smart', 'address': 'auto.edge.kingline.online', 'port': 443,
              'sni': 'www.samsung.com', 'fingerprint': 'firefox',
              'inbound': {'configProfileUuid': 'shared-profile', 'configProfileInboundUuid': 'shared-inbound'}}
    manager.api.hosts = [common]
    assert manager.standard_public()['uniform_profiles_enforced']
    for change in [{'port': 36847}, {'sni': 'aws.amazon.com'}, {'fingerprint': 'chrome'},
                   {'inbound': {'configProfileUuid': 'shared-profile', 'configProfileInboundUuid': 'another'}}]:
        manager.api.hosts = [common, {**common, 'uuid': 'bad', **change}]
        with pytest.raises(SafeError, match='published_profile_drift'):
            manager.standard()


def test_failed_https_target_never_registers_or_publishes_new_node(manager):
    state = manager.standard()
    manager.standard = lambda: state
    class BrokenCover:
        def deploy(self, ssh):
            raise SafeError('cover_target_verification_failed')
    manager.cover = BrokenCover()
    job = manager.submit(request(), 'bad-cover')
    complete(manager, job['id'])
    assert manager.store.get(job['id'])['error'] == 'cover_target_verification_failed'
    assert not manager.api.creates and not manager.store.nodes()


def test_owned_https_is_prepared_before_registration_and_dns_is_published_after_probe(manager, monkeypatch):
    from node_manager import manager as module
    state = manager.standard()
    manager.standard = lambda: state
    class GoodCover:
        def deploy(self, ssh):
            assert not manager.api.creates
            return {'domain': 'edge.kingline.online', 'scanner_verified': True}
    manager.cover = GoodCover()
    calls = []
    def sync(control, nodes):
        assert nodes[0]['verification']['https']
        assert nodes[0]['cover']['scanner_verified']
        calls.append(nodes[0]['public_ip'])
    monkeypatch.setattr(module, 'sync_cover_dns', sync)
    job = manager.submit(request(), 'owned-cover')
    complete(manager, job['id'])
    assert manager.store.get(job['id'])['state'] == 'ready'
    assert calls == ['8.8.4.4']


def test_remove_node_requires_confirmation_and_preserves_other_location(manager):
    job = manager.submit(request(), 'remove-test')
    complete(manager, job['id'])
    target = manager.store.nodes()[0]
    other = {'uuid': '22222222-2222-4222-a222-222222222222', 'name': 'DE-Other-01',
             'public_ip': '9.9.9.9', 'management_ip': '10.77.0.2', 'provisioned': True}
    manager.store.save_node(other)
    manager.api.nodes.append({'uuid': other['uuid']})
    manager.api.hosts[0]['nodes'].append(other['uuid'])
    manager.remove_core_peer = lambda node: None
    calls = []
    class RecordingSSH:
        fail_stop = True
        def run(self, command, payload=None, timeout=None):
            calls.append(command)
            if 'docker compose down' in command and self.fail_stop:
                raise SafeError('remote_operation_failed')
            return '0' if command == 'id -u' else ''
        def close(self): pass
    manager.ssh_factory = lambda *args: RecordingSSH()
    client = TestClient(create_app(manager, run_worker=False))
    endpoint = '/v1/nodes/' + target['uuid']
    headers = {'Authorization': 'Bearer admin-test-token'}
    assert client.request('DELETE', endpoint, json={'name': target['name'], 'ip': target['public_ip']}).status_code == 401
    assert client.request('DELETE', endpoint, headers=headers,
                          json={'name': target['name'], 'ip': other['public_ip']}).status_code == 409
    assert not calls
    legacy = client.request('DELETE', '/v1/nodes/' + other['uuid'], headers=headers,
                            json={'name': other['name'], 'ip': other['public_ip']})
    assert legacy.status_code == 409 and legacy.json()['detail'] == 'node_not_managed_by_backend'
    assert not calls
    response = client.request('DELETE', endpoint, headers=headers,
                              json={'name': target['name'], 'ip': target['public_ip']})
    assert response.status_code == 409
    assert manager.store.removal(target['uuid'])['state'] == 'failed'
    assert manager.store.removal(target['uuid'])['phase'] == 'stopping_container'
    assert manager.store.nodes()[0]['provisioned'] is False
    assert manager.api.hosts[0]['nodes'] == [other['uuid']]
    RecordingSSH.fail_stop = False
    response = client.request('DELETE', endpoint, headers=headers,
                              json={'name': target['name'], 'ip': target['public_ip']})
    assert response.status_code == 200 and response.json()['removed'] is True
    assert any('docker compose down' in command for command in calls)
    assert manager.store.nodes() == [other]
    assert manager.api.hosts == [{'uuid': 'smart', 'address': 'auto.edge.kingline.online',
                                  'nodes': [other['uuid']]}]
    assert manager.api.nodes == [{'uuid': other['uuid']}]
    assert manager.store.get(job['id'])['state'] == 'removed'
    exported=json.loads((manager.control / 'nodes.json').read_text())
    assert len(exported)==1 and {k:exported[0][k] for k in other}==other
    assert exported[0]['network_auto_eligible'] is True
    calls.clear()
    assert client.request('DELETE', endpoint, headers=headers,
                          json={'name': target['name'], 'ip': target['public_ip']}).status_code == 200
    assert not calls
    assert client.get('/v1/removals/' + target['uuid'], headers=headers).json()['state'] == 'removed'
    with pytest.raises(ValueError, match='retired_address_requires_manual_reset'):
        manager.submit(request(), 're-add-retired-address')


def test_display_name_and_flag_are_separate_and_old_labels_still_work():
    node = AddNode(ip='8.8.4.4', display_name='Турция · Стамбул', flag_code='TR', country='TR',
                   quota_tb=2, speed_mbps=25000)
    assert node.name == 'TR-8-8-4-4'
    assert node.label == '🇹🇷 Турция · Стамбул'
    assert node.flag_code == 'TR'
    old = request(label='🇫🇮 Финляндия')
    assert old.display_name == 'Финляндия' and old.flag_code == 'FI'
    with pytest.raises(ValueError):
        request(display_name='Another name', flag_code='FI')
    with pytest.raises(ValueError):
        request(label='🇫🇮 Финляндия', flag_code='TR')


def test_old_requests_keep_idempotency_after_presentation_fields_are_added(manager):
    job = manager.submit(request(), 'old-presentation')
    with manager.store.db() as db:
        old = json.loads(db.execute('SELECT request FROM jobs WHERE id=?', (job['id'],)).fetchone()[0])
        old.pop('display_name'); old.pop('flag_code')
        db.execute('UPDATE jobs SET request=? WHERE id=?', (json.dumps(old, sort_keys=True), job['id']))
    assert manager.submit(request(), 'old-presentation')['id'] == job['id']


def test_legacy_node_removal_preserves_dns_management_network(manager, monkeypatch):
    from node_manager import manager as module
    target = {'uuid': 'legacy-de', 'name': 'DE-Old-01', 'public_ip': '8.8.4.4',
              'management_ip': '10.77.0.2', 'host_uuid': 'legacy-host', 'managed_ssh': True,
              'cover': {'domain': 'edge.kingline.online'}, 'manual_connection': {'port': 36847}}
    other = {'uuid': 'legacy-dk', 'name': 'DK-Other-01', 'public_ip': '9.9.9.9',
             'management_ip': '10.77.0.3'}
    for node in (target, other):
        manager.store.save_node(node)
    manager.api.nodes = [{'uuid': target['uuid']}, {'uuid': other['uuid']}]
    manager.api.hosts[0]['nodes'] = [target['uuid'], other['uuid']]
    manager.api.hosts.append({'uuid': 'legacy-host', 'address': target['public_ip'],
                              'nodes': [target['uuid']], 'isDisabled': False})
    commands = []
    class RecordingSSH(FakeSSH):
        def run(self, command, *args, **kwargs):
            commands.append(command)
            return super().run(command, *args, **kwargs)
    manager.ssh_factory = RecordingSSH
    manager.cover = object()
    dns_updates = []
    monkeypatch.setattr(module, 'sync_cover_dns', lambda control, nodes:
                        dns_updates.append([n['public_ip'] for n in nodes if n.get('provisioned', True)]))
    manager.remove_core_peer = lambda _: pytest.fail('Legacy DNS tunnel must survive VPN removal')
    result = manager.remove(target['uuid'], RemoveNode(name=target['name'], ip=target['public_ip']))
    assert result['removed']
    assert any('docker compose down' in command for command in commands)
    assert not any('wg-quick' in command for command in commands)
    assert any('rm -rf /opt/kingline-cover' in command for command in commands)
    assert any('kingline mobile 36847' in command for command in commands)
    assert dns_updates == [['9.9.9.9']]
    assert manager.store.nodes() == [other]


def test_worker_recovers_from_transient_queue_failure(manager):
    from unittest.mock import Mock
    import sqlite3
    calls=[]
    original=manager.store.claim
    def claim():
        calls.append(1)
        if len(calls)==1:raise sqlite3.OperationalError('database temporarily busy')
        manager.stop.set();return None
    manager.store.claim=claim
    manager.stop.wait=Mock(return_value=False)
    try:
        manager.worker()
        assert len(calls)==2
        manager.stop.wait.assert_any_call(5)
    finally:manager.store.claim=original


def test_manual_only_install_still_publishes_its_manual_profile(manager):
    job = manager.submit(request(smart_enabled=False), 'manual-only')
    complete(manager, job['id'])
    node = manager.store.nodes()[0]
    assert manager.store.get(job['id'])['state'] == 'ready'
    assert node['smart_enabled'] is False and node['provisioned']
    assert manager.api.hosts[-1]['isDisabled'] is False
    exported = json.loads((manager.control/'nodes.json').read_text())
    assert exported[0]['smart_enabled'] is False


def test_smart_choice_change_preserves_manual_host_and_panel_config(manager):
    job = manager.submit(request(), 'change-smart-only')
    complete(manager, job['id'])
    node = manager.store.nodes()[0]
    assert node['smart_enabled'] is True
    before = json.dumps({'nodes':manager.api.nodes,'hosts':manager.api.hosts},sort_keys=True)
    for enabled in (False, True):
        updated = manager.capacity(node['uuid'], Capacity(smart_enabled=enabled))
        assert updated['smart_enabled'] is enabled
        assert json.loads((manager.control/'nodes.json').read_text())[0]['smart_enabled'] is enabled
        assert json.dumps({'nodes':manager.api.nodes,'hosts':manager.api.hosts},sort_keys=True) == before


def test_domains_are_verified_before_publishing_new_node(manager, monkeypatch):
    from node_manager import domains
    manager.settings['node_domains'] = True
    calls = []
    def sync(control, nodes, network):
        assert not any(not h.get('isDisabled', True) for h in manager.api.hosts[1:]) or len(nodes) == 1
        calls.append(nodes)
    monkeypatch.setattr(domains, 'sync', sync)
    job = manager.submit(request(), 'domain-onboarding')
    manager.store.claim();complete(manager, job['id'])
    assert manager.store.get(job['id'])['state'] == 'ready'
    assert manager.api.hosts[-1]['address'] == domains.hostname(manager.store.nodes()[0])
    assert calls


def test_domain_failure_cannot_publish_raw_ip_fallback(manager, monkeypatch):
    from node_manager import domains
    manager.settings['node_domains'] = True
    def fail(*args):raise SafeError('node_domains_replica_update_failed')
    monkeypatch.setattr(domains, 'sync', fail)
    job = manager.submit(request(), 'domain-onboarding-failure')
    manager.store.claim();complete(manager, job['id'])
    assert manager.store.get(job['id'])['state'] == 'failed'
    assert len(manager.api.hosts) == 1
    assert not manager.store.nodes()


def test_new_nodes_activate_and_verify_both_transports_before_publication(manager):
    manager.settings['xhttp']={'path':'/fixture/transport123/','inbound_uuid':'xhttp-inbound'}
    calls=[]
    def probe(*args,**kwargs):
        assert len(manager.api.hosts)==1
        calls.append(kwargs.get('transport','raw'))
        return {'https':True,'udp_dns':True}
    manager.probe=probe
    job=manager.submit(request(),'two-transports');manager.store.claim();complete(manager, job['id'])
    assert manager.store.get(job['id'])['state']=='ready'
    assert calls==['raw','xhttp','raw','xhttp']
    assert manager.api.nodes[0]['configProfile']['activeInbounds']==['shared-inbound','xhttp-inbound']


def test_new_node_is_not_published_when_xhttp_fails(manager):
    manager.settings['xhttp']={'path':'/fixture/transport123/','inbound_uuid':'xhttp-inbound'}
    def probe(*args,**kwargs):
        if kwargs.get('transport')=='xhttp':raise SafeError('xhttp_probe_failed')
        return {'https':True,'udp_dns':True}
    manager.probe=probe
    job=manager.submit(request(),'xhttp-failure');manager.store.claim();complete(manager, job['id'])
    assert manager.store.get(job['id'])['state']=='failed'
    assert len(manager.api.hosts)==1 and not manager.store.nodes()
