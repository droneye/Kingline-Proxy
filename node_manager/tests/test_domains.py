import json
from types import SimpleNamespace

import pytest

from node_manager import domains
from node_manager.ssh import SafeError

A = '11111111-1111-4111-8111-111111111111'
B = '22222222-2222-4222-8222-222222222222'


def test_stable_names_follow_actual_entry_and_keep_hidden_nodes_addressable():
    nodes = [{'uuid': A, 'public_ip': '8.8.8.8', 'hidden': True},
             {'uuid': B, 'public_ip': '9.9.9.9'}]
    direct = domains.desired_records(nodes, {})
    assert set(direct.values()) == {'8.8.8.8', '9.9.9.9'}
    relay = domains.desired_records(nodes, {'via': {B: A}})
    assert relay[domains.hostname(nodes[1]) + '.'] == '8.8.8.8'
    assert relay.keys() == direct.keys()  # Existing keys survive a route change.
    with pytest.raises(SafeError):
        domains.desired_records(nodes, {'via': {A: B, B: A}})


def test_opt_in_changes_publication_but_preserves_legacy_fixtures():
    n = {'uuid': A}
    assert domains.published_address(SimpleNamespace(), n, '8.8.8.8') == '8.8.8.8'
    assert domains.published_address(SimpleNamespace(settings={'node_domains': True}), n, '8.8.8.8') == domains.hostname(n)


def test_reconcile_all_replicas_delete_only_owned_names_and_fail_closed(tmp_path, monkeypatch):
    (tmp_path / 'dns-replicas.json').write_text(json.dumps([{'public_ip': x} for x in ['one', 'two', 'three']]))
    stale = 'n-' + 'f' * 32 + '.edge.kingline.online.'
    calls = []
    def api(replica, method='GET', body=None):
        calls.append((replica['public_ip'], method, body))
        if method == 'GET':
            return {'rrsets': [{'name': x, 'type': 'A'} for x in
                              [stale, 'test.edge.kingline.online.', 'auto.edge.kingline.online.']]}
    monkeypatch.setattr(domains, 'dns_api', api)
    def verify(replica, desired):
        if replica['public_ip'] == 'two':
            raise OSError('resolver failed')
    monkeypatch.setattr(domains, 'verify_records', verify)
    with pytest.raises(SafeError, match='node_domains_replica_update_failed'):
        domains.sync(tmp_path, [{'uuid': A, 'public_ip': '8.8.8.8'}], {})
    patches = [c for c in calls if c[1] == 'PATCH']
    assert len(patches) == 3
    for _, _, body in patches:
        deleted = [r['name'] for r in body['rrsets'] if r['changetype'] == 'DELETE']
        assert deleted == [stale]
        assert body['rrsets'][0]['records'][0]['content'] == '8.8.8.8'


def test_deleted_nodes_have_no_published_record():
    assert domains.desired_records([{'uuid': A, 'public_ip': '8.8.8.8', 'provisioned': False}], {}) == {}


def test_periodic_dns_keeps_unpublished_candidate_until_review_finishes(tmp_path, monkeypatch):
    (tmp_path/'dns-replicas.json').write_text('[{"public_ip":"resolver"}]')
    candidate={'uuid':B,'public_ip':'9.9.9.9'}
    (tmp_path/'pending-nodes.json').write_text(json.dumps([candidate]))
    monkeypatch.setattr(domains,'dns_api',lambda *args,**kwargs:{'rrsets':[]})
    monkeypatch.setattr(domains,'verify_records',lambda *_:None)
    nodes=[{'uuid':A,'public_ip':'8.8.8.8'},dict(candidate,provisioned=False)]
    desired=domains.sync(tmp_path,nodes,{})
    assert desired[domains.hostname(candidate)+'.']=='9.9.9.9'
    assert nodes[-1]['provisioned'] is False # DNS alone must not publish it.
    (tmp_path/'pending-nodes.json').write_text('[]')
    assert domains.hostname(candidate)+'.' not in domains.sync(tmp_path,nodes,{})
