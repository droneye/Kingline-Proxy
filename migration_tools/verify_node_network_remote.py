"""Core-only acceptance. No cutover: same order/settings, temporary unpublished relay.

All user credentials stay on the core. Before/after snapshots and an encrypted
recovery record remain there until verification has completed successfully.
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
from node_manager.cover import certificate_lock
from node_manager.node_network import REMOTE
from node_manager.probe import verify_tunnel
from node_manager.ssh import SSH
from node_manager.store import Store,atomic_json

root=Path('/opt/kingline-manager/data');control=Path('/opt/kingline-control')
api=RemnaAPI();store=Store(root)
headers={'Authorization':'Bearer '+(root/'admin.token').read_text().strip(),'Content-Type':'application/json'}
def request(path,body=None):
    req=urllib.request.Request('http://127.0.0.1:8090'+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)
def wait(change_id):
    for _ in range(60):
        change=request('/v1/vpn/change?id='+change_id)['change']
        if change['state'] in ('noop','ready'):return change
        if change['state'] in ('failed','rolled_back','rollback_failed'):raise AssertionError('network_job_failed:'+str(change.get('error')))
        time.sleep(1)
    raise AssertionError('network_job_timeout')
def signatures():
    return {p['uuid']:hashlib.sha256(json.dumps(api.call('config-profiles/'+p['uuid'])['config'],sort_keys=True).encode()).hexdigest() for p in api.items('config-profiles','configProfiles')}
before=signatures()
layout=request('/v1/node-network')
assert len(layout['nodes'])==3 and len(layout['keys'])==4
assert not any(n['via'] for n in layout['nodes'])
assert 'privateKey' not in json.dumps(layout) and 'config' not in layout
for action,body in [('order',{'hosts':[k['uuid'] for k in layout['keys']]}),
                    ('routes',{'via':{n['uuid']:n['via'] for n in layout['nodes']}}),
                    ('connection',{k:layout['nodes'][0][k] for k in ('uuid','sni','target')})]:
    layout=request('/v1/node-network')
    result=request('/v1/node-network/'+action,{'revision':layout['revision'],'request_id':str(uuid.uuid4()),**body})
    assert wait(result['change']['id'])['state']=='noop'
# Exercise installed reorder API without changing any position or user key.
hosts=api.items('hosts','hosts');order=[h['uuid'] for h in sorted(hosts,key=lambda h:(h['viewPosition'],h['uuid']))]
api.call('hosts/actions/reorder','POST',{'hosts':[{'uuid':h['uuid'],'viewPosition':h['viewPosition']} for h in hosts]})
assert order==[h['uuid'] for h in sorted(api.items('hosts','hosts'),key=lambda h:(h['viewPosition'],h['uuid']))]

# An unpublished blind TCP relay proves HTTPS and XUDP without changing subscriptions.
nodes=store.nodes();entry=next(n for n in nodes if n['country']=='DE');exit=next(n for n in nodes if n['country']=='DK')
req={'ip':entry['public_ip'],'ssh_port':entry.get('ssh_port',22),'host_key_sha256':entry.get('host_key_sha256')}
def remote(payload):
    ssh=SSH(root,req)
    try:return json.loads(ssh.python(REMOTE,payload,timeout=400))
    finally:ssh.close()
report={'nodes':len(layout['nodes']),'subscription_keys':len(layout['keys']),'noops_passed':3,'installed_reorder_api':True}
with certificate_lock(root):
    saved=remote({'action':'snapshot'})
    recovery=root/'relay-verification-recovery.enc'
    recovery.write_bytes(store.cipher.encrypt(json.dumps({'node':entry,'before':saved}).encode()));recovery.chmod(0o600)
    config='''global
  maxconn 32
defaults
  mode tcp
  timeout connect 5s
  timeout client 2m
  timeout server 2m
listen canary
  bind 0.0.0.0:24999
  server exit '''+exit['public_ip']+''':443
'''
    try:
        remote({'action':'apply','config':config,'ports':[24999]})
        result=verify_tunnel(control,exit['public_ip'],connect_address=entry['public_ip'],server_port=24999)
        assert result['https'] and result['udp_dns']
        # Reload while an authenticated TCP connection is being served; listener check
        # also verifies the master/worker signal and new generation startup.
        remote({'action':'apply','config':config,'ports':[24999]})
        second=verify_tunnel(control,exit['public_ip'],connect_address=entry['public_ip'],server_port=24999)
        assert second['https'] and second['udp_dns']
        report.update(relay_https=True,relay_udp_dns=True,relay_reload=True)
    finally:
        remote({'action':'restore','before':saved})
        restored=remote({'action':'snapshot'})
        assert restored['files']==saved['files'] and restored['firewall']==saved['firewall'],'relay_restore_failed'
        recovery.unlink()
report['profiles_unchanged']=signatures()==before
assert report['profiles_unchanged']
report['subscriptions_and_routes_unchanged']=not any(n['via'] for n in request('/v1/node-network')['nodes'])
assert report['subscriptions_and_routes_unchanged']
print(json.dumps(report))
