"""Run only on the core. No user databases/keys leave the server.

Validates the current config on real nodes, then proves that exact same config is
a no-op (no profile PATCH or restart). A separate unassigned profile checks API
compatibility and is removed in finally. Never assigns a real node to a test config.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.request
import uuid

sys.path.insert(0,'/opt/kingline-manager/app')
from remna_api import RemnaAPI
from node_manager.vpn_config import rename_tags

root=Path('/opt/kingline-manager/data')
headers={'Authorization':'Bearer '+(root/'admin.token').read_text().strip(),'Content-Type':'application/json'}
def request(path,body=None):
    req=urllib.request.Request('http://127.0.0.1:8090'+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)
def wait(change_id,done):
    for _ in range(180):
        change=request('/v1/vpn/change?id='+change_id)['change']
        if change['state'] in done:return change
        if change['state'] in ('failed','rolled_back','rollback_failed'):raise AssertionError('vpn_check_failed:'+str(change.get('error')))
        time.sleep(1)
    raise AssertionError('vpn_check_timeout')

api=RemnaAPI()
before={p['uuid']:hashlib.sha256(json.dumps(api.call('config-profiles/'+p['uuid'])['config'],sort_keys=True).encode()).hexdigest() for p in api.items('config-profiles','configProfiles')}
catalog=request('/v1/vpn/config');pid=catalog['default_profile']
detail=request('/v1/vpn/config?profile='+pid)['profile']
assert 'privateKey' in json.dumps(detail['config']) and '__KINGLINE_KEEP_SECRET__' in json.dumps(detail['config'])
assert 'rawInbound' not in json.dumps(catalog)
raw=api.call('config-profiles/'+pid)
clone_id=None
try:
    check_id=uuid.uuid4().hex[:16]
    isolated=rename_tags(raw['config'],{i['tag']:'KL_check_'+check_id+'_'+str(index) for index,i in enumerate(raw['config']['inbounds'])})
    clone=api.call('config-profiles','POST',{'name':'KL-schema-check-'+uuid.uuid4().hex[:10],'config':isolated})
    clone_id=clone['uuid']
    first=api.call('config-profiles/'+clone_id)
    candidate=copy.deepcopy(first['config']);candidate.setdefault('log',{})['loglevel']='error'
    api.call('config-profiles','PATCH',{'uuid':clone_id,'config':candidate})
    second=api.call('config-profiles/'+clone_id)
    assert second['config']['log']['loglevel']=='error'
    assert [(i['uuid'],i['tag']) for i in first['inbounds']]==[(i['uuid'],i['tag']) for i in second['inbounds']]
finally:
    if clone_id:api.call('config-profiles/'+clone_id,'DELETE')

# The temporary profile changed global revision, so reload before checking.
detail=request('/v1/vpn/config?profile='+pid)['profile']
change=request('/v1/vpn/validate',{'profile_uuid':pid,'revision':detail['revision'],'request_id':str(uuid.uuid4()),
    'scope':'all','nodes':[],'config':detail['config'],'client':detail['client'],
    'note':'Проверка редактора: текущая конфигурация, без изменений'})['change']
checked=wait(change['id'],{'validated'})
assert len(checked['checks'])==len(catalog['nodes']) and all(c['ok'] for c in checked['checks'])
request('/v1/vpn/apply',{'id':change['id']})
final=wait(change['id'],{'noop'})
after={p['uuid']:hashlib.sha256(json.dumps(api.call('config-profiles/'+p['uuid'])['config'],sort_keys=True).encode()).hexdigest() for p in api.items('config-profiles','configProfiles')}
assert before==after,'live_config_changed'
print(json.dumps({'api_schema_compatible':True,'inbound_ids_preserved_on_patch':True,'real_nodes_validated':len(checked['checks']),
                  'noop_applied_without_config_change':final['state']=='noop','profiles_unchanged':before==after,'secrets_redacted':True}))
