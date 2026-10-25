import copy,json
from types import SimpleNamespace
import pytest
from node_manager.tests.test_manager import manager
from node_manager.models import RUReceiver
from node_manager.ssh import SafeError
from node_manager import ru_installer,ru_mesh_remote
from node_manager.store import atomic_json

def prepare(manager):
    old={'enabled':True,'node_uuid':'old-ru','profile_uuid':'ru-private','inbound_uuid':'ru-inbound','pi':{'ip':'10.79.0.2','ssh_port':22},
         'vless_uuid':'internal-only-canary','domains':['domain:ru','domain:yandex.net'],'mesh_peers':[],'direct_fallback_nodes':[]}
    atomic_json(manager.control/'ru-egress.json',old)
    return old

def receiver(**extra):return RUReceiver(ip='8.8.8.8',request_id='33333333-3333-4333-a333-333333333333',credentials={'password':'private-password-canary'},**extra)

def test_receiver_queue_is_idempotent_encrypted_and_protected(manager):
    prepare(manager);installer=manager.ru_installer
    first=installer.submit(receiver());assert installer.submit(receiver())['id']==first['id']
    assert 'private-password-canary' not in json.dumps(first)
    assert b'private-password-canary' not in b''.join(p.read_bytes() for p in manager.root.glob('manager.sqlite3*'))
    with pytest.raises(SafeError,match='idempotency_key_conflict'):installer.submit(receiver(display_name='Other'))
    manager.settings['core_public_ip']='9.9.9.9'
    with pytest.raises(SafeError,match='protected_server_address'):installer.submit(RUReceiver(ip=manager.settings['core_public_ip'],request_id='44444444-4444-4444-a444-444444444444'))

def test_failed_preparation_retains_live_receiver_and_erases_bootstrap_password(manager,monkeypatch):
    old=prepare(manager);installer=manager.ru_installer;job=installer.submit(receiver())
    def fail(*args,**kwargs):raise SafeError('ssh_authentication_failed')
    monkeypatch.setattr(installer,'remote',fail)
    installer.execute(job['id']);row=installer.read(job['id'])
    assert row['state']=='failed' and row['secret'] is None
    assert json.loads((manager.control/'ru-egress.json').read_text())==old
    assert row['error']=='ssh_authentication_failed'
    assert 'internal-only-canary' not in json.dumps(installer.jobs())

@pytest.mark.parametrize('success',[True,False])
def test_cutover_verifies_before_commit_and_compensates_failure(manager,monkeypatch,success):
    old=prepare(manager);installer=manager.ru_installer;job=installer.submit(receiver())
    manager.store.save_node({'uuid':'head','public_ip':'8.8.4.4','management_ip':'10.77.0.3','ru_enabled':True})
    original={'inbounds':[{'port':443,'protocol':'vless','settings':{'clients':[]}}],'outbounds':[{'tag':'DIRECT','protocol':'freedom'},{'tag':'BLOCK','protocol':'blackhole'}],'routing':{'rules':[{'type':'field','ip':['geoip:private'],'outboundTag':'BLOCK'}]}}
    class API:
        config=copy.deepcopy(original)
        def items(self,path,key):return [{'uuid':'head','configProfile':{'activeConfigProfileUuid':'shared'}}]
        def call(self,path,method='GET',body=None):
            if method=='GET':return {'config':copy.deepcopy(self.config)}
            self.config=copy.deepcopy(body['config']);return {}
    api=API();manager.api=api
    monkeypatch.setattr(ru_installer.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0))
    monkeypatch.setattr(ru_installer.time,'sleep',lambda *_:None)
    def check(*args,**kwargs):
        assert json.loads((manager.control/'ru-egress.json').read_text())['node_uuid']=='old-ru'
        assert kwargs['expected_exit_ip']=='9.9.9.9'
        if not success:raise SafeError('probe_failed')
        return {'https':True,'udp_dns':True}
    monkeypatch.setattr(ru_installer,'verify_tunnel',check)
    context={'job_id':job['id'],'old':old,'wan':'9.9.9.9','node_uuid':'new-ru','peers':[]}
    if success:
        installer.commit(context,1,{'ip':'10.79.1.2','ssh_port':22},{'ip':'8.8.8.8','display_name':'New RU'})
        state=json.loads((manager.control/'ru-egress.json').read_text());assert state['node_uuid']=='new-ru' and state['generation']==1
        assert api.config['inbounds']==original['inbounds']
        assert any(o.get('settings',{}).get('vnext',[{}])[0].get('address')=='10.78.1.1' for o in api.config['outbounds'])
    else:
        with pytest.raises(SafeError,match='ru_public_route_check_failed'):installer.commit(context,1,{'ip':'10.79.1.2'},{'ip':'8.8.8.8','display_name':'New RU'})
        assert api.config==original and json.loads((manager.control/'ru-egress.json').read_text())==old

def test_generational_receiver_does_not_overwrite_legacy_container(monkeypatch):
    import shutil
    from pathlib import Path
    written={};real_mkdir=Path.mkdir
    monkeypatch.setattr(Path,'mkdir',lambda p,*a,**kw:None if str(p).startswith('/opt/kingline-ru-node') else real_mkdir(p,*a,**kw))
    monkeypatch.setattr(shutil,'which',lambda *_:'/usr/bin/tool')
    monkeypatch.setattr(ru_mesh_remote,'write',lambda path,text,mode=0o600:written.setdefault(str(path),text))
    monkeypatch.setattr(ru_mesh_remote,'run',lambda *args,**kwargs:None)
    assert ru_mesh_remote.container({'generation':2,'secret':'not-a-real-secret','image':'remnawave/node@sha256:fixture'})['ready']
    compose=written['/opt/kingline-ru-node-2/compose.yml']
    assert 'kingline-ru-remnanode-2' in compose and '10.79.2.2:2222' in compose and '10.78.2.1:15443' in compose
    unit=written['/etc/systemd/system/kingline-ru-node-2.service']
    assert 'wg-quick@wg-ru2.service' in unit and 'wg-quick@wg-ruc2.service' in unit
    assert '/opt/kingline-ru-node/compose.yml' not in written
    firewall=written['/etc/kingline-ru-firewall-2.nft']
    assert 'iifname "wg-ru2"' in firewall and '10.78.2.0/24' in firewall

def test_full_worker_orders_private_checks_before_switch_and_keeps_old_xui(manager,monkeypatch):
    from pathlib import Path
    old=prepare(manager);installer=manager.ru_installer
    manager.store.save_node({'uuid':'head','public_ip':'8.8.4.4','management_ip':'10.77.0.3','ru_enabled':True})
    job=installer.submit(receiver());calls=[]
    class SSH:
        fingerprint='SHA256:'+'a'*43
        def __init__(self,*args):pass
        def close(self):pass
        def run(self,command,**kwargs):return '9.9.9.9' if 'curl' in command else '0'
        def python(self,*args,**kwargs):return '{"ssh_key_only":true}'
    manager.ssh_factory=SSH
    def remote(request,payload,**kwargs):
        calls.append(payload['mode'])
        return {'ok':True,'public_key':'A'*43+'=','management_ip':'10.79.1.2'}
    monkeypatch.setattr(installer,'remote',remote);monkeypatch.setattr(installer,'core_peer',lambda *a,**k:None)
    read_bytes=Path.read_bytes
    monkeypatch.setattr(Path,'read_bytes',lambda p: b'fake-key' if str(p)=='/etc/wireguard/ru-control.key' else read_bytes(p))
    monkeypatch.setattr(ru_installer.subprocess,'check_output',lambda *a,**k:b'public-key')
    class API:
        def items(self,*a):return []
        def call(self,path,method='GET',body=None):
            if path=='keygen':return {'secretKey':'fake-node-secret'}
            if path=='nodes' and method=='POST':return {'uuid':'new-ru'}
            if path=='nodes/new-ru':return {'isConnected':True,'xrayUptime':30}
            return {}
    manager.api=API()
    def commit(context,generation,request,body):
        assert calls.count('probe')==1
        assert json.loads(installer.gateway.path.read_text())==old
        calls.append('commit');atomic_json(installer.gateway.path,{**old,'node_uuid':'new-ru','generation':generation})
    monkeypatch.setattr(installer,'commit',commit);monkeypatch.setattr(installer.gateway,'tick',lambda:None)
    installer.execute(job['id']);row=installer.read(job['id'])
    assert row['state']=='ready' and row['secret'] is None
    assert calls.index('probe')<calls.index('commit')<calls.index('retire')
    assert 'remove_old_xui' not in calls
    assert installer.jobs()[0]['progress']['verified_nodes']==1
