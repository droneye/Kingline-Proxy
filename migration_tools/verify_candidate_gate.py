"""Server-only release verification. Never print credentials or attach a test to a live node."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import uuid
sys.path[:0]=['/opt/kingline-manager/app','/opt/kingline-control']
from node_manager.manager import Manager
from node_manager.models import AddNode
from node_manager.store import atomic_json
from remna_api import RemnaAPI

os.umask(0o077)
root=Path('/opt/kingline-manager/data');control=Path('/opt/kingline-control')
restricted=RemnaAPI(root/'panel.token')
api=RemnaAPI()
users_api=api
report={'at':time.time(),'kind':'isolated panel contract and real RF check of existing DK entry'}
probe_id=json.loads((control/'probe-user.json').read_text())['id']
probe_before=users_api.call('users/'+str(probe_id))
ids=lambda path,key:{r['uuid'] for r in api.items(path,key)}
collections=[('nodes','nodes'),('hosts','hosts'),('config-profiles','configProfiles'),('internal-squads','internalSquads')]
before={path:ids(path,key) for path,key in collections}
inventory_before=hashlib.sha256((control/'nodes.json').read_bytes()).hexdigest()
folder=Path(tempfile.mkdtemp(prefix='candidate-contract-',dir=control))
settings=json.loads((root/'settings.json').read_text());atomic_json(folder/'settings.json',settings)
m=Manager(folder,api=restricted)
m.onboarding._user_api=users_api
real=Manager(root)
job=m.submit(AddNode(ip='8.8.4.4',display_name='Contract candidate',country='FI',quota_tb=1,speed_mbps=1000),str(uuid.uuid4()))['id']
node_id=None
try:
    state=real.standard();binding=m.onboarding.profile(job,state)
    # Only a new, disconnected API record. No live server receives this profile.
    with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',65534))!=0,'contract_port_busy'
    node=api.call('nodes','POST',{'name':'KL-contract-'+uuid.uuid4().hex[:12],'address':'127.0.0.1',
        'port':65534,'countryCode':'FI','configProfile':binding})
    node_id=node['uuid'];assert node_id not in before['nodes']
    data=m.onboarding.data(job)
    data.update(candidate={'uuid':node_id,'label':'Contract candidate','public_ip':'203.0.113.88'},state=state)
    m.onboarding.save(job,data)
    result=m.onboarding.phone_key(job,data);data=m.onboarding.data(job)
    user=users_api.call('users/'+str(data['phone']['id']))
    assert user['trafficLimitBytes']==1024**3 and user['tag']=='NODE_TEST'
    squads=[s['uuid'] if isinstance(s,dict) else s for s in user['activeInternalSquads']]
    assert squads==[data['squad']]
    assert 7100<result['expires']-time.time()<7201
    others=[s for s in api.items('internal-squads','internalSquads') if s['uuid']!=data['squad']]
    assert not any(set(data['inbounds']) & {i['uuid'] if isinstance(i,dict) else i for i in s['inbounds']} for s in others)
    # computed-config resolves snippets; this panel leaves clients empty even on
    # live profiles. Accessible-nodes is its canonical authorization relation.
    accessible=lambda uid:{n['uuid'] for n in users_api.call('users/'+str(uid)+'/accessible-nodes')['activeNodes']}
    report['phone_only_candidate']=accessible(data['phone']['id'])=={node_id}
    report['probe_can_check_candidate']=node_id in accessible(probe_id)
    report['owner_cannot_use_candidate']=node_id not in accessible(state['user']['id'])
    assert report['phone_only_candidate'] and report['probe_can_check_candidate'] and report['owner_cannot_use_candidate']
    assert data['phone']['vlessUuid'] not in json.dumps(m.onboarding.public(job))
    report['isolated_phone_key']=True
finally:
    if node_id: m.onboarding.delete('nodes',node_id)
    m.onboarding.cleanup_profile(job)
    report['panel_resources_restored']=all(ids(path,key)==before[path] for path,key in collections)
    probe_after=users_api.call('users/'+str(probe_id))
    squad_ids=lambda u:sorted(s['uuid'] if isinstance(s,dict) else s for s in u['activeInternalSquads'])
    report['service_probe_rights_restored']=squad_ids(probe_before)==squad_ids(probe_after)
    report['public_inventory_unchanged']=inventory_before==hashlib.sha256((control/'nodes.json').read_bytes()).hexdigest()
    atomic_json(control/'candidate-gate-verification.json',report)
assert all(report[k] for k in ('panel_resources_restored','service_probe_rights_restored','public_inventory_unchanged'))

# Read-only configuration of an existing entry. Real bounded traffic uses SERVICE_PROBE.
dk=next(n for n in real.store.nodes() if n.get('country')=='DK' and n.get('provisioned',True))
state=real.standard();data={'candidate':dk,'state':state,'standard_revision':'0'*64}
result=real.onboarding.remote_check(data)
report['real_rf']={'ok':result['ok'],'source_ok':result['source_ok'],'error':result.get('error'),
    'transports':{key:{'ok':value['ok'],'attempts':len(value['attempts']),
    'error_codes':[a['error'] for a in value['attempts'] if a.get('error')]} for key,value in result['transports'].items()}}
atomic_json(control/'candidate-gate-verification.json',report)
print(json.dumps(report))
