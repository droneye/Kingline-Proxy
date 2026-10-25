import json
import pytest
from node_manager.ru_egress import RUEgress
from node_manager.store import atomic_json

def setup(tmp_path):
    manager=tmp_path/'manager';manager.mkdir()
    control=tmp_path/'control';control.mkdir()
    state={'enabled':True,'pi':{'ip':'10.79.0.2','ssh_port':22},'mesh_peers':[],
           'direct_fallback_nodes':['germany'],'direct_targets':{'germany':'10.78.0.1'}}
    atomic_json(control/'ru-egress.json',state)
    service=RUEgress(manager,control)
    calls=[]
    def invoke(request,payload,timeout=90):
        calls.append((request,payload))
        return {'public_key':'public-test-key'}
    service.invoke=invoke
    return service,calls

def test_germany_keeps_direct_exit_without_any_remote_change(tmp_path):
    service,calls=setup(tmp_path)
    service.provision({'uuid':'germany'})
    assert calls==[]

def test_new_node_gets_direct_private_peer_once_and_never_a_relay(tmp_path):
    service,calls=setup(tmp_path)
    node={'uuid':'new-node','public_ip':'192.0.2.10','management_ip':'10.77.0.9'}
    service.provision(node)
    mesh=[payload for _,payload in calls if payload['mode']=='mesh']
    assert [p['role'] for p in mesh]==['pi','head']
    assert mesh[0]['peers'][0]['address']=='10.78.0.9'
    assert mesh[0]['peers'][0]['public_ip']=='192.0.2.10'
    assert 'endpoint_ip' not in mesh[0]['peers'][0]
    total=len(calls);service.provision(node);assert len(calls)==total

def test_failed_peer_is_not_published_and_removal_revokes_pi_first(tmp_path):
    service,calls=setup(tmp_path)
    node={'uuid':'new-node','public_ip':'192.0.2.10','management_ip':'10.77.0.9'}
    good=service.invoke
    def fail(request,payload,timeout=90):
        if payload.get('role')=='head':raise RuntimeError('unreachable')
        return good(request,payload,timeout)
    service.invoke=fail
    with pytest.raises(RuntimeError):service.provision(node)
    assert json.loads(service.path.read_text())['mesh_peers']==[]
    service.invoke=good;service.provision(node);calls.clear();service.remove(node)
    assert calls[0][1]['role']=='pi' and calls[0][1]['peers']==[]
    assert calls[1][1]['mode']=='remove' and calls[1][1]['remove_direct']
    assert json.loads(service.path.read_text())['mesh_peers']==[]

def test_checkbox_can_disable_and_reenable_one_peer_without_affecting_others(tmp_path):
    service,calls=setup(tmp_path)
    node={'uuid':'new-node','public_ip':'192.0.2.10','management_ip':'10.77.0.9','ru_enabled':True}
    service.provision(node);calls.clear();service.provision({**node,'ru_enabled':False})
    assert calls[0][1]['mode']=='direct'
    assert json.loads(service.path.read_text())['mesh_peers']==[]
    calls.clear();service.provision(node)
    assert any(payload.get('release_direct') for _,payload in calls)
    assert json.loads(service.path.read_text())['mesh_peers'][0]['uuid']=='new-node'
