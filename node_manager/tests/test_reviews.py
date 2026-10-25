import copy,json,time,uuid
import pytest
from node_manager.tests.test_node_network import network,editor,uid
from node_manager.visibility import Visibility
from node_manager.reviews import Reviews,clean_observation
from node_manager.ru_detection import classify
from node_manager.ssh import SafeError
from node_manager.store import atomic_json

@pytest.fixture
def visibility(network):
    network.m.vpn_config=network.vpn
    obj=Visibility(network.m);network.m.visibility=obj
    return obj

def test_hide_cascades_to_dependent_profiles_preserves_services_and_restores_previous_flags(visibility):
    m=visibility.m;nodes=m.network.nodes();a,b=nodes
    state=m.network.state();state['via'][b['uuid']]=a['uuid'];state['ports'][b['uuid']]=24000;atomic_json(m.network.path,state)
    hosts=m.network.api.hosts;child=next(h for h in hosts if h['uuid']==b['host_uuid']);child['isDisabled']=True
    before=copy.deepcopy(m.network.api.nodes)
    result=visibility.set(a['uuid'],True,uid())
    assert set(result['affected'])=={a['uuid'],b['uuid']}
    assert all(h.get('isDisabled') for h in hosts if h['uuid'] in (a['host_uuid'],b['host_uuid']))
    exported=json.loads((m.control/'nodes.json').read_text());assert all(n['effective_hidden'] for n in exported)
    assert m.network.api.nodes==before
    visibility.set(a['uuid'],False,uid())
    assert not next(h for h in hosts if h['uuid']==a['host_uuid'])['isDisabled']
    assert child['isDisabled'] is True
    assert not any(n['effective_hidden'] for n in json.loads((m.control/'nodes.json').read_text()))

def test_visibility_failure_compensates_panel_and_keeps_node_published(visibility,monkeypatch):
    m=visibility.m;a,b=m.network.nodes();state=m.network.state();state['via'][b['uuid']]=a['uuid'];state['ports'][b['uuid']]=24000;atomic_json(m.network.path,state)
    original=m.network.api.call;failed=False
    def call(path,method='GET',body=None):
        nonlocal failed
        if path=='hosts' and body and body['uuid']==b['host_uuid'] and not failed:failed=True;raise RuntimeError('temporary panel outage')
        return original(path,method,body)
    monkeypatch.setattr(m.network.api,'call',call)
    with pytest.raises(SafeError,match='visibility_update_failed'):visibility.set(a['uuid'],True,uid())
    assert not any(n.get('hidden') for n in m.store.nodes())
    assert not any(h.get('isDisabled') for h in m.network.api.hosts)

def test_visibility_recovery_rolls_back_interrupted_host_write(visibility):
    m=visibility.m;host=m.network.api.hosts[1];host['isDisabled']=True
    with m.store.db() as db:db.execute('INSERT INTO visibility_changes VALUES(?,?,\'applying\',?,NULL,?)',(uid(),uid(),m.store.cipher.encrypt(json.dumps({'patches':[{'uuid':host['uuid'],'before':False}]}).encode()),time.time()))
    visibility.recover();assert host['isDisabled'] is False

def trial(ok=False,plateau=16384):
    return {'ok':ok,'tcp':{'ok':True},'small':{'ok':True,'bytes':2048},'large':{'ok':ok,'bytes':1048576 if ok else plateau,'error':None if ok else 'timeout'},'error':None if ok else 'timeout'}

def test_classification_distinguishes_repeated_freeze_server_failure_source_and_transient():
    bad={'ok':False,'attempts':[trial(),trial(plateau=17000),trial()]}
    assert classify(bad,core_ok=True,other_ru_ok=True)['code']=='flow_stall_suspected'
    assert classify(bad,core_ok=False,other_ru_ok=True)['code']=='server_unavailable'
    assert not classify(bad,core_ok=True,source_ok=False)['confirmed']
    transient={'ok':True,'attempts':[trial(),trial(True),trial(True)]}
    assert not classify(transient,core_ok=True)['confirmed']
    assert classify(transient,core_ok=True,previous={'unstable':True})['code']=='unstable'
    assert classify({'ok':False,'attempts':[trial()]},core_ok=True)['confirmed'] is False

def test_observations_cannot_publish_arbitrary_agent_fields_or_secrets():
    raw={'node':'x','revision':'a'*64,'ok':False,'probe_uuid':'secret-canary','attempts':[trial()|{'privateKey':'secret-canary'}]}
    safe=clean_observation(raw);assert 'secret-canary' not in json.dumps(safe)

@pytest.fixture
def reviews(visibility):
    obj=Reviews(visibility.m,control_probe=lambda *args:{'ok':True});visibility.m.reviews=obj
    atomic_json(obj.path,{'enabled':True,'owner_id':42,'probe':{'ip':'10.79.0.2'}})
    return obj

def manifest(r):
    return {'revision':'m','entry_ids':[n['uuid'] for n in r.nodes()],'targets':[{'id':n['uuid'],'revision':'r','ip':n['public_ip'],'label':n['name']} for n in r.nodes()]}

def report(r,first_bad=True):
    targets=manifest(r)['targets'];now=time.time()
    atomic_json(r.m.control/'tunnel-health.json',{'checked_at':now,'nodes':[{'uuid':t['id'],'ok':True} for t in targets]})
    return {'id':uid(),'manifest_revision':'m','at':now,'last_full_at':now,'control':{'clock_synced':True,'reference_https':True},'nodes':[{'node':t['id'],'revision':'r','ok':not first_bad or i>0,'attempts':[trial(not first_bad or i>0)]*(3 if first_bad and not i else 1)} for i,t in enumerate(targets)]}

def test_confirmed_problem_queues_one_owner_alert_without_automatic_hiding(reviews):
    reviews.ingest(report(reviews),manifest(reviews));snapshot=reviews.snapshot()
    assert len(snapshot['reviews'])==1 and snapshot['reviews'][0]['code']=='flow_stall_suspected'
    assert not any(n.get('hidden') for n in reviews.nodes())
    first=reviews.claim_outbox(42);assert len(first)==1 and 'ВАЖНО' in first[0]['text'] and len(first[0]['keyboard'])==2
    assert reviews.claim_outbox(42)==[]
    reviews.ingest(report(reviews),manifest(reviews));assert reviews.claim_outbox(42)==[]
    with pytest.raises(SafeError,match='owner_only'):reviews.claim_outbox(43)

def test_buttons_are_owner_bound_idempotent_and_hidden_node_can_be_restored(reviews):
    reviews.ingest(report(reviews),manifest(reviews));reviews.save_meta('manifest_public',manifest(reviews))
    alert=reviews.claim_outbox(42)[0];hide=alert['keyboard'][1][0]['callback_data']
    with pytest.raises(SafeError,match='owner_only'):reviews.callback(43,hide)
    first=reviews.callback(42,hide);second=reviews.callback(42,hide);assert first==second
    node=reviews.nodes()[0];assert node['hidden']
    review=reviews.snapshot()['reviews'][0]
    reviews.action(review['id'],'restore',request_id=uid());assert not reviews.nodes()[0]['hidden']

def test_stale_and_unknown_reports_do_not_create_reviews(reviews):
    old=report(reviews);old['at']-=4000;reviews.ingest(old,manifest(reviews));assert not reviews.snapshot()['reviews']
    old=report(reviews);old['manifest_revision']='different';reviews.ingest(old,manifest(reviews));assert not reviews.snapshot()['reviews']

def test_recheck_coalesces_pending_and_outbox_ack_is_owner_only(reviews):
    node=reviews.nodes()[0]['uuid'];one=reviews.request(node);two=reviews.request(node);assert one['request_id']==two['request_id']
    reviews.notice('canary','check')
    item=reviews.claim_outbox(42)[0]
    with pytest.raises(SafeError):reviews.ack(43,item['id'],1)
    reviews.ack(42,item['id'],1);assert reviews.claim_outbox(42)==[]

def test_manifest_checks_only_direct_entries_and_contains_no_server_private_key(reviews):
    import base64
    m=reviews.m;a,b=m.network.nodes();state=m.network.state();state['via'][b['uuid']]=a['uuid'];state['ports'][b['uuid']]=24000;atomic_json(m.network.path,state)
    api=m.network.api
    for profile in api.profiles:
        profile['config']['inbounds'][0]['streamSettings']['realitySettings']['privateKey']=base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip('=')
    atomic_json(m.control/'probe-user.json',{'vlessUuid':uid()})
    data=reviews.manifest()
    assert data['entry_ids']==[a['uuid']]
    child=next(t for t in data['targets'] if t['id']==b['uuid']);assert child['address']==a['public_ip'] and child['port']==24000
    assert 'privateKey' not in json.dumps(data)
    assert len(data['targets'])==2

def test_failed_new_route_stays_hidden_and_success_publishes_only_after_ru_check(reviews):
    reviews.ingest(report(reviews),manifest(reviews));row=reviews.snapshot()['reviews'][0];node=reviews.nodes()[0]
    reviews.m.visibility.set(node['uuid'],True,uid())
    with reviews.store.db() as db:db.execute("INSERT INTO review_routes VALUES(?,?,?,?,'checking_ru',?)",(uid(),row['id'],node['uuid'],uid(),time.time()))
    reviews.finish_route(row['id'],False);assert reviews.nodes()[0]['hidden']
    with reviews.store.db() as db:db.execute("UPDATE review_routes SET state='checking_ru' WHERE review=?",(row['id'],))
    reviews.finish_route(row['id'],True);assert not reviews.nodes()[0]['hidden']

def test_recheck_button_can_be_used_again_after_previous_result(reviews):
    token=reviews.token(None,'all');first=reviews.callback(42,token);second=reviews.callback(42,token);assert first==second
    with reviews.store.db() as db:db.execute("UPDATE review_requests SET state='done' WHERE id=?",(first['request_id'],))
    third=reviews.callback(42,token);assert third['request_id']!=first['request_id']

def test_probe_source_failure_does_not_open_individual_node_incidents(reviews):
    data=report(reviews);data['control']['clock_synced']=False
    reviews.ingest(data,manifest(reviews));assert not reviews.snapshot()['reviews']
    assert len(reviews.claim_outbox(42))==1

def test_bot_socket_is_narrow_and_does_not_accept_arbitrary_node_commands():
    from fastapi.testclient import TestClient
    from node_manager.review_bot_socket import create_app
    class Client:
        calls=[]
        def call(self,*args):self.calls.append(args);return {'ok':True,'items':[]}
    backend=Client();client=TestClient(create_app(backend))
    assert client.post('/claim',json={'actor_id':42}).status_code==200
    assert client.post('/callback',json={'actor_id':42,'data':'kr:'+('a'*24),'private_key':'must-not-echo'}).status_code==422
    assert 'must-not-echo' not in client.post('/callback',json={'actor_id':42,'data':'secret-canary'}).text
    assert client.post('/nodes/remove',json={'actor_id':42}).status_code==404
    assert len(backend.calls)==1


def test_hiding_during_route_check_cancels_automatic_republication(reviews):
    reviews.ingest(report(reviews),manifest(reviews));row=reviews.snapshot()['reviews'][0];node=reviews.nodes()[0]
    reviews.m.visibility.set(node['uuid'],True,uid())
    with reviews.store.db() as db:db.execute("INSERT INTO review_routes VALUES(?,?,?,?,'checking_ru',?)",(uid(),row['id'],node['uuid'],uid(),time.time()))
    with pytest.raises(SafeError,match='review_route_check_pending'):reviews.visibility(node['uuid'],False,uid())
    reviews.visibility(node['uuid'],True,uid());reviews.finish_route(row['id'],True)
    assert reviews.nodes()[0]['hidden']

def test_tcp_timeout_and_tls_failure_without_payload_are_classified_safely(reviews):
    data=report(reviews);data['nodes'][0]['attempts']=[{'ok':False,'tcp':{'ok':False},'error':'tcp_unreachable'} for _ in range(3)]
    reviews.ingest(data,manifest(reviews));assert reviews.snapshot()['reviews'][0]['code']=='regional_path_suspected'
    tls=clean_observation({'node':'x','revision':'r','ok':False,'attempts':[{'ok':False,'tcp':{'ok':True},'small':{'ok':False,'bytes':0,'error':'timeout'},'large':None}]*3})
    assert classify(tls,core_ok=True,other_ru_ok=True)['code']=='tls_interference_suspected'


def test_same_asset_control_failure_is_not_reported_as_russian_filtering(reviews):
    reviews.control_probe=lambda *args:{'ok':False}
    reviews.ingest(report(reviews),manifest(reviews))
    row=reviews.snapshot()['reviews'][0]
    assert row['code']=='server_unavailable' and row['check']['matched_control'] is True
