import copy
import json
import threading
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from node_manager.app import create_app
from node_manager.store import Store, atomic_json
from node_manager.ssh import SafeError
from node_manager.vpn_config import VPNConfig, KEEP, masked


def uid():
    return str(uuid.uuid4())


class Panel:
    def __init__(self):
        self.pid, self.iid = uid(), uid()
        self.config = {'log': {'loglevel': 'warning', 'access': 'none', 'error': '/dev/stderr'},
            'inbounds': [{'tag': 'KINGLINE_XHTTP_REALITY', 'listen': '0.0.0.0', 'port': 443, 'protocol': 'vless',
                'settings': {'clients': [], 'decryption': 'none', 'flow': 'xtls-rprx-vision'},
                'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': {
                    'serverNames': ['example.org'], 'target': 'example.org:443', 'shortIds': ['aabb'], 'privateKey': 'do-not-expose-this-key'}}}],
            'outbounds': [{'protocol': 'freedom', 'tag': 'direct'}]}
        self.profiles = [{'uuid': self.pid, 'name': 'Shared', 'config': self.config, 'inbounds': [{'uuid': self.iid, 'tag': 'KINGLINE_XHTTP_REALITY'}]}]
        self.nodes = [{'uuid': uid(), 'name': 'Node-'+str(i), 'isConnected': True, 'isDisabled': False, 'xrayUptime': 12,
                       'versions': {'xray': '26.7.28', 'node': '2.7'},
                       'configProfile': {'activeConfigProfileUuid': self.pid, 'activeInbounds': [{'uuid': self.iid, 'rawInbound': self.config['inbounds'][0]}]}}
                      for i in range(2)]
        self.hosts = [{'uuid': uid(), 'inbound': {'configProfileUuid': self.pid, 'configProfileInboundUuid': self.iid},
                       'nodes': ns, 'port': 443, 'sni': 'example.org', 'fingerprint': 'firefox', 'alpn': None, 'isDisabled': False}
                      for ns in ([n['uuid'] for n in self.nodes], *[[n['uuid']] for n in self.nodes])]
        self.squads = [{'uuid': uid(), 'inbounds': [{'uuid': self.iid}]}]
        self.mutations, self.failure = [], None

    def items(self, path, key):
        return copy.deepcopy({'nodes': self.nodes, 'hosts': self.hosts, 'config-profiles': self.profiles, 'internal-squads': self.squads}[path])

    def call(self, path, method='GET', body=None):
        collection, _, identity = path.partition('/')
        items = {'nodes': self.nodes, 'hosts': self.hosts, 'config-profiles': self.profiles, 'internal-squads': self.squads}[collection]
        if method == 'GET':
            return copy.deepcopy(next(i for i in items if i['uuid'] == identity))
        self.mutations.append((method, path, copy.deepcopy(body)))
        if self.failure == (method, collection):
            self.failure = None
            raise RuntimeError('secret-bearing API response must never escape')
        if method == 'POST':
            assert len(body['name']) <= 30
            tags={i['tag'] for p in self.profiles for i in p['config']['inbounds']}
            assert not tags.intersection(i['tag'] for i in body['config']['inbounds']), 'panel_inbound_tags_must_be_globally_unique'
            item = {'uuid': uid(), **copy.deepcopy(body), 'inbounds': [{'uuid': uid(), 'tag': i['tag']} for i in body['config']['inbounds']]}
            items.append(item)
        elif method == 'PATCH':
            item = next(i for i in items if i['uuid'] == body['uuid'])
            if collection=='config-profiles':
                other_tags={i['tag'] for p in self.profiles if p['uuid']!=body['uuid'] for i in p['config']['inbounds']}
                assert not other_tags.intersection(i['tag'] for i in body['config']['inbounds'])
            item.update(copy.deepcopy(body))
        elif method == 'DELETE':
            items[:] = [i for i in items if i['uuid'] != identity]
            return None
        return copy.deepcopy(item)


@pytest.fixture
def editor(tmp_path):
    root = tmp_path/'data'; root.mkdir()
    control = tmp_path/'control'; control.mkdir()
    api = Panel()
    store = Store(root)
    for i, n in enumerate(api.nodes):
        store.save_node({'uuid': n['uuid'], 'name': n['name'], 'public_ip': '8.8.8.'+str(i+1), 'managed_ssh': True})
    atomic_json(control/'owner-profile.json', {'profile_uuid': api.pid, 'inbound_uuid': api.iid,
        'sni': 'example.org', 'fingerprint': 'firefox', 'short_id': 'aabb', 'user': {'vlessUuid': uid()}, 'reality': {'publicKey': 'public'}})
    ssh = SimpleNamespace(run=lambda *a, **kw: None, close=lambda: None)
    m = SimpleNamespace(root=root, control=control, store=store, lock=threading.RLock(), stop=threading.Event(),
        cover=None, ssh_factory=lambda *a: ssh, probe=lambda *a, **kw: {'https': True, 'udp_dns': True})
    obj = VPNConfig(m, api)
    return obj


def draft(e, scope='profile'):
    detail = e.catalog(e.api.pid)['profile']
    return {'profile_uuid': e.api.pid, 'revision': detail['revision'], 'request_id': uid(),
            'scope': scope, 'nodes': [e.api.nodes[0]['uuid']], 'config': detail['config'], 'client': detail['client'], 'note': 'Example'}


def checked(e, body):
    change = e.submit(body)
    e.process(change['id'])
    assert e.public(change['id'])['state'] == 'validated'
    return change['id']


def applied(e, body):
    change = checked(e, body)
    e.commit(change); e.process(change)
    return change


def test_read_masks_nested_secrets_versions_and_does_not_mutate(editor):
    data = editor.catalog(editor.api.pid)
    assert 'do-not-expose-this-key' not in json.dumps(data)
    assert 'rawInbound' not in json.dumps(data)
    assert data['profile']['config']['inbounds'][0]['streamSettings']['realitySettings']['privateKey'] == KEEP
    assert not editor.api.mutations


def test_validate_noop_does_not_restart_or_change_any_panel_resource(editor):
    change = applied(editor, draft(editor))
    assert editor.public(change)['state'] == 'noop'
    assert not editor.api.mutations
    assert 'do-not-expose-this-key' not in editor.m.store.path.read_bytes().decode(errors='ignore')


def test_global_apply_keeps_inbound_ids_and_updates_hosts_owner_then_rollback(editor):
    b = draft(editor, 'all')
    b['config']['log']['loglevel'] = 'error'
    b['client']['sni'] = 'new.example.org'; b['client']['fingerprint'] = 'chrome'
    b['config']['inbounds'][0]['streamSettings']['realitySettings']['serverNames'] = ['new.example.org']
    before = editor.revision(editor.snapshot())
    change = applied(editor, b)
    assert editor.public(change)['state'] == 'ready'
    assert editor.api.profiles[0]['inbounds'][0]['uuid'] == editor.api.iid
    assert all(h['sni'] == 'new.example.org' and h['fingerprint'] == 'chrome' for h in editor.api.hosts)
    assert json.loads((editor.m.control/'owner-profile.json').read_text())['sni'] == 'new.example.org'
    editor.rollback(change, editor.revision(editor.snapshot())); editor.process(change)
    assert editor.public(change)['state'] == 'rolled_back'
    assert editor.revision(editor.snapshot()) == before


def test_selected_node_clones_config_rebinds_host_and_same_squads_preserving_auto(editor):
    b = draft(editor, 'selected'); b['config']['log']['loglevel'] = 'error'
    initial = editor.revision(editor.snapshot())
    change = applied(editor, b)
    assert editor.public(change)['state'] == 'ready'
    assert len(editor.api.profiles) == 2
    clone = editor.api.profiles[1]
    assert editor.api.nodes[0]['configProfile']['activeConfigProfileUuid'] == clone['uuid']
    assert editor.api.nodes[1]['configProfile']['activeConfigProfileUuid'] == editor.api.pid
    assert editor.api.hosts[0]['inbound']['configProfileUuid'] == editor.api.pid  # shared DNS key stays intact
    assert editor.api.hosts[1]['inbound']['configProfileUuid'] == clone['uuid']
    assert clone['inbounds'][0]['uuid'] in editor.api.squads[0]['inbounds']
    assert editor.api.profiles[0]['config']['log']['loglevel'] == 'warning'
    editor.rollback(change, editor.revision(editor.snapshot())); editor.process(change)
    assert len(editor.api.profiles) == 1
    assert editor.revision(editor.snapshot()) == initial


@pytest.mark.parametrize('kind',['identity','private_key','add_secret','clients','listener','api','file','env'])
def test_rejects_unsafe_or_incompatible_drafts_without_mutating(editor, kind):
    b = draft(editor, 'selected'); c = b['config']; r = c['inbounds'][0]['streamSettings']['realitySettings']
    if kind == 'identity':
        b['client']['sni'] = 'other.example.org'; r['serverNames'] = ['other.example.org']
    if kind == 'private_key': r['privateKey'] = 'replace-key'
    if kind == 'add_secret': c['outbounds'].append({'protocol':'vless','settings':{'password':'new-secret'}})
    if kind == 'clients': c['inbounds'][0]['settings']['clients'] = [{'id':uid()}]
    if kind == 'listener': c['inbounds'][0]['port'] = 8443
    if kind == 'api': c['api'] = {'listen':'0.0.0.0'}
    if kind == 'file': c['log']['access'] = '/tmp/unbounded.log'
    if kind == 'env': c['env'] = {'XRAY_LOCATION_ASSET':'/root'}
    with pytest.raises(SafeError): editor.submit(b)
    assert not editor.api.mutations


def test_stale_validation_concurrent_operation_and_idempotency(editor):
    b = draft(editor); change = editor.submit(b)
    assert editor.submit(b)['id'] == change['id']
    with pytest.raises(SafeError, match='vpn_operation_busy'): editor.submit(b | {'request_id':uid()})
    with pytest.raises(SafeError, match='vpn_request_conflict'): editor.submit(b | {'note':'different'})
    editor.process(change['id'])
    editor.api.profiles[0]['config']['log']['loglevel'] = 'none'
    with pytest.raises(SafeError, match='vpn_revision_changed'): editor.commit(change['id'])
    assert not editor.api.mutations


def test_xray_rejection_never_applies_a_draft(editor):
    def fail(*args): raise SafeError('vpn_xray_validation_failed')
    editor.validate_core = fail
    change = editor.submit(draft(editor)); editor.process(change['id'])
    assert editor.public(change['id'])['error'] == 'vpn_xray_validation_failed'
    with pytest.raises(SafeError, match='vpn_validation_required'): editor.commit(change['id'])
    assert not editor.api.mutations


def test_partial_failure_rolls_back_profile_hosts_and_owner(editor):
    b = draft(editor); b['config']['log']['loglevel'] = 'error'
    before = editor.revision(editor.snapshot())
    change = checked(editor, b); editor.api.failure = ('PATCH', 'hosts')
    editor.commit(change); editor.process(change)
    result = editor.public(change)
    assert result['state'] == 'rolled_back' and result['error'] == 'vpn_operation_failed'
    assert 'secret-bearing' not in json.dumps(result)
    assert editor.revision(editor.snapshot()) == before


def test_clone_failure_restores_groups_nodes_and_deletes_only_its_clone(editor):
    b = draft(editor, 'selected'); b['config']['log']['loglevel'] = 'error'
    before = editor.revision(editor.snapshot())
    change = checked(editor, b); editor.api.failure = ('PATCH', 'nodes')
    editor.commit(change); editor.process(change)
    assert editor.public(change)['state'] == 'rolled_back'
    assert editor.revision(editor.snapshot()) == before


def test_tunnel_failure_triggers_rollback(editor):
    b = draft(editor); b['config']['log']['loglevel'] = 'error'
    change = checked(editor, b)
    def fail(*args, **kwargs): raise SafeError('timeout')
    editor.m.probe = fail
    editor.m.stop.wait = lambda *_: False
    before = editor.revision(editor.snapshot())
    editor.commit(change); editor.process(change)
    assert editor.public(change)['state'] == 'rolled_back'
    assert editor.public(change)['error'] == 'vpn_tunnel_verification_failed'
    assert editor.revision(editor.snapshot()) == before


def test_process_restart_replays_durable_journal(editor):
    b = draft(editor); b['config']['log']['loglevel'] = 'error'
    change = checked(editor, b); before = editor.revision(editor.snapshot())
    _, data = editor.read(change)
    editor.patch(change,data,'config-profiles',{'uuid':editor.api.pid,'config':b['config']},
                 {'uuid':editor.api.pid,'config':data['snapshot']['profiles'][0]['config']})
    restarted = VPNConfig(editor.m,editor.api)
    restarted.process(change,recover=True)
    assert restarted.public(change)['state'] == 'rolled_back'
    assert restarted.revision(restarted.snapshot()) == before


def test_rollback_cannot_overwrite_later_edits(editor):
    b = draft(editor); b['config']['log']['loglevel'] = 'error'
    change = applied(editor,b)
    editor.api.profiles[0]['config']['log']['loglevel'] = 'none'
    with pytest.raises(SafeError,match='vpn_rollback_revision_changed'):
        editor.rollback(change,editor.revision(editor.snapshot()))


def test_selected_fingerprint_can_differ_without_changing_the_shared_dns_key(editor):
    b = draft(editor,'selected'); b['client']['fingerprint']='chrome'
    change = applied(editor,b)
    assert editor.public(change)['state']=='ready'
    assert editor.api.hosts[0]['fingerprint']=='firefox'
    assert editor.api.hosts[1]['fingerprint']=='chrome'


def test_global_update_after_an_isolated_profile_preserves_unique_tags_and_routing(editor):
    b=draft(editor,'selected');b['config']['log']['loglevel']='error'
    first=applied(editor,b);assert editor.public(first)['state']=='ready'
    tags=[p['config']['inbounds'][0]['tag'] for p in editor.api.profiles]
    assert len(set(tags))==2
    b=draft(editor,'all');b['config']['log']['loglevel']='none'
    second=applied(editor,b);assert editor.public(second)['state']=='ready'
    assert [p['config']['inbounds'][0]['tag'] for p in editor.api.profiles]==tags
    assert all(p['config']['log']['loglevel']=='none' for p in editor.api.profiles)
    assert all(editor.api.nodes[i]['configProfile']['activeConfigProfileUuid']==editor.api.profiles[1 if i==0 else 0]['uuid'] for i in range(2))


def test_failed_rollback_blocks_new_changes_and_can_be_retried(editor):
    b = draft(editor); b['config']['log']['loglevel']='error'
    change = applied(editor,b)
    editor.rollback(change,editor.revision(editor.snapshot()))
    editor.api.failure=('PATCH','hosts')
    # Both rollback attempts fail to model an unavailable panel.
    original=editor.api.call
    def fail(path,method='GET',body=None):
        if method=='PATCH':raise RuntimeError('offline')
        return original(path,method,body)
    editor.api.call=fail
    editor.process(change)
    assert editor.public(change)['state']=='rollback_failed'
    with pytest.raises(SafeError,match='vpn_operation_busy'):editor.submit(draft(editor))
    editor.api.call=original;editor.api.failure=None
    editor.rollback(change,editor.revision(editor.snapshot()));editor.process(change)
    assert editor.public(change)['state']=='rolled_back'
    assert not editor.busy()


def test_manager_routes_require_private_api_token(editor):
    (editor.m.root/'admin.token').write_text('test-token')
    editor.m.vpn_config=editor
    app=create_app(editor.m,run_worker=False)
    with TestClient(app) as c:
        assert c.get('/v1/vpn/config').status_code==401
        assert c.post('/v1/vpn/apply',json={'id':uid()}).status_code==401
        result=c.get('/v1/vpn/config',headers={'Authorization':'Bearer test-token'})
        assert result.status_code==200 and 'do-not-expose' not in result.text
