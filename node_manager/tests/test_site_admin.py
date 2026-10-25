import json
import time
from pathlib import Path
import uuid
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from node_manager.site_admin import create_app, Vault, Metadata, LogReader
from node_manager.models import Credentials, AddNode, Capacity
from node_manager.diagnostics import sanitize

NODE='11111111-1111-4111-a111-111111111111'

def test_inventory_returns_fresh_resources_without_host_secrets(root,tmp_path,monkeypatch):
    site=tmp_path/'website';(site/'logs').mkdir(parents=True)
    monkeypatch.setenv('KINGLINE_SITE_ROOT',str(site))
    sample={'at':time.time(),'cpu_percent':12.5,'ram_percent':40,'disk_percent':30,'private_key':'secret-canary'}
    file=site/'logs/resources.json';file.write_text(json.dumps({'core':sample,'nodes':{NODE:sample}}))
    c=TestClient(create_app(root,FakeManager(),False))
    data=c.get('/nodes').json()
    assert data['core']['resources']['cpu_percent']==12.5
    assert data['nodes'][0]['resources']['ram_percent']==40
    assert 'secret-canary' not in json.dumps(data)
    sample['at']-=121;file.write_text(json.dumps({'core':sample,'nodes':{NODE:sample}}))
    assert c.get('/nodes').json()['nodes'][0]['resources'] is None

def test_ru_service_resources_are_read_only_and_do_not_expose_secrets(root,tmp_path,monkeypatch):
    control=tmp_path/'control';control.mkdir()
    monkeypatch.setenv('KINGLINE_CONTROL_ROOT',str(control))
    report=control/'ru-resources.json'
    report.write_text(json.dumps({'at':time.time(),'online':True,'public_ip':'192.0.2.1',
        'resources':{'at':time.time(),'cpu_percent':8,'private_key':'secret-canary'},'secret':'hidden-canary'}))
    c=TestClient(create_app(root,FakeManager(),False));data=c.get('/nodes').json()
    assert data['service_nodes'][0]['online']
    assert data['service_nodes'][0]['resources']['cpu_percent']==8
    assert all(n['public_ip']!='192.0.2.1' for n in data['nodes'])
    assert 'canary' not in json.dumps(data)
    value=json.loads(report.read_text());value['at']-=121;value['resources']['at']-=121
    report.write_text(json.dumps(value));service=c.get('/nodes').json()['service_nodes'][0]
    assert not service['online'] and service['resources'] is None

class FakeManager:
    def __init__(self):self.calls=[];self.removed=False
    def call(self,method,path,payload=None,idem=None):
        self.calls.append((method,path,payload,idem))
        if path=='/v1/nodes' and method=='GET':return {'nodes':[] if self.removed else [{'uuid':NODE,'name':'DE-Test','public_ip':'8.8.4.4','managed_ssh':True,'removable':True,'private_key':'manager-private-canary','quota_bytes':5000000000000,'speed_mbps':1000,'reset_day':19,'maintenance':False}],'routing':{'nodes':[]}}
        if path=='/v1/jobs':return {'jobs':[]}
        if path.endswith('/preflight'):return {'ok':True,'ubuntu':'24.04','host_key_sha256':'SHA256:'+'x'*43}
        if path=='/v1/nodes' and method=='POST':return {'id':'job','state':'queued'}
        if method=='PATCH':return {'uuid':NODE}
        if method=='DELETE':self.removed=True;return {'removed':True}
        raise HTTPException(404,'not_found')

@pytest.fixture
def root(tmp_path):
    (tmp_path/'credentials.key').write_bytes(Fernet.generate_key());return tmp_path

def enrollment():return {'node':{'ip':'8.8.4.4','display_name':'Германия','country':'DE','quota_tb':5,'speed_mbps':1000,'reset_day':31,'credentials':{'password':'canary-do-not-log'}},'metadata':{'description':'provider','monthly_cost':4.9,'currency':'EUR'},'request_id':str(uuid.uuid4())}

def test_vault_encrypts_and_merges_without_plaintext(root):
    v=Vault(root);v.save('8.8.4.4',Metadata(monthly_cost=4.9),Credentials(password='canary-password'))
    v.save('8.8.4.4',Metadata(description='new'))
    assert v.reveal('8.8.4.4')=={'password':'canary-password'}
    assert v.metadata('8.8.4.4')['description']=='new'
    assert b'canary-password' not in b''.join(p.read_bytes() for p in root.glob('site-admin.sqlite3*'))
    assert v.path.stat().st_mode & 0o777==0o600

def test_reminder_flag_defaults_on_and_update_can_disable_without_changing_secrets(root):
    v=Vault(root);v.save('8.8.4.4',Metadata(monthly_cost=4.9),Credentials(password='canary-password'))
    with v.db() as db:
        db.execute('UPDATE inventory SET metadata=?',(json.dumps({'monthly_cost':4.9,'currency':'EUR','description':'old row'}),))
    assert v.metadata('8.8.4.4')['payment_reminders_enabled'] is True
    c=TestClient(create_app(root,FakeManager(),False))
    r=c.post('/update',json={'uuid':NODE,'metadata':{'monthly_cost':4.9,'payment_reminders_enabled':False}})
    assert r.status_code==200
    assert v.metadata('8.8.4.4')['payment_reminders_enabled'] is False
    assert v.reveal('8.8.4.4')['password']=='canary-password'
    v.save('8.8.4.4',Metadata(monthly_cost=5,description='old client without the flag'))
    assert v.metadata('8.8.4.4')['payment_reminders_enabled'] is False

def test_no_mutation_or_secrets_on_inventory_get(root):
    fake=FakeManager();v=Vault(root);v.save('8.8.4.4',Metadata(),Credentials(password='canary-password'))
    c=TestClient(create_app(root,fake,False));r=c.get('/nodes')
    assert r.status_code==200 and r.json()['nodes'][0]['admin']['stored_credentials']
    assert 'canary-password' not in r.text and 'manager-private-canary' not in r.text
    assert all(method=='GET' for method,_,_,_ in fake.calls)

def test_provider_url_survives_old_clients_and_can_be_cleared(root):
    fake=FakeManager();c=TestClient(create_app(root,fake,False));body=enrollment()
    body['metadata']['provider_url']='  billing.example.org/account  '
    assert c.post('/add',json=body).status_code==202
    data=c.get('/nodes').json()['nodes'][0]['admin']
    assert data['provider_url']=='https://billing.example.org/account'
    assert data['description']=='provider'
    # A tab with the old interface must not erase a previously saved URL.
    assert c.post('/update',json={'uuid':NODE,'metadata':{'description':'edited note'}}).status_code==200
    assert c.app.state.vault.metadata('8.8.4.4')['provider_url']==data['provider_url']
    assert c.app.state.vault.reveal('8.8.4.4')['password']=='canary-do-not-log'
    assert c.post('/update',json={'uuid':NODE,'metadata':{'description':'edited note','provider_url':''}}).status_code==200
    assert c.get('/nodes').json()['nodes'][0]['admin']['provider_url']==''
    assert not any(call[0]=='PATCH' for call in fake.calls)

@pytest.mark.parametrize('url',[
    'javascript:alert(1)','data:text/html,<script>alert(1)</script>',
    'file:///etc/passwd','ftp://example.org','//example.org',
    'https://user:secret-canary@example.org','https://example.org\\@evil.invalid',
    'https://example.org/\nsecret-canary','https://',
])
def test_provider_url_rejects_unsafe_links_without_mutation_or_echo(root,url):
    fake=FakeManager();c=TestClient(create_app(root,fake,False))
    response=c.post('/update',json={'uuid':NODE,'metadata':{'provider_url':url}})
    assert response.status_code==422 and 'secret-canary' not in response.text
    assert fake.calls==[]

def test_job_progress_only_exposes_status_and_phase_history(root):
    class ProgressManager(FakeManager):
        def call(self,method,path,payload=None,idem=None):
            assert method=='GET' and path=='/v1/jobs/'+NODE
            return {'id':NODE,'ip':'8.8.4.4','state':'running','phase':'container','error':None,
                    'created':10,'updated':20,'credentials':{'password':'secret-canary'},
                    'verification':{'private_key':'tunnel-canary'},
                    'events':[{'at':20,'phase':'container','password':'event-canary'}]}
    c=TestClient(create_app(root,ProgressManager(),False))
    response=c.get('/job',params={'id':NODE})
    assert response.status_code==200
    job=response.json()['job']
    assert job['phase']=='container' and job['state']=='running'
    assert job['events']==[{'at':20,'phase':'container'}]
    assert 'canary' not in response.text and 'credentials' not in job and 'verification' not in job

def test_job_progress_rejects_paths_before_contacting_manager(root):
    fake=FakeManager();c=TestClient(create_app(root,fake,False))
    for job in ('../nodes','abc','../'+NODE):
        assert c.get('/job',params={'id':job}).status_code==422
    assert fake.calls==[]

def test_candidate_action_is_a_narrow_validated_manager_call(root):
    class CandidateManager(FakeManager):
        def call(self,method,path,payload=None,idem=None):
            self.calls.append((method,path,payload,idem))
            assert method=='POST' and path=='/v1/installation-action'
            return {'ok':True,'job':{'id':NODE,'state':'queued'}}
    fake=CandidateManager();c=TestClient(create_app(root,fake,False))
    body={'job_id':NODE,'operation':'check','request_id':str(uuid.uuid4())}
    assert c.post('/installation-action',json=body).json()['job']['state']=='queued'
    assert fake.calls[0][2]['operation']=='check'
    body['operation']='execute-arbitrary-command'
    assert c.post('/installation-action',json=body).status_code==422
    assert len(fake.calls)==1

def test_validation_never_echoes_secrets(root):
    c=TestClient(create_app(root,FakeManager(),False));body=enrollment();body['node']['ip']='127.0.0.1'
    r=c.post('/add',json=body)
    assert r.status_code==422 and 'canary-do-not-log' not in r.text and '127.0.0.1' not in r.text
    assert c.post('/credentials',content='x'*65537).status_code==413

def test_preflight_does_not_store_and_add_preserves_credentials(root):
    fake=FakeManager();c=TestClient(create_app(root,fake,False));body=enrollment()
    assert c.post('/preflight',json=body).status_code==200
    assert not c.app.state.vault.metadata('8.8.4.4')['stored_credentials']
    assert c.post('/add',json=body).status_code==202
    assert c.app.state.vault.reveal('8.8.4.4')['password']=='canary-do-not-log'
    assert fake.calls[-1][3]==body['request_id']

def test_reveal_explicit_and_removal_durable_idempotent(root):
    fake=FakeManager();app=create_app(root,fake,True)
    with TestClient(app) as c:
        body=enrollment();c.post('/add',json=body)
        assert c.post('/credentials',json={'uuid':NODE}).json()['credentials']['password']=='canary-do-not-log'
        wrong={'uuid':NODE,'name':'wrong','ip':'8.8.4.4'}
        assert c.post('/remove',json=wrong).status_code==409
        good={**wrong,'name':'DE-Test'}
        result=c.post('/remove',json=good)
        assert result.status_code==202
        for _ in range(30):
            ops=app.state.vault.operations()
            if ops[0]['state']=='done':break
            time.sleep(.1)
        assert ops[0]['state']=='done'
        assert not app.state.vault.reveal('8.8.4.4')
    assert sum(method=='DELETE' for method,_,_,_ in fake.calls)==1

def test_queue_recovers_after_restart(root):
    from node_manager.site_admin import DeleteNode
    v=Vault(root);op=v.queue_removal(DeleteNode(uuid=NODE,name='DE-Test',ip='8.8.4.4'))
    assert v.queue_removal(DeleteNode(uuid=NODE,name='DE-Test',ip='8.8.4.4'))['id']==op['id']
    with v.db() as db:db.execute("UPDATE operations SET state='running'")
    with TestClient(create_app(root,FakeManager(),True)) as c:
        for _ in range(30):
            if c.app.state.vault.operations()[0]['state']=='done':break
            time.sleep(.1)
        assert c.app.state.vault.operations()[0]['state']=='done'

def test_logs_redact_and_keep_bounded_technical_fields(root,tmp_path):
    site=root/'site';(site/'logs').mkdir(parents=True)
    event={'ts':'2026-10-23T00:00:00+00:00','service':'api','event':'test','level':'ERROR','message':'password=canary-pass Bearer abcdefghijkl','private_key':'hidden','body':{'secret':'hidden'},'stack':[{'file':'server.py','line':1}]}
    (site/'logs/diagnostics.jsonl').write_text(json.dumps(event)+'\n{broken\n')
    r=LogReader(root,site);r.health=lambda:[]
    data=r.snapshot();text=json.dumps(data)
    assert 'canary-pass' not in text and 'abcdefghijkl' not in text and 'hidden' not in text
    assert data['events'][0]['stack'][0]['file']=='server.py'

def test_private_key_and_password_redaction():
    value='password="canary" -----BEGIN OPENSSH PRIVATE KEY-----\nsecretmaterial\n-----END OPENSSH PRIVATE KEY-----'
    text=sanitize(value)
    assert 'canary' not in text and 'secretmaterial' not in text

def test_panel_supports_reset_on_month_end():
    assert Capacity(reset_day=31).reset_day==31
    with pytest.raises(Exception):Capacity(reset_day=32)


def test_metadata_only_edit_never_reconfigures_running_node(root):
    fake=FakeManager();c=TestClient(create_app(root,fake,False))
    body={'uuid':NODE,'metadata':{'description':'notes','monthly_cost':4.9},'capacity':{'quota_tb':5,'speed_mbps':1000,'reset_day':19,'maintenance':False}}
    assert c.post('/update',json=body).status_code==200
    assert not any(method=='PATCH' for method,_,_,_ in fake.calls)
    assert c.app.state.vault.metadata('8.8.4.4')['description']=='notes'
    body['capacity']['quota_tb']=6
    assert c.post('/update',json=body).status_code==200
    patches=[payload for method,_,payload,_ in fake.calls if method=='PATCH']
    assert patches==[{'quota_tb':6}]


def test_collector_health_uses_results_not_only_recent_file(root,monkeypatch):
    import urllib.request
    import subprocess
    def offline(*args,**kwargs):raise OSError('offline')
    monkeypatch.setattr(urllib.request,'urlopen',offline)
    monkeypatch.setattr(subprocess,'check_output',lambda *a,**k:'/api running healthy 0')
    site=root/'site';(site/'logs').mkdir(parents=True);(site/'run').mkdir()
    (site/'run/bot-heartbeat').touch()
    report=site/'logs/collector-status.json'
    reader=LogReader(root,site)
    def collector():return next(h for h in reader.health() if h['name']=='Сбор статистики')
    report.write_text(json.dumps({'snapshot':False,'nodes':{}}));assert not collector()['ok']
    report.write_text(json.dumps({'snapshot':True,'nodes':{'a':{'ok':True}}}));assert collector()['ok']
    report.write_text(json.dumps({'snapshot':True,'nodes':{'a':{'ok':True}},'backpressure':True}));assert not collector()['ok']


def test_smart_choice_flows_from_enrollment_and_changes_only_when_needed(root):
    fake=FakeManager();c=TestClient(create_app(root,fake,False));body=enrollment()
    assert c.get('/nodes').json()['nodes'][0]['smart_enabled'] is True
    body['node']['smart_enabled']=False
    assert c.post('/add',json=body).status_code==202
    creation=next(call for call in fake.calls if call[0]=='POST' and call[1]=='/v1/nodes')
    assert creation[2]['smart_enabled'] is False
    payload={'uuid':NODE,'metadata':{},'capacity':{'smart_enabled':True}}
    assert c.post('/update',json=payload).status_code==200
    assert not any(call[0]=='PATCH' for call in fake.calls)
    payload['capacity']['smart_enabled']=False
    assert c.post('/update',json=payload).status_code==200
    patches=[call for call in fake.calls if call[0]=='PATCH']
    assert len(patches)==1 and patches[0][2]=={'smart_enabled':False}
