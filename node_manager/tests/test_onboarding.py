"""Synthetic API/SSH only. No production servers or real subscriber credentials."""
import json
import time
import uuid
from urllib.parse import urlsplit, parse_qs
import pytest
from fastapi.testclient import TestClient

from node_manager.app import create_app
from node_manager.manager import Manager
from node_manager.models import InstallationAction
from node_manager.ssh import SafeError
from node_manager.tests.test_manager import manager, request, approve, FakeSSH


def action(m, identifier, operation, **changes):
    return m.onboarding.action(InstallationAction(job_id=identifier, operation=operation,
        request_id=str(uuid.uuid4()), revision=m.onboarding.public(identifier)['revision'], **changes))


def pending(m, monkeypatch=None):
    if monkeypatch:
        from node_manager import domains
        m.settings['node_domains']=True
        monkeypatch.setattr(domains,'sync',lambda *_:None)
    job=m.submit(request(),'candidate-test')
    m.store.claim();m.execute(job['id'])
    assert m.store.get(job['id'])['state']=='waiting_review'
    return job['id']


def test_mandatory_gate_even_after_success_isolated_profile_and_no_subscribers(manager):
    identifier=pending(manager)
    data=manager.onboarding.data(identifier)
    assert len(manager.api.hosts)==1 and not manager.api.hosts[0]['nodes']
    assert not manager.store.nodes()
    assert manager.api.nodes[0]['configProfile']['activeConfigProfileUuid']==data['profile']!='shared-profile'
    assert manager.api.squads[0]['inbounds']==data['inbounds']
    assert manager.api.users[0]['activeInternalSquads']==['standard-squad',data['squad']]
    # Startup recovery requeues running work only, not a human decision.
    resumed=Manager(manager.root,api=manager.api,ssh_factory=FakeSSH,probe=manager.probe)
    assert resumed.store.claim() is None
    assert resumed.onboarding.public(identifier)['revision']==manager.onboarding.public(identifier)['revision']
    approve(resumed,identifier)
    assert resumed.store.get(identifier)['state']=='ready'
    assert resumed.api.nodes[0]['configProfile']['activeConfigProfileUuid']=='shared-profile'
    assert len(resumed.store.nodes())==1
    assert len(resumed.api.profiles)==1 and not resumed.api.squads


def test_failed_rf_check_does_not_publish_recheck_and_explicit_override(manager):
    manager.onboarding.remote_check=lambda _: {'ok':False,'source_ok':True,'transports':{}}
    identifier=pending(manager)
    assert not manager.onboarding.public(identifier)['report']['ok']
    before=manager.onboarding.public(identifier)['revision']
    result=action(manager,identifier,'check')
    assert result['job']['state']=='queued'
    manager.execute(identifier)
    assert manager.onboarding.public(identifier)['revision']!=before
    assert not manager.store.nodes()
    # A phone test may work on another carrier, so the human may override RF failure.
    approve(manager,identifier)
    assert manager.store.get(identifier)['state']=='ready'


def test_unavailable_probe_is_a_pending_result_not_an_install_failure(manager):
    manager.onboarding.remote_check=lambda _: (_ for _ in ()).throw(SafeError('ru_check_unavailable'))
    identifier=pending(manager)
    assert manager.onboarding.public(identifier)['report']['error']=='ru_check_unavailable'
    assert not manager.store.nodes()


def test_phone_key_scoped_to_candidate_encrypted_bounded_and_revoked(manager,monkeypatch):
    identifier=pending(manager,monkeypatch)
    result=action(manager,identifier,'test-key')
    same=action(manager,identifier,'test-key')
    uri=urlsplit(result['test_key']);identity=uri.username
    assert same['test_key']==result['test_key']
    assert uri.hostname.startswith('n-') and uri.hostname.endswith('.edge.kingline.online')
    assert parse_qs(uri.query)['fp']==['firefox']
    assert 7100 < result['expires']-time.time() <= 7201
    user=next(u for u in manager.api.users if u['vlessUuid']==identity)
    assert user['trafficLimitBytes']==1024**3
    assert user['activeInternalSquads']==[manager.onboarding.data(identifier)['squad']]
    assert len(manager.api.users)==2
    assert identity!=manager.api.users[0]['vlessUuid']
    client=TestClient(create_app(manager,run_worker=False))
    public=client.get('/v1/jobs/'+identifier,headers={'Authorization':'Bearer admin-test-token'})
    assert identity not in public.text and 'test_key' not in public.json()
    assert identity.encode() not in b''.join(p.read_bytes() for p in manager.root.glob('manager.sqlite3*'))
    approve(manager,identifier)
    assert len(manager.api.users)==1
    with pytest.raises(SafeError,match='candidate_not_waiting'):action(manager,identifier,'test-key')


def test_idempotency_and_stale_review_cannot_publish(manager):
    identifier=pending(manager)
    body=InstallationAction(job_id=identifier,operation='check',request_id=str(uuid.uuid4()))
    manager.onboarding.action(body);manager.onboarding.action(body)
    assert manager.store.get(identifier)['state']=='queued'
    manager.execute(identifier)
    revision=manager.onboarding.public(identifier)['revision']
    with pytest.raises(SafeError,match='candidate_review_changed'):
        manager.onboarding.action(InstallationAction(job_id=identifier,operation='approve',
            request_id=str(uuid.uuid4()),revision='0'*64))
    assert manager.onboarding.public(identifier)['revision']==revision
    assert not manager.store.nodes()


def test_core_verification_still_required_after_phone_approval(manager):
    identifier=pending(manager)
    manager.probe=lambda *_: (_ for _ in ()).throw(SafeError('encrypted_https_exit_check_failed'))
    approve(manager,identifier)
    assert manager.store.get(identifier)['state']=='failed'
    assert not manager.store.nodes() and len(manager.api.hosts)==1


def test_cancel_retries_cleanup_without_publication_and_is_not_delete_live(manager,monkeypatch):
    identifier=pending(manager,monkeypatch)
    action(manager,identifier,'test-key')
    calls=[]
    manager.remove_core_peer=lambda node:calls.append('peer')
    class Remote(FakeSSH):
        failed=True
        def python(self,source,payload,timeout=None):
            calls.append(payload['job_id'])
            if self.failed:raise SafeError('remote_operation_failed')
            assert 'identity.json' in source and '3x-ui' not in source
            return '{"ok":true}'
    manager.ssh_factory=Remote
    action(manager,identifier,'cancel');manager.execute(identifier)
    assert manager.store.get(identifier)['state']=='failed'
    assert manager.onboarding.public(identifier)['cancel_failed']
    assert len(manager.api.users)==1 # key revoked even when SSH cleanup failed
    with pytest.raises(SafeError,match='candidate_cancellation_pending'):action(manager,identifier,'approve')
    Remote.failed=False
    action(manager,identifier,'cancel');manager.execute(identifier)
    assert manager.store.get(identifier)['state']=='cancelled'
    assert not manager.api.nodes and not manager.api.squads and len(manager.api.profiles)==1
    assert not manager.store.nodes() and len(manager.api.hosts)==1
    assert json.loads((manager.control/'pending-nodes.json').read_text())==[]
    count=len(calls);action(manager,identifier,'cancel');assert len(calls)==count


def test_api_requires_admin_and_rejects_extra_fields_without_echoing(manager):
    identifier=pending(manager)
    client=TestClient(create_app(manager,run_worker=False))
    body={'job_id':identifier,'operation':'approve','request_id':str(uuid.uuid4()),'revision':'0'*64}
    assert client.post('/v1/installation-action',json=body).status_code==401
    headers={'Authorization':'Bearer admin-test-token'}
    assert client.post('/v1/installation-action',json=body,headers=headers).status_code==409
    body['password']='secret-canary'
    response=client.post('/v1/installation-action',json=body,headers=headers)
    assert response.status_code==422 and 'secret-canary' not in response.text


def test_standard_drift_requires_fresh_candidate_check(manager):
    identifier=pending(manager)
    manager.api.profiles[0]['config']['outbounds']=[{'protocol':'freedom','tag':'direct'}]
    with pytest.raises(SafeError,match='candidate_standard_changed'):action(manager,identifier,'approve')
    action(manager,identifier,'check');manager.execute(identifier)
    assert manager.store.get(identifier)['state']=='waiting_review'
    approve(manager,identifier)
    assert manager.store.get(identifier)['state']=='ready'


def test_remote_check_verifies_source_and_both_transports_without_keys_in_status(manager):
    identifier=pending(manager)
    data=manager.onboarding.data(identifier)
    manager.settings['xhttp']={'path':'/fixture/','inbound_uuid':'xhttp-inbound'}
    manager.reviews.config=lambda:{'enabled':True,'probe':{'ip':'10.79.0.2'}}
    manager.reviews.source_ip=lambda:'203.0.113.200'
    class RF(FakeSSH):
        source=True
        def python(self,source,payload,timeout=None):
            assert payload['target']['probe_uuid']==manager.api.users[0]['vlessUuid']
            assert payload['manifest']['expected_source_ip']=='203.0.113.200'
            assert payload['xhttp_path']=='/fixture/' and timeout>=400
            one={'node':data['candidate']['uuid'],'revision':data['standard_revision'],'ok':True,
                 'attempts':[{'ok':True,'tcp':{'ok':True},'large':{'ok':True,'bytes':1048576},'private_key':'canary'}]}
            return json.dumps({'source_ok':self.source,'transports':{'raw':one,'xhttp':one}})
    manager.ssh_factory=RF
    # Use the real implementation instead of the fixture's no-network stub.
    from node_manager.onboarding import Onboarding
    result=Onboarding.remote_check(manager.onboarding,data)
    assert result['ok'] and set(result['transports'])=={'raw','xhttp'}
    assert 'canary' not in json.dumps(result)
    RF.source=False
    assert not Onboarding.remote_check(manager.onboarding,data)['ok']
