import pytest
import unittest
from smart_dns import remaining_weight, lua_record, regional_blocked

class RoutingPolicyTests(unittest.TestCase):
    def test_provider_capacity_ratio(self):
        self.assertEqual(remaining_weight(5e12, 0), 5 * remaining_weight(1e12, 0))

    def test_usage_reduces_share(self):
        self.assertLess(remaining_weight(1e12, 8e11), remaining_weight(1e12, 1e11))

    def test_quota_reserve_and_disabled_node(self):
        self.assertEqual(remaining_weight(1e12, 9.5e11), 0)
        self.assertEqual(remaining_weight(1e12, 2e12), 0)
        self.assertEqual(remaining_weight(1e12, 0, True), 0)

    def test_monthly_counter_reset_restores_weight(self):
        self.assertGreater(remaining_weight(1e12, 0), remaining_weight(1e12, 9e11))

    def test_bad_metrics_are_not_published(self):
        for used in [-1, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                remaining_weight(1e12, used)

    def test_zero_weight_nodes_do_not_enter_health_pool(self):
        record = lua_record([('198.51.100.31', 100), ('198.51.100.32', 0)])
        self.assertIn('198.51.100.31', record)
        self.assertNotIn('198.51.100.32', record)

    def test_dns_empties_when_no_capacity(self):
        self.assertEqual(lua_record([('198.51.100.31', 0)]), 'A "; return {}"')

    def test_invalid_address_is_rejected(self):
        with self.assertRaises(ValueError):
            lua_record([('injected.example', 5)])

    def test_speed_and_remaining_quota_both_affect_weight(self):
        self.assertEqual(remaining_weight(5e12, 0, speed_mbps=1000),
                         10 * remaining_weight(1e12, 0, speed_mbps=500))
        self.assertEqual(remaining_weight(1e12, 0), remaining_weight(1e12, 0, speed_mbps=1000))
        for speed in [0, -1, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                remaining_weight(1e12, 0, speed_mbps=speed)

    def test_regional_reports_need_three_fresh_required_failures(self):
        report = {'node': 'dk', 'required': True, 'ok': False, 'failures': 3, 'received': 1000}
        self.assertTrue(regional_blocked('dk', [report], now=1100))
        self.assertFalse(regional_blocked('de', [report], now=1100))
        self.assertFalse(regional_blocked('dk', [report], now=1400))
        self.assertFalse(regional_blocked('dk', [{**report, 'required': False}], now=1100))
        self.assertFalse(regional_blocked('dk', [{**report, 'failures': 2}], now=1100))
        self.assertFalse(regional_blocked('dk', [{**report, 'ok': True}], now=1100))

if __name__ == '__main__':
    unittest.main()


def test_failed_owned_https_is_excluded_from_smart_but_dns_ownership_is_retained(tmp_path, monkeypatch):
    import json
    import types
    import smart_dns as module
    inventory = [{'uuid': name, 'name': name, 'public_ip': ip, 'cover': {'domain': 'edge.kingline.online'}}
                 for name, ip in [('bad', '8.8.4.4'), ('good', '9.9.9.9')]]
    (tmp_path / 'nodes.json').write_text(json.dumps(inventory))
    (tmp_path / 'cover-policy.json').write_text(json.dumps({'domain': 'edge.kingline.online'}))
    (tmp_path / 'dns-replicas.json').write_text(json.dumps([{'role': 'core', 'public_ip': '1.1.1.1'}]))
    panel = [{'uuid': n['uuid'], 'trafficLimitBytes': 10**12, 'trafficUsedBytes': 0,
              'isTrafficTrackingActive': True, 'trafficResetDay': 19, 'isDisabled': False, 'isConnected': True}
             for n in inventory]
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'RemnaAPI', lambda *a: types.SimpleNamespace(items=lambda *a: panel))
    monkeypatch.setattr(module, 'https_cover_ready', lambda ip, domain: ip == '9.9.9.9')
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout='9.9.9.9\n'))
    writes = []
    def dns(*args):
        if len(args) == 2:
            return {'rrsets': [{'name': module.ZONE, 'type': 'SOA', 'records': [{'content': 'ns hostmaster 1 60 60 60 5'}]}]}
        writes.extend(args[3]['rrsets'])
    monkeypatch.setattr(module, 'dns_api', dns)
    report = module.synchronize()
    assert report['nodes'][0]['weight'] == 0 and not report['nodes'][0]['https_cover_ready']
    assert report['nodes'][1]['weight'] > 0
    smart = next(r for r in writes if r['type'] == 'LUA')
    assert '8.8.4.4' not in smart['records'][0]['content']
    identity = next(r for r in writes if r['type'] == 'A')
    assert {r['content'] for r in identity['records']} == {'8.8.4.4', '9.9.9.9'}


def test_broken_authenticated_tunnel_is_excluded_despite_connected_management(tmp_path, monkeypatch):
    import json
    import time
    import types
    import smart_dns as module
    from tunnel_health_policy import standard_identity
    inventory = [{'uuid': name, 'name': name, 'public_ip': ip}
                 for name, ip in [('bad', '8.8.4.4'), ('good', '9.9.9.9')]]
    state = {'sni': 'www.samsung.com'}
    (tmp_path / 'owner-profile.json').write_text(json.dumps(state))
    (tmp_path / 'nodes.json').write_text(json.dumps(inventory))
    (tmp_path / 'dns-replicas.json').write_text(json.dumps([{'role': 'core', 'public_ip': '1.1.1.1'}]))
    (tmp_path / 'tunnel-health.json').write_text(json.dumps({'identity': standard_identity(state),
        'dependencies_ready': True, 'nodes': [{'uuid': 'bad', 'ip': '8.8.4.4', 'checked_at': time.time(),
                                             'ok': False, 'failures': 2}]}))
    panel = [{'uuid': n['uuid'], 'trafficLimitBytes': 10**12, 'trafficUsedBytes': 0,
              'isTrafficTrackingActive': True, 'trafficResetDay': 19, 'isDisabled': False, 'isConnected': True}
             for n in inventory]
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'RemnaAPI', lambda *a: types.SimpleNamespace(items=lambda *a: panel))
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout='9.9.9.9\n'))
    writes = []
    monkeypatch.setattr(module, 'ensure_zone', lambda *a: {'rrsets': [
        {'name': module.ZONE, 'type': 'SOA', 'records': [{'content': 'ns hostmaster 1 60 60 60 5'}]}]})
    monkeypatch.setattr(module, 'dns_api', lambda *args: writes.extend(args[3]['rrsets']))
    data = module.synchronize()
    assert data['nodes'][0]['weight'] == 0 and data['nodes'][0]['vpn_probe_excluded']
    assert data['nodes'][1]['weight'] > 0
    assert '8.8.4.4' not in writes[0]['records'][0]['content']
    assert json.loads((tmp_path / 'nodes.json').read_text()) == inventory


@pytest.mark.parametrize('disable',[{'smart_enabled':False},{'hidden':True},{'effective_hidden':True}])
def test_unpublished_routes_never_enter_dns_and_preserve_inventory(tmp_path, monkeypatch,disable):
    import json
    import types
    import smart_dns as module
    inventory=[{'uuid':name,'name':name,'public_ip':ip,**extra} for name,ip,extra in (
        ('manual','8.8.4.4',disable),('legacy','9.9.9.9',{}))]
    (tmp_path/'nodes.json').write_text(json.dumps(inventory))
    (tmp_path/'dns-replicas.json').write_text(json.dumps([{'role':'core','public_ip':'1.1.1.1'}]))
    panel=[{'uuid':n['uuid'],'trafficLimitBytes':10**12,'trafficUsedBytes':0,'isTrafficTrackingActive':True,
            'trafficResetDay':19,'isDisabled':False,'isConnected':True} for n in inventory]
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module,'RemnaAPI',lambda *a:types.SimpleNamespace(items=lambda *a:panel))
    monkeypatch.setattr(module,'ensure_zone',lambda *a:{'rrsets':[{'name':module.ZONE,'type':'SOA','records':[{'content':'ns hostmaster 1 60 60 60 5'}]}]})
    writes=[]
    monkeypatch.setattr(module,'dns_api',lambda *a:writes.extend(a[3]['rrsets']))
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:types.SimpleNamespace(returncode=0,stdout='9.9.9.9\n'))
    report=module.synchronize()
    assert report['nodes'][0]['weight']==0 and report['nodes'][0]['smart_enabled']==disable.get('smart_enabled',True)
    assert report['nodes'][1]['weight']>0 and report['nodes'][1]['smart_enabled'] is True
    assert '8.8.4.4' not in writes[0]['records'][0]['content']
    assert json.loads((tmp_path/'nodes.json').read_text())==inventory
    inventory[1]['smart_enabled']=False
    (tmp_path/'nodes.json').write_text(json.dumps(inventory));writes.clear()
    report=module.synchronize()
    assert all(n['weight']==0 for n in report['nodes'])
    assert writes[0]['records'][0]['content']=='A "; return {}"'


def test_chain_exclusion_and_physical_gateway_usage_are_used_by_dns(tmp_path,monkeypatch):
    import json,time,types
    import smart_dns as module
    inventory=[{'uuid':name,'name':name,'public_ip':ip,**extra} for name,ip,extra in (
        ('chain','8.8.4.4',{'network_auto_eligible':False}),
        ('gateway','8.8.8.8',{'relay_accounted':True}),('direct','9.9.9.9',{}))]
    (tmp_path/'nodes.json').write_text(json.dumps(inventory))
    (tmp_path/'dns-replicas.json').write_text(json.dumps([{'role':'core','public_ip':'1.1.1.1'}]))
    usage={'gateway':{'used_bytes':10**12,'reset_day':19,'updated_at':time.time(),'period_until':time.time()+86400}}
    (tmp_path/'relay-usage.json').write_text(json.dumps(usage))
    panel=[{'uuid':n['uuid'],'trafficLimitBytes':10**12,'trafficUsedBytes':100,'isTrafficTrackingActive':True,
            'trafficResetDay':19,'isDisabled':False,'isConnected':True} for n in inventory]
    monkeypatch.setattr(module,'ROOT',tmp_path)
    monkeypatch.setattr(module,'RemnaAPI',lambda *a:types.SimpleNamespace(items=lambda *a:panel))
    monkeypatch.setattr(module,'ensure_zone',lambda *a:{'rrsets':[{'name':module.ZONE,'type':'SOA','records':[{'content':'ns hostmaster 1 60 60 60 5'}]}]})
    monkeypatch.setattr(module,'dns_api',lambda *a:None)
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:types.SimpleNamespace(returncode=0,stdout='9.9.9.9\n'))
    report=module.synchronize()
    assert report['nodes'][0]['weight']==0 and not report['nodes'][0]['network_auto_eligible']
    assert report['nodes'][1]['weight']==0 and report['nodes'][1]['used_bytes']==10**12
    assert report['nodes'][2]['weight']>0
    usage['gateway'].update(used_bytes=100,updated_at=time.time()-1000)
    (tmp_path/'relay-usage.json').write_text(json.dumps(usage))
    report=module.synchronize()
    assert not report['nodes'][1]['relay_accounting_ready'] and report['nodes'][1]['weight']==0
