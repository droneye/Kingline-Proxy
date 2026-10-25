import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time

import pytest
from pydantic import ValidationError

from node_manager.node_network import NodeNetwork, compile_relays, route_paths, usage_sample
from node_manager.models import NodeConnection, NodeRoutes
from node_manager.ssh import SafeError
from node_manager.store import atomic_json
from node_manager.tests.test_vpn_config import editor, uid  # noqa: F401 (shared fixture)


@pytest.fixture
def network(editor):
    for i,n in enumerate(editor.m.store.nodes()):
        editor.m.store.save_node(n|{'host_uuid':editor.api.hosts[1+i]['uuid'],'label':n['name'],'reset_day':19,
            'quota_bytes':5*10**12,'speed_mbps':1000,'smart_enabled':True})
        editor.api.nodes[i]['trafficUsedBytes']=100
    for i,h in enumerate(editor.api.hosts):
        h.update(viewPosition=i,remark='Smart' if not i else 'Node '+str(i),
                 address='auto.edge.kingline.online' if not i else editor.m.store.nodes()[i-1]['public_ip'])
    original=editor.api.call
    def call(path,method='GET',body=None):
        if path=='hosts/actions/reorder':
            editor.api.mutations.append((method,path,copy.deepcopy(body)))
            for value in body['hosts']:
                next(h for h in editor.api.hosts if h['uuid']==value['uuid'])['viewPosition']=value['viewPosition']
            return {}
        return original(path,method,body)
    editor.api.call=call
    obj=NodeNetwork(editor.m,editor);editor.m.network=obj
    def export():
        atomic_json(editor.m.control/'nodes.json',obj.enrich(obj.nodes()));obj.export()
    editor.m.export_inventory=export
    obj.resources={};obj.remote_calls=[];obj.fail_apply=None;obj.fail_check=False
    def remote(node,body,timeout=0):
        obj.remote_calls.append((node['uuid'],copy.deepcopy(body)))
        action=body['action']
        if action=='snapshot':return {'files':{},'firewall':'original','usage':{'boot_id':'boot','counter':1000,'at':time.time()}}
        if action=='stats':return {'boot_id':'boot','counter':2000,'at':time.time()}
        if action=='check' and obj.fail_check:raise SafeError('remote_operation_failed')
        if action=='apply':
            obj.resources[node['uuid']]=copy.deepcopy(body)
            if obj.fail_apply==node['uuid']:
                obj.fail_apply=None;raise SafeError('remote_operation_failed')
        if action=='restore':obj.resources.pop(node['uuid'],None)
        return {'ok':True}
    obj.remote=remote
    return obj


def body(n,**kwargs):
    return {'revision':n.catalog()['revision'],'request_id':uid(),**kwargs}


def apply(n,kind,b):
    job=n.submit(kind,b);n.vpn.process(job['id']);return n.vpn.public(job['id'])


def test_catalog_contains_only_small_fields_and_server_order(network):
    data=network.catalog()
    assert len(data['keys'])==3 and data['keys'][0]['smart']
    assert len(data['nodes'])==2 and data['common_sni']=='example.org'
    encoded=json.dumps(data)
    assert 'do-not-expose' not in encoded and 'privateKey' not in encoded and 'config' not in data


def test_subscription_order_is_persisted_in_panel_for_all_client_formats(network):
    old=network.catalog();desired=[x['uuid'] for x in reversed(old['keys'])]
    job=apply(network,'order',body(network,hosts=desired))
    assert job['state']=='ready'
    assert [x['uuid'] for x in network.catalog()['keys']]==desired
    assert not network.remote_calls
    assert not any(path in ('nodes','config-profiles') for _,path,_ in network.api.mutations)
    network.rollback(job['id'],network.catalog()['revision']);network.vpn.process(job['id'])
    assert [x['uuid'] for x in network.catalog()['keys']]==[x['uuid'] for x in old['keys']]


def test_identical_connection_is_a_noop_without_restart_or_relay_install(network):
    node=network.catalog()['nodes'][0]
    job=apply(network,'connection',body(network,uuid=node['uuid'],sni=node['sni'],target=node['target']))
    assert job['state']=='noop' and not network.api.mutations and not network.remote_calls


def test_single_sni_target_is_isolated_preserves_users_and_excludes_only_that_smart_route(network):
    before=network.catalog();node=before['nodes'][0];other=before['nodes'][1]
    job=apply(network,'connection',body(network,uuid=node['uuid'],sni='new.example.org',target='new.example.org:443'))
    assert job['state']=='ready'
    assert network.api.profiles[0]['config']['inbounds'][0]['streamSettings']['realitySettings']['serverNames']==['example.org']
    assert len(network.api.profiles)==2
    assert network.api.profiles[1]['config']['inbounds'][0]['streamSettings']['realitySettings']['serverNames']==['new.example.org','example.org']
    selected=next(n for n in network.catalog()['nodes'] if n['uuid']==node['uuid'])
    assert selected['sni']=='new.example.org' and selected['target']=='new.example.org:443' and not selected['auto_eligible']
    assert next(n for n in network.catalog()['nodes'] if n['uuid']==other['uuid'])['auto_eligible']
    assert len(network.api.squads[0]['inbounds'])==2
    assert network.api.hosts[0]['sni']=='example.org' and network.api.hosts[0]['port']==443
    assert network.enrich(network.nodes())[0]['smart_enabled'] is True  # user's preference remains intact
    network.rollback(job['id'],network.catalog()['revision']);network.vpn.process(job['id'])
    assert network.catalog()['nodes'][0]['sni']=='example.org' and len(network.api.profiles)==1


def test_chain_has_blind_tcp_forwarding_keeps_exit_authentication_and_changes_only_its_host(network):
    nodes=network.nodes();exit_id,entry_id=[n['uuid'] for n in nodes]
    old_auto=copy.deepcopy(network.api.hosts[0]);profiles=copy.deepcopy(network.api.profiles)
    job=apply(network,'routes',body(network,via={exit_id:entry_id,entry_id:None}))
    assert job['state']=='ready'
    resource=network.resources[entry_id]
    assert nodes[0]['public_ip']+':443' in resource['config'] and 'bind 0.0.0.0:24000' in resource['config']
    assert 'ssl' not in resource['config'] and 'password' not in resource['config']
    host=network.api.hosts[1]
    assert host['address']==nodes[1]['public_ip'] and host['port']==24000 and host['nodes']==[exit_id]
    assert network.api.hosts[0]==old_auto and network.api.profiles==profiles
    enriched=network.enrich(nodes)
    assert not enriched[0]['network_auto_eligible'] and enriched[1]['network_auto_eligible']
    assert enriched[1]['relay_accounted']
    assert enriched[0]['manual_connection']['connect_address']==nodes[1]['public_ip']
    network.rollback(job['id'],network.catalog()['revision']);network.vpn.process(job['id'])
    assert not network.resources and network.api.hosts[1]['address']==nodes[0]['public_ip']
    assert network.enrich(nodes)[1]['relay_accounted']  # consumed traffic is never rolled back


def test_cycles_unknown_nodes_and_duplicate_keys_fail_before_any_remote_action(network):
    a,b=[n['uuid'] for n in network.nodes()]
    for via in ({a:b,b:a},{a:a,b:None},{a:uid(),b:None}):
        with pytest.raises(SafeError):network.submit('routes',body(network,via=via))
    keys=[k['uuid'] for k in network.catalog()['keys']]
    with pytest.raises(SafeError):network.submit('order',body(network,hosts=[keys[0]]*len(keys)))
    assert not network.remote_calls and not network.api.mutations


def test_compile_three_hops_and_multiple_branches_use_direct_node_addresses():
    nodes=[{'uuid':str(i),'public_ip':'8.8.8.'+str(i)} for i in range(1,5)]
    state={'via':{'1':None,'2':'1','3':'2','4':'1'},'ports':{'2':24000,'3':24001,'4':24002}}
    paths,configs=compile_relays(state,nodes)
    assert paths['3']==['1','2','3']
    assert 'server hop 8.8.8.2:24001' in configs['1']['config']
    assert 'server hop 8.8.8.3:443' in configs['2']['config']
    assert configs['1']['ports']==[24000,24001,24002]
    assert configs['3']['ports']==[]


def test_failed_relay_application_restores_firewall_and_does_not_publish_broken_endpoint(network):
    a,b=[n['uuid'] for n in network.nodes()];before=network.catalog();hosts=copy.deepcopy(network.api.hosts)
    network.fail_apply=b
    job=apply(network,'routes',body(network,via={a:b,b:None}))
    assert job['state']=='rolled_back' and not network.resources
    assert network.api.hosts==hosts and network.catalog()['revision']==before['revision']


def test_failed_sni_canary_restores_groups_profile_binding_and_host(network):
    before=network.catalog();node=before['nodes'][0]
    def fail(*args,**kwargs):raise RuntimeError('network unavailable')
    network.m.probe=fail;network.m.stop.wait=lambda *_:False
    job=apply(network,'connection',body(network,uuid=node['uuid'],sni='bad.example.org',target='bad.example.org:443'))
    assert job['state']=='rolled_back' and job['error']=='network_tunnel_check_failed'
    assert len(network.api.profiles)==1 and len(network.api.squads[0]['inbounds'])==1
    assert network.catalog()['revision']==before['revision']


def test_recovery_compensates_an_interrupted_relay_change(network):
    a,b=[n['uuid'] for n in network.nodes()]
    job=network.submit('routes',body(network,via={a:b,b:None}));_,data=network.vpn.read(job['id'])
    node=network.nodes()[1];before=network.remote(node,{'action':'snapshot'})
    network.vpn.journal(job['id'],data,{'relay_node':node,'before':before})
    network.resources[b]={'config':'partially installed'}
    network.vpn.process(job['id'],recover=True)
    assert network.vpn.public(job['id'])['state']=='rolled_back' and not network.resources


def test_adding_a_node_preserves_custom_order_and_appends_its_key(network):
    keys=[h['uuid'] for h in network.catalog()['keys']]
    job=apply(network,'order',body(network,hosts=keys[::-1]));assert job['state']=='ready'
    new=copy.deepcopy(network.nodes()[0]);new.update(uuid=uid(),public_ip='9.9.9.9',name='New',host_uuid=uid())
    network.m.store.save_node(new)
    panel=copy.deepcopy(network.api.nodes[0]);panel.update(uuid=new['uuid'],name='New');network.api.nodes.append(panel)
    host=copy.deepcopy(network.api.hosts[1]);host.update(uuid=new['host_uuid'],remark='New',nodes=[new['uuid']],viewPosition=0)
    network.api.hosts.append(host)
    network.on_node_added(new['host_uuid'])
    assert [k['uuid'] for k in network.catalog()['keys']]==keys[::-1]+[new['host_uuid']]


def test_removal_cannot_break_other_routes(network):
    a,b=[n['uuid'] for n in network.nodes()]
    atomic_json(network.path,network.state()|{'via':{a:b,b:None},'ports':{a:24000}})
    with pytest.raises(SafeError,match='has_dependents'):network.removal_guard(b)
    with pytest.raises(SafeError,match='disconnect_before_remove'):network.removal_guard(a)


def test_physical_usage_survives_reload_reboot_and_monthly_reset():
    now=datetime(2026,10,23,tzinfo=timezone.utc)
    first=usage_sample(None,{'boot_id':'a','counter':1000},19,500,now)
    second=usage_sample(first,{'boot_id':'a','counter':1400},19,500,now)
    assert second['used_bytes']==900
    reboot=usage_sample(second,{'boot_id':'b','counter':200},19,500,now)
    assert reboot['used_bytes']==1100
    reset=usage_sample(reboot,{'boot_id':'b','counter':500},19,999999,datetime(2026,11,19,tzinfo=timezone.utc))
    assert reset['used_bytes']==0


@pytest.mark.parametrize('target',['127.0.0.1:443','[fc00::1]:443','example.org','example.org:443/abc','user:pass@example.org:443','example.org:443\ncommand'])
def test_target_validation_rejects_local_or_malformed_destinations(target):
    with pytest.raises(ValidationError):NodeConnection.model_validate({'uuid':uid(),'revision':'a'*64,'request_id':uid(),'sni':'example.org','target':target})


def test_route_changes_and_rollback_keep_domain_and_reconcile_entry(network, monkeypatch):
    from node_manager import domains
    network.m.settings = {'node_domains': True}
    states = []
    monkeypatch.setattr(domains, 'sync', lambda control, nodes, state: states.append(domains.desired_records(nodes, state)))
    a, b = network.nodes(); before = network.catalog()
    job = apply(network, 'routes', body(network, via={a['uuid']: b['uuid'], b['uuid']: None}))
    assert job['state'] == 'ready'
    address = domains.hostname(a)
    assert next(h['address'] for h in network.api.hosts if h['uuid'] == a['host_uuid']) == address
    assert states[-1][address + '.'] == b['public_ip']
    network.rollback(job['id'], network.catalog()['revision']); network.vpn.process(job['id'])
    assert states[-1][address + '.'] == a['public_ip']
    assert network.catalog()['revision'] == before['revision']


def test_failed_dns_route_update_compensates_without_publishing_a_new_host(network, monkeypatch):
    from node_manager import domains
    network.m.settings = {'node_domains': True}
    before = copy.deepcopy(network.api.hosts); calls = []
    def sync(control, nodes, state):
        calls.append(state)
        if len(calls) == 1: raise SafeError('node_domains_replica_update_failed')
    monkeypatch.setattr(domains, 'sync', sync)
    a, b = network.nodes()
    job = apply(network, 'routes', body(network, via={a['uuid']: b['uuid'], b['uuid']: None}))
    assert job['state'] == 'rolled_back' and len(calls) == 2
    assert network.api.hosts == before
