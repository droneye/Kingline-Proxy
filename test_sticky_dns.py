import base64
import json
import sqlite3
import pytest
from sticky_dns import StickyDNS,preferred_record
from subscription_gateway import transform,device_headers
from fastapi.testclient import TestClient
import urllib.error
import io
import subscription_gateway as gateway

def test_pin_survives_weight_changes_and_restart(tmp_path):
    store=StickyDNS(tmp_path);users=[{'id':7,'shortUuid':'private-subscription-token'}]
    first=store.reconcile(users,[('192.0.2.1',5),('192.0.2.2',1)],now=100)
    lease=first['leases'][0];other='192.0.2.2' if lease['ip']=='192.0.2.1' else '192.0.2.1'
    second=StickyDNS(tmp_path).reconcile(users,[(lease['ip'],1),(other,100000)],now=86000)
    assert second['leases'][0]['ip']==lease['ip']
    assert second['subscriptions']==first['subscriptions']
    assert 'private-subscription-token' not in lease['name']
    third=store.reconcile(users,[(lease['ip'],1),(other,100000)],now=86501)
    assert third['leases'][0]['ip']==other

def test_quota_failure_can_move_early_and_empty_pool_fails_closed(tmp_path):
    s=StickyDNS(tmp_path);users=[{'id':1,'shortUuid':'secret'}]
    first=s.reconcile(users,[('192.0.2.1',1)],now=1)
    second=s.reconcile(users,[('192.0.2.1',0),('192.0.2.2',1)],now=2)
    assert second['leases'][0]['ip']=='192.0.2.2'
    assert second['leases'][0]['reason']=='unavailable_or_quota'
    empty=s.reconcile(users,[],now=3)
    assert not empty['leases'][0]['eligible']
    assert preferred_record(None,[])=='A "; return {}"'

def test_distribution_weighted_but_stable(tmp_path):
    s=StickyDNS(tmp_path);users=[{'id':i,'shortUuid':str(i)} for i in range(10000)]
    r=s.reconcile(users,[('192.0.2.1',5),('192.0.2.2',1)],now=1)
    ratio=sum(l['ip']=='192.0.2.1' for l in r['leases'])/len(users)
    assert .80<ratio<.87
    assert len({l['name'] for l in r['leases']})==len(users)

def test_native_health_fallback_has_order_without_random():
    record=preferred_record('192.0.2.1',['192.0.2.2'])
    assert record.index('192.0.2.1')<record.index('192.0.2.2')
    assert 'minimumFailures=2' in record and 'pickwrandom' not in record

def test_transform_all_client_formats_and_preserve_manual_keys():
    raw=b'vless://uuid@auto.edge.kingline.online:443?security=reality#Auto\nvless://uuid@192.0.2.1:443#DE'
    name='u-opaque.auto.edge.kingline.online'
    converted=transform(raw,name)
    assert b'@192.0.2.1:443#DE' in converted and name.encode() in converted
    assert base64.b64decode(transform(base64.b64encode(raw)+b'\n',name))==converted
    for payload in [b'{"server":"auto.edge.kingline.online"}',b'server: auto.edge.kingline.online']:
        assert name.encode() in transform(payload,name)

def test_explicit_desktop_link_uses_one_stable_slot_without_overriding_phone():
    a=device_headers({'user-agent':'v2rayN'},'subscription','WorkPC-123456789',b'secret')
    b=device_headers({'user-agent':'v2rayN'},'subscription','WorkPC-123456789',b'secret')
    c=device_headers({'user-agent':'v2rayN'},'subscription','OtherPC-123456789',b'secret')
    assert a['x-hwid']==b['x-hwid'] and a['x-hwid']!=c['x-hwid']
    assert 'subscription' not in a['x-hwid']
    assert device_headers({'x-hwid':'real-phone'},'s','WorkPC-123456789',b'secret')['x-hwid']=='real-phone'
    with pytest.raises(ValueError):device_headers({},'s','bad',b'secret')

def test_http_gateway_keeps_fixed_upstream_device_slot_and_hwid_errors(tmp_path,monkeypatch):
    (tmp_path/'sticky-dns.key').write_bytes(b'secret')
    (tmp_path/'sticky-subscriptions.json').write_text(json.dumps({'subscriptions':{'opaque-subscription':'u-private.auto.edge.kingline.online'}}))
    monkeypatch.setattr(gateway,'ROOT',tmp_path)
    seen=[]
    class Upstream:
        status=200
        headers={'Content-Type':'text/plain','X-Hwid-Active':'true','Set-Cookie':'must-not-leak'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return base64.b64encode(b'vless://fake@auto.edge.kingline.online:443#Auto')
    class Opener:
        def open(self,request,timeout):seen.append(request);return Upstream()
    monkeypatch.setattr(gateway,'OPENER',Opener())
    client=TestClient(gateway.app)
    response=client.get('/opaque-subscription?device=WorkPC-123456789&format=singbox',headers={'User-Agent':'v2rayN','X-Real-IP':'192.0.2.1'})
    assert response.status_code==200
    assert b'u-private.auto.edge.kingline.online' in base64.b64decode(response.content)
    assert seen[0].full_url=='http://127.0.0.1:3010/opaque-subscription?format=singbox'
    assert seen[0].get_header('X-hwid').startswith('Kingline-')
    assert seen[0].get_header('X-forwarded-for')=='192.0.2.1'
    assert response.headers['cache-control']=='no-store' and 'set-cookie' not in response.headers
    assert client.get('/opaque-subscription?device=bad').status_code==400
    assert len(seen)==1
    class Limit:
        def open(self,request,timeout):raise urllib.error.HTTPError(request.full_url,403,'Device limit',{'X-Hwid-Limit':'true'},io.BytesIO(b''))
    monkeypatch.setattr(gateway,'OPENER',Limit())
    blocked=client.get('/opaque-subscription')
    assert blocked.status_code==403 and blocked.headers['x-hwid-limit']=='true'
