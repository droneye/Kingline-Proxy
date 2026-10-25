import base64,json,sqlite3
from urllib.parse import urlsplit,parse_qs
import pytest
from subscription_modes import convert,selected
from node_manager.xhttp import add_inbound

CFG={'path':'/channels/fixture12345/','sni':'edge.example.com'}
RAW=b'vless://11111111-1111-4111-8111-111111111111@node.example.com:443?type=tcp&security=reality&sni=edge.example.com&pbk=public&sid=aaaa&flow=xtls-rprx-vision#Location'

@pytest.mark.parametrize('encoded',[True,False])
def test_xhttp_keeps_identity_dns_order_and_uses_valid_tls(encoded):
    result=convert(base64.b64encode(RAW) if encoded else RAW,CFG)
    text=base64.b64decode(result) if encoded else result
    uri=urlsplit(text.decode().strip());params=parse_qs(uri.query)
    assert uri.netloc==urlsplit(RAW.decode()).netloc
    assert params['type']==['xhttp'] and params['security']==['tls'] and params['mode']==['auto']
    assert params['path']==[CFG['path']] and params['alpn']==['h2']
    assert all(k not in params for k in ['pbk','sid','flow','allowInsecure'])


def test_unknown_formats_and_custom_sni_fail_closed():
    for data in [b'{"outbounds":[]}',RAW.replace(b'edge.example.com',b'foreign.example.com'),b'<html>']:
        with pytest.raises((ValueError,UnicodeError)):convert(data,CFG)


def test_policy_identity_mapping_and_raw_default(tmp_path):
    assert selected(tmp_path,'unknown')[0]=='raw'
    file=tmp_path/'business.db'
    with sqlite3.connect(file) as c:
        c.executescript("CREATE TABLE users(tg_id INTEGER,sub_id TEXT);CREATE TABLE remna_links(short_uuid TEXT,sub_id TEXT);CREATE TABLE vpn_preferences(tg_id INTEGER,transport TEXT);INSERT INTO users VALUES(123,'sub');INSERT INTO remna_links VALUES('native-token','sub');INSERT INTO vpn_preferences VALUES(123,'xhttp');")
    (tmp_path/'transport-policy.json').write_text(json.dumps({'enabled':True,'business_db':str(file),'aliases':{'legacy-native':123}}))
    assert selected(tmp_path,'native-token')[0]=='xhttp'
    assert selected(tmp_path,'legacy-native')[0]=='xhttp'
    assert selected(tmp_path,'another-account')[0]=='raw'


def test_live_wal_preferences_after_connection_closes_and_reopens(tmp_path):
    from contextlib import closing
    file=tmp_path/'business.db'
    with closing(sqlite3.connect(file)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript("CREATE TABLE users(tg_id INTEGER,sub_id TEXT);CREATE TABLE remna_links(short_uuid TEXT,sub_id TEXT);CREATE TABLE vpn_preferences(tg_id INTEGER,transport TEXT);INSERT INTO users VALUES(123,'sub');INSERT INTO remna_links VALUES('native-token','sub');INSERT INTO vpn_preferences VALUES(123,'raw');")
    assert not (tmp_path/'business.db-wal').exists()
    (tmp_path/'transport-policy.json').write_text(json.dumps({'enabled':True,'business_db':str(file)}))
    assert selected(tmp_path,'native-token')[0]=='raw'
    with closing(sqlite3.connect(file)) as db:
        db.execute("UPDATE vpn_preferences SET transport='xhttp'");db.commit()
        assert selected(tmp_path,'native-token')[0]=='xhttp'
    assert not (tmp_path/'business.db-wal').exists()
    assert selected(tmp_path,'native-token')[0]=='xhttp'


def test_second_listener_never_changes_raw_or_publishes_plain_vless():
    config={'inbounds':[{'tag':'RAW','port':443,'settings':{'flow':'xtls-rprx-vision'}}],'routing':{'rules':[{'domain':['domain:ru'],'balancerTag':'ru'}]}}
    after=add_inbound(config,CFG['path'])
    assert after['inbounds'][0]==config['inbounds'][0] and after['routing']==config['routing']
    new=after['inbounds'][1]
    assert new['listen']=='127.0.0.1' and new['streamSettings']['security']=='none'
    assert 'flow' not in new['settings']
    assert add_inbound(after,CFG['path'])==after


def test_gateway_picks_mode_from_account_not_spoofed_headers(tmp_path,monkeypatch):
    import subscription_gateway as gateway
    from fastapi.testclient import TestClient
    (tmp_path/'sticky-dns.key').write_bytes(b'fixture-key')
    (tmp_path/'sticky-subscriptions.json').write_text(json.dumps({'subscriptions':{'opaque-subscription':'u-fixture.auto.edge.kingline.online'}}))
    monkeypatch.setattr(gateway,'ROOT',tmp_path)
    mode=['raw'];seen=[]
    monkeypatch.setattr(gateway,'selected',lambda root,short:(mode[0],CFG))
    class Upstream:
        status=200;headers={'Content-Type':'text/plain','X-Hwid-Active':'true'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return base64.b64encode(RAW)
    class Opener:
        def open(self,request,timeout):seen.append(request);return Upstream()
    monkeypatch.setattr(gateway,'OPENER',Opener())
    c=TestClient(gateway.app)
    raw=c.get('/opaque-subscription',headers={'X-Kingline-Transport':'XHTTP','X-Hwid':'same-device'})
    assert base64.b64decode(raw.content)==RAW
    assert seen[-1].get_header('X-kingline-transport') is None
    mode[0]='xhttp'
    xhttp=c.get('/opaque-subscription',headers={'X-Hwid':'same-device'})
    assert parse_qs(urlsplit(base64.b64decode(xhttp.content).decode().strip()).query)['type']==['xhttp']
    assert seen[-1].get_header('X-kingline-transport')=='XHTTP'
    assert seen[-1].get_header('X-hwid')=='same-device'
    assert xhttp.headers['x-hwid-active']=='true'


def test_xhttp_head_and_native_hwid_refusal_are_not_converted(tmp_path,monkeypatch):
    import subscription_gateway as gateway
    from fastapi.testclient import TestClient
    import io,urllib.error
    (tmp_path/'sticky-dns.key').write_bytes(b'fixture-key')
    monkeypatch.setattr(gateway,'ROOT',tmp_path)
    monkeypatch.setattr(gateway,'selected',lambda *args:('xhttp',CFG))
    class Empty:
        status=200;headers={'Content-Type':'text/plain','X-Hwid-Active':'true'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return b''
    class Opener:
        def open(self,request,timeout):return Empty()
    monkeypatch.setattr(gateway,'OPENER',Opener());client=TestClient(gateway.app)
    response=client.head('/native-subscription-token')
    assert response.status_code==200 and response.content==b''
    class Refused:
        def open(self,request,timeout):raise urllib.error.HTTPError(request.full_url,403,'Limit',{'X-Hwid-Limit':'true'},io.BytesIO(b'limit'))
    monkeypatch.setattr(gateway,'OPENER',Refused())
    refused=client.get('/native-subscription-token')
    assert refused.status_code==403 and refused.content==b'limit' and refused.headers['x-hwid-limit']=='true'


@pytest.mark.parametrize('flag',['X-Hwid-Not-Supported','X-Hwid-Max-Devices-Reached','X-Hwid-Limit'])
@pytest.mark.parametrize('body',[b'',b'device limit reached'])
def test_native_200_hwid_refusal_is_preserved(tmp_path,monkeypatch,flag,body):
    import subscription_gateway as gateway
    from fastapi.testclient import TestClient
    from email.message import Message
    (tmp_path/'sticky-dns.key').write_bytes(b'fixture-key')
    monkeypatch.setattr(gateway,'ROOT',tmp_path)
    monkeypatch.setattr(gateway,'selected',lambda *args:('xhttp',CFG))
    class Upstream:
        status=200
        headers=Message()
        headers['Content-Type']='text/plain'
        headers[flag]='true'
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):return body
    class Opener:
        def open(self,*args,**kwargs):return Upstream()
    monkeypatch.setattr(gateway,'OPENER',Opener())
    response=TestClient(gateway.app).get('/native-subscription-token')
    assert response.status_code==200 and response.content==body
    assert response.headers[flag]=='true'


def test_failure_diagnostics_never_include_secrets(tmp_path,monkeypatch,caplog):
    import subscription_gateway as gateway
    from fastapi.testclient import TestClient
    (tmp_path/'sticky-dns.key').write_bytes(b'fixture-key')
    monkeypatch.setattr(gateway,'ROOT',tmp_path)
    monkeypatch.setattr(gateway,'selected',lambda *args:('xhttp',CFG))
    class Opener:
        def open(self,*args,**kwargs):raise OSError('private token MUST_NOT_LOG')
    monkeypatch.setattr(gateway,'OPENER',Opener())
    response=TestClient(gateway.app).get('/private-subscription-token',headers={'X-Hwid':'private-device-identifier'})
    assert response.status_code==503 and response.headers['cache-control']=='no-store'
    assert 'reason=upstream_unavailable' in caplog.text
    assert all(secret not in caplog.text for secret in ['MUST_NOT_LOG','private-subscription-token','private-device-identifier'])
