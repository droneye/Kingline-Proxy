import base64
from email.message import Message
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest
import subscription_gateway as gateway
from subscription_modes import announcement

RAW=b'vless://11111111-1111-4111-8111-111111111111@node.example.com:443?type=tcp&security=reality&sni=edge.example.com&pbk=public&sid=aaaa&flow=xtls-rprx-vision#Location'


@pytest.fixture
def notice(tmp_path,monkeypatch):
    file=tmp_path/'business.db'
    with sqlite3.connect(file) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE subscription_announcement(singleton INTEGER PRIMARY KEY,text TEXT,updated_at TEXT,revision INTEGER)')
    cfg={'business_db':str(file),'path':'/channels/fixture12345/','sni':'edge.example.com'}
    (tmp_path/'transport-policy.json').write_text(json.dumps(cfg))
    (tmp_path/'sticky-dns.key').write_bytes(b'fixture')
    monkeypatch.setattr(gateway,'ROOT',tmp_path)
    def save(text):
        with sqlite3.connect(file) as db:db.execute('INSERT OR REPLACE INTO subscription_announcement VALUES(1,?,"fixture",1)',(text,))
    return tmp_path,cfg,save


@pytest.mark.parametrize('mode',['raw','xhttp'])
@pytest.mark.parametrize('encoded',[False,True])
@pytest.mark.parametrize('method',['get','head'])
def test_banner_is_metadata_only_for_both_transports_and_head(notice,monkeypatch,mode,encoded,method):
    root,cfg,save=notice
    save('Новости 🇩🇰\nНовая локация')
    monkeypatch.setattr(gateway,'selected',lambda *a:(mode,cfg))
    class Upstream:
        status=200
        headers=Message()
        headers['Content-Type']='text/plain'
        headers['Announce']='old'
        headers['Announce-Url']='https://old.example.invalid'
        headers['X-Hwid-Active']='true'
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,limit):return b'' if method=='head' else base64.b64encode(RAW) if encoded else RAW
    class Opener:
        def open(self,*a,**k):return Upstream()
    monkeypatch.setattr(gateway,'OPENER',Opener())
    response=getattr(TestClient(gateway.app),method)('/opaque-token',headers={'X-Hwid':'fixture'})
    assert response.status_code==200 and response.headers['x-hwid-active']=='true'
    assert base64.b64decode(response.headers['announce'].removeprefix('base64:')).decode()=='Новости 🇩🇰\nНовая локация'
    assert 'announce-url' not in response.headers
    if method=='head':assert response.content==b''
    elif mode=='raw':assert response.content==(base64.b64encode(RAW) if encoded else RAW)
    else:assert b'type=xhttp' in (base64.b64decode(response.content) if encoded else response.content)
    assert announcement(root).startswith('base64:')


def test_unmanaged_preserves_defaults_and_clear_is_explicit(notice):
    root,_,save=notice
    assert announcement(root) is None
    save('test');assert announcement(root)=='base64:dGVzdA=='
    save('');assert announcement(root)==''
    save(' \n\t ');assert announcement(root)==''


@pytest.mark.parametrize('mode',['raw','xhttp'])
@pytest.mark.parametrize('method',['get','head'])
def test_removed_banner_omits_both_headers_without_changing_profiles(notice,monkeypatch,mode,method):
    _,cfg,save=notice
    monkeypatch.setattr(gateway,'selected',lambda *a:(mode,cfg))
    class Upstream:
        status=200
        headers=Message()
        headers['Content-Type']='text/plain'
        headers['Announce']='native-default'
        headers['Announce-Url']='https://native.example.invalid'
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,limit):return b'' if method=='head' else RAW
    class Opener:
        def open(self,*a,**k):return Upstream()
    monkeypatch.setattr(gateway,'OPENER',Opener())
    client=TestClient(gateway.app)
    save('A real announcement')
    visible=getattr(client,method)('/opaque-token')
    assert 'announce' in visible.headers
    save('')
    hidden=getattr(client,method)('/opaque-token')
    assert hidden.status_code==200 and hidden.content==visible.content
    assert 'announce' not in hidden.headers and 'announce-url' not in hidden.headers


@pytest.mark.parametrize('variant',['html','hwid','unavailable'])
def test_optional_notice_never_breaks_profiles_or_denials(notice,monkeypatch,caplog,variant):
    _,cfg,save=notice
    save('new')
    monkeypatch.setattr(gateway,'selected',lambda *a:('raw',cfg))
    class Upstream:
        status=200
        headers=Message()
        headers['Content-Type']='text/html' if variant=='html' else 'text/plain'
        headers['Announce']='native'
        if variant=='hwid':headers['X-Hwid-Limit']='true'
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,limit):return b'<html>page</html>' if variant=='html' else b'' if variant=='hwid' else RAW
    class Opener:
        def open(self,*a,**k):return Upstream()
    monkeypatch.setattr(gateway,'OPENER',Opener())
    def broken(*a):raise sqlite3.OperationalError('private-text-MUST_NOT_LOG')
    monkeypatch.setattr(gateway,'announcement',broken)
    response=TestClient(gateway.app).get('/private-token')
    assert response.status_code==200 and response.headers['announce']=='native'
    assert response.content==Upstream().read(10000)
    assert 'private-text-MUST_NOT_LOG' not in caplog.text and 'private-token' not in caplog.text
    assert ('subscription_announcement_unavailable' in caplog.text)==(variant=='unavailable')
