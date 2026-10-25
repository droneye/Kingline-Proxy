import base64
from contextlib import closing
from email.message import Message
import io
import json
import sqlite3
import urllib.error

from fastapi.testclient import TestClient
import pytest

import subscription_gateway as gateway
from subscription_modes import profile_title

TOKEN='native-client-111'
OTHER='native-client-222'
RAW=b'vless://11111111-1111-4111-8111-111111111111@node.example.com:443?type=tcp&security=reality&sni=edge.example.com&pbk=public&sid=aaaa&flow=xtls-rprx-vision#Location'


@pytest.fixture
def title_db(tmp_path,monkeypatch):
    db_path=tmp_path/'business.db'
    with closing(sqlite3.connect(db_path)) as db,db:
        db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE users(tg_id INTEGER PRIMARY KEY,sub_id TEXT,tg_name TEXT);
            CREATE TABLE remna_links(short_uuid TEXT,sub_id TEXT);
            CREATE TABLE web_accounts(id INTEGER PRIMARY KEY,name TEXT,linked_tg_id INTEGER,web_tg_id INTEGER);
            INSERT INTO users VALUES(100,'first','Имя из Telegram'),(200,'second','Борис'),(-300,'old','Старое имя');
            INSERT INTO web_accounts VALUES(1,'Анна',100,-300);
        ''')
        db.executemany('INSERT INTO remna_links VALUES(?,?)',[(TOKEN,'first'),(OTHER,'second'),('old-native-token','old')])
    cfg={'business_db':str(db_path),'path':'/channels/fixture12345/','sni':'edge.example.com','aliases':{'owner-legacy-token':100}}
    (tmp_path/'transport-policy.json').write_text(json.dumps(cfg))
    (tmp_path/'sticky-dns.key').write_bytes(b'fixture')
    monkeypatch.setattr(gateway,'ROOT',tmp_path)
    return tmp_path,db_path,cfg


def decode(value):return base64.b64decode(value.removeprefix('base64:')).decode()


def test_names_are_scoped_to_subscription_with_web_then_telegram_priority(title_db):
    root,_,_=title_db
    assert decode(profile_title(root,TOKEN))=='Kingline VPN · Анна'
    assert decode(profile_title(root,OTHER))=='Kingline VPN · Борис'
    assert profile_title(root,'not-a-subscription') is None
    assert decode(profile_title(root,'owner-legacy-token'))=='Kingline VPN · Анна'
    assert decode(profile_title(root,'old-native-token'))=='Kingline VPN · Старое имя'


def test_empty_web_name_falls_back_to_telegram_and_missing_names_never_expose_email(title_db):
    root,path,_=title_db
    with closing(sqlite3.connect(path)) as db,db:db.execute('UPDATE web_accounts SET name=""')
    assert decode(profile_title(root,TOKEN))=='Kingline VPN · Имя из Telegram'
    with closing(sqlite3.connect(path)) as db,db:db.execute('UPDATE users SET tg_name="private@example.invalid" WHERE tg_id=100')
    assert decode(profile_title(root,TOKEN))=='Kingline VPN'


def test_name_normalization_caps_length_and_removes_bidi_and_control_characters(title_db):
    root,path,_=title_db
    with closing(sqlite3.connect(path)) as db,db:db.execute('UPDATE web_accounts SET name=?',('  Анна\r\nПетрова\x00\u202e  👩\u200d💻  ',))
    assert decode(profile_title(root,TOKEN))=='Kingline VPN · Анна Петрова 👩\u200d💻'
    with closing(sqlite3.connect(path)) as db,db:db.execute('UPDATE web_accounts SET name=?',('Я'*200,))
    assert decode(profile_title(root,TOKEN))=='Kingline VPN · '+'Я'*80


@pytest.mark.parametrize('mode',['raw','xhttp'])
@pytest.mark.parametrize('method',['get','head'])
def test_profile_title_metadata_works_for_both_transports_without_changing_keys(title_db,monkeypatch,mode,method):
    _,_,cfg=title_db
    monkeypatch.setattr(gateway,'selected',lambda *a:(mode,cfg))
    class Upstream:
        status=200
        headers=Message()
        headers['Content-Type']='text/plain'
        headers['Profile-Title']='Old native title'
        headers['X-Hwid-Active']='true'
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,limit):return b'' if method=='head' else RAW
    class Opener:
        def open(self,*a,**k):return Upstream()
    monkeypatch.setattr(gateway,'OPENER',Opener())
    client=TestClient(gateway.app)
    first=getattr(client,method)('/'+TOKEN,headers={'X-Hwid':'same-device'})
    second=getattr(client,method)('/'+OTHER,headers={'X-Hwid':'same-device'})
    assert first.status_code==second.status_code==200
    assert decode(first.headers['profile-title'])=='Kingline VPN · Анна'
    assert decode(second.headers['profile-title'])=='Kingline VPN · Борис'
    assert first.content==second.content
    assert first.headers['x-hwid-active']=='true'
    if mode=='raw' and method=='get':assert first.content==RAW
    if method=='head':assert first.content==b''


@pytest.mark.parametrize('variant',['hwid','html','error','unavailable'])
def test_title_is_optional_and_never_added_to_refusal_or_html(title_db,monkeypatch,caplog,variant):
    _,_,cfg=title_db
    monkeypatch.setattr(gateway,'selected',lambda *a:('raw',cfg))
    class Upstream:
        status=200
        headers=Message()
        headers['Content-Type']='text/html' if variant=='html' else 'text/plain'
        headers['Profile-Title']='Native'
        if variant=='hwid':headers['X-Hwid-Limit']='true'
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self,limit):return b'<html>page</html>' if variant=='html' else b'' if variant=='hwid' else RAW
    class Opener:
        def open(self,request,**k):
            if variant=='error':raise urllib.error.HTTPError(request.full_url,403,'Limit',Upstream.headers,io.BytesIO(b'no access'))
            return Upstream()
    monkeypatch.setattr(gateway,'OPENER',Opener())
    def broken(*a):raise sqlite3.OperationalError('private-name-MUST_NOT_LOG')
    monkeypatch.setattr(gateway,'profile_title',broken)
    response=TestClient(gateway.app).get('/'+TOKEN)
    assert response.headers['profile-title']=='Native'
    assert response.status_code==(403 if variant=='error' else 200)
    assert ('subscription_title_unavailable' in caplog.text)==(variant=='unavailable')
    assert all(secret not in caplog.text for secret in ('private-name-MUST_NOT_LOG',TOKEN,'Анна'))
