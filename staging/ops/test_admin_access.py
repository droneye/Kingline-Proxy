"""Delegation boundaries against a disposable database, without network or real admins."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import timedelta

fixture=tempfile.TemporaryDirectory()
for key,name in (('DB_PATH','business.db'),('KINGLINE_DB_PATH','business.db'),('PROMOS_DB_PATH','promos.db'),('REFERRAL_DB_PATH','ref.db')):
    os.environ[key]=str(Path(fixture.name)/name)
os.environ['KINGLINE_OWNER_EMAIL']='owner@example.invalid'
os.environ['KINGLINE_ADMIN_EMAILS']=''
sys.path.insert(0,'/app/site/backend')
sys.path.insert(0,'/app/bot')
# Simulate an existing admin table from before infrastructure permissions existed.
with sqlite3.connect(os.environ['DB_PATH']) as c:
    c.execute('CREATE TABLE web_admins(id INTEGER PRIMARY KEY,email TEXT UNIQUE,tg_id INTEGER UNIQUE,is_owner INTEGER DEFAULT 0,created_at TEXT,created_by_account_id INTEGER)')
    c.execute("INSERT INTO web_admins(email) VALUES ('existing@example.invalid')")
import server
import infrastructure
server.init_db()

class PermissionTests(unittest.TestCase):
    def setUp(self):
        with server.connect() as c:
            for email in ('owner@example.invalid','delegate@example.invalid','regular@example.invalid'):
                c.execute('INSERT OR IGNORE INTO web_accounts(email,password_hash,email_verified,web_tg_id) VALUES (?, ?, 1, ?)',(email,'test-only',-1000-len(email)))
            c.execute("INSERT OR IGNORE INTO web_admins(email) VALUES ('delegate@example.invalid')")
            c.execute("UPDATE web_admins SET can_nodes=0,can_technical=0,can_node_credentials=0,can_vpn_config=0,can_documentation=0,can_user_transport=0,can_broadcast=0 WHERE email='delegate@example.invalid'")
            import announcements
            announcements.schema(c)
            c.execute('DELETE FROM subscription_announcement')
            c.execute('DELETE FROM web_sessions')
        self.request_calls=[]

    def handler(self,email='delegate@example.invalid',verified=True,path='nodes',body=None):
        with server.connect() as c:
            account=c.execute('SELECT * FROM web_accounts WHERE email=?',(email,)).fetchone()
            token='fixture-'+email
            c.execute('INSERT OR REPLACE INTO web_sessions(token_hash,account_id,expires_at,admin_verified_at) VALUES (?,?,?,?)',(server.hash_token(token),account['id'],(server.utc_now()+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),server.iso_now() if verified else None))
        h=object.__new__(server.Handler)
        h.path='/api/admin/infrastructure/'+path
        h.headers={'Content-Length':str(len(json.dumps(body or {})))}
        h.session_token=lambda: token
        h.json_response=Mock()
        h.validate_post_request=Mock(return_value=True)
        h.read_json=Mock(return_value=body or {})
        h.client_ip=lambda:'127.0.0.1'
        return h

    def grant(self,**permissions):
        with server.connect() as c:
            c.execute('UPDATE web_admins SET '+','.join('can_'+k+'=?' for k in permissions)+" WHERE email='delegate@example.invalid'",tuple(int(v) for v in permissions.values()))

    def dispatch(self,h,method='GET'):
        response=Mock(status_code=200);response.json.return_value={'ok':True}
        with patch.object(infrastructure.httpx,'Client') as client:
            client.return_value.__enter__.return_value.request.return_value=response
            infrastructure.handle(h,method)
            called=client.return_value.__enter__.return_value.request.called
        return called

    def test_existing_and_new_admin_permissions_default_to_denied(self):
        with server.connect() as c:
            for email in ('existing@example.invalid','delegate@example.invalid'):
                row=c.execute('SELECT * FROM web_admins WHERE email=?',(email,)).fetchone()
                access=server.public_admin(row)['permissions']
                for key in ('nodes','technical','node_credentials','vpn_config','documentation','user_transport'):self.assertFalse(access[key])
        missing=sqlite3.connect(':memory:');missing.row_factory=sqlite3.Row
        row=missing.execute("SELECT 1 id,'' email,NULL tg_id,0 is_owner,NULL created_at,NULL created_by_account_id").fetchone()
        self.assertFalse(server.public_admin(row)['permissions']['nodes'])
        self.assertFalse(server.public_admin(row)['permissions']['vpn_config'])
        self.assertFalse(server.public_admin(row)['permissions']['documentation'])
        self.assertFalse(server.public_admin(row)['permissions']['user_transport'])
        missing.close()

    def test_nodes_grant_and_revoke_cover_read_and_mutation(self):
        self.grant(nodes=True)
        for path,method in (('nodes','GET'),('job?id=11111111-1111-4111-a111-111111111111','GET'),('add','POST'),('update','POST'),('remove','POST'),('retry','POST'),('ru-egress','GET'),('ru-replace','POST'),('ru-retry','POST'),('node-reviews','GET'),('review-check','POST'),('node-visibility','POST'),('review-action','POST'),('installation-action','POST')):
            self.assertTrue(self.dispatch(self.handler(path=path),method))
        self.grant(nodes=False)
        for path,method in (('nodes','GET'),('add','POST'),('update','POST'),('installation-action','POST')):
            h=self.handler(path=path);self.assertFalse(self.dispatch(h,method));self.assertEqual(h.json_response.call_args.args[1],403)

    def test_candidate_review_requires_2fa_and_csrf(self):
        self.grant(nodes=True)
        h=self.handler(path='installation-action',verified=False)
        self.assertFalse(self.dispatch(h,'POST'))
        self.assertEqual(h.json_response.call_args.args[1],403)
        h=self.handler(path='installation-action')
        h.validate_post_request=Mock(return_value=False)
        self.assertFalse(self.dispatch(h,'POST'))

    def test_documentation_has_independent_permission_and_export_checks_2fa(self):
        import documentation
        from io import BytesIO
        file=Path(fixture.name)/'manual.md';file.write_text('# Manual\n\nSafe service documentation')
        def request(path='/api/admin/documentation',email='delegate@example.invalid',verified=True):
            h=self.handler(email=email,verified=verified);h.path=path;h.wfile=BytesIO();h.send_response=Mock();h.send_header=Mock();h.end_headers=Mock();return h
        with patch.dict(os.environ,{'KINGLINE_DOCUMENTATION_PATH':str(file)}),patch.object(documentation,'runtime_context',return_value={'status':'available','nodes':[]}):
            self.grant(nodes=True,technical=True)
            h=request();documentation.handle(h);self.assertEqual(h.json_response.call_args.args[1],403)
            self.grant(nodes=False,technical=False,documentation=True)
            h=request();documentation.handle(h);self.assertEqual(h.json_response.call_args.args[0]['content'],file.read_text())
            h=request('/api/admin/documentation/export');documentation.handle(h);self.assertIn(b'Safe service documentation',h.wfile.getvalue());h.send_response.assert_called_with(200)
            h=request('/api/admin/documentation/export',verified=False);documentation.handle(h);self.assertEqual(h.json_response.call_args.args[1],403);self.assertFalse(h.wfile.getvalue())
            self.grant(documentation=False)
            h=request('/api/admin/documentation/export');documentation.handle(h);self.assertEqual(h.json_response.call_args.args[1],403)
            h=request(email='regular@example.invalid');documentation.handle(h);self.assertEqual(h.json_response.call_args.args[1],403)

    def mode_handler(self,body=None,**kwargs):
        os.environ.setdefault('TELEMETRY_DB_PATH',str(Path(fixture.name)/'telemetry.db'))
        import staging_server
        base=self.handler(body=body,**kwargs)
        h=object.__new__(staging_server.Handler);h.__dict__.update(base.__dict__)
        h.path='/api/admin/user/vpn-mode'
        return h

    def test_legacy_subscription_preserves_personal_title_from_gateway(self):
        from io import BytesIO
        import base64
        os.environ.setdefault('TELEMETRY_DB_PATH',str(Path(fixture.name)/'telemetry.db'))
        import staging_server
        title='base64:'+base64.b64encode('Kingline VPN · Анна'.encode()).decode()
        expiry=(server.utc_now()+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        h=object.__new__(staging_server.Handler)
        h.path='/sub/fixture-legacy';h.headers={'User-Agent':'v2raytun/2.4.7'};h.wfile=BytesIO()
        h.send_response=Mock();h.send_header=Mock();h.end_headers=Mock();h.json_response=Mock()
        row={'short_uuid':'fixture-native','is_blocked':0,'expires':expiry}
        with patch.object(staging_server,'links_connection') as connection,patch.object(staging_server,'Remnawave') as panel,patch.object(staging_server.urllib.request,'urlopen') as opener,patch.dict(os.environ,{'SUBSCRIPTION_INTERNAL_URL':'http://subscription.invalid'}):
            connection.return_value.__enter__.return_value.execute.return_value.fetchone.return_value=row
            panel.return_value.find.return_value={'status':'ACTIVE','expireAt':expiry}
            response=opener.return_value.__enter__.return_value
            response.read.return_value=b'vless://fixture-only'
            response.headers={'Profile-Title':title,'Content-Type':'text/plain'}
            h.subscription('fixture-legacy')
        h.send_response.assert_called_once_with(200)
        h.send_header.assert_any_call('Profile-Title',title)
        self.assertEqual(h.wfile.getvalue(),b'vless://fixture-only')

    def test_announcement_requires_broadcast_permission_2fa_and_csrf(self):
        import announcements
        for method in ('GET','POST'):
            for kwargs in ({},{'email':'regular@example.invalid'}):
                h=self.handler(body={'text':'Hello','revision':0},**kwargs);h.path=announcements.PATH
                announcements.handle(h,method);self.assertEqual(h.json_response.call_args.args[1],403)
            self.grant(broadcast=True)
            h=self.handler(verified=False);h.path=announcements.PATH
            announcements.handle(h,method);self.assertEqual(h.json_response.call_args.args[1],403)
            self.grant(broadcast=False)
        self.grant(broadcast=True)
        h=self.handler();h.path=announcements.PATH;h.validate_post_request.return_value=False
        announcements.handle(h,'POST');h.read_json.assert_not_called()
        with server.connect() as c:self.assertEqual(announcements.read(c)['revision'],0)

    def test_announcement_unicode_stale_updates_clear_and_safe_audit(self):
        import announcements
        self.grant(broadcast=True)
        def request(payload):
            h=self.handler(body=payload);h.path=announcements.PATH
            with patch.object(server,'log_admin_action') as audit:
                announcements.handle(h,'POST')
                return h.json_response.call_args.args,audit
        response,audit=request({'text':'  Новости 🇩🇰\nНовая локация  ','revision':0})
        self.assertEqual(response[0]['announcement']['text'],'Новости 🇩🇰\nНовая локация')
        self.assertEqual(response[0]['announcement']['revision'],1)
        self.assertNotIn('Новости',str(audit.call_args))
        for payload in ({'text':'x','revision':0},{'text':'x'*501,'revision':1},{'text':None,'revision':1},{'text':'x\x00','revision':1},{'text':'x','revision':True},[]):
            response,audit=request(payload)
            self.assertIn(response[1],(400,409));audit.assert_not_called()
        response,_=request({'text':'','revision':1})
        self.assertEqual(response[0]['announcement']['text'],'')
        self.assertEqual(response[0]['announcement']['revision'],2)

    def test_user_protocol_change_requires_permission_2fa_and_csrf(self):
        import vpn_preferences as pref
        for options,status in [({},403),({'email':'regular@example.invalid'},403)]:
            h=self.mode_handler({'tgId':-9050,'transport':'xhttp'},**options)
            with patch.object(pref,'save') as save:h.do_POST();save.assert_not_called()
            self.assertEqual(h.json_response.call_args.args[1],status)
        self.grant(user_transport=True)
        h=self.mode_handler({'tgId':-9050,'transport':'xhttp'},verified=False)
        with patch.object(pref,'save') as save:h.do_POST();save.assert_not_called()
        self.assertEqual(h.json_response.call_args.args[1],403)
        h=self.mode_handler({'tgId':-9050,'transport':'xhttp'});h.validate_post_request.return_value=False
        with patch.object(pref,'save') as save:h.do_POST();save.assert_not_called()

    def test_authorized_user_protocol_change_is_isolated_audited_and_manual(self):
        import vpn_preferences as pref
        server.BillingDB.ensure_user_row(-9050,'Protocol fixture','protocol-fixture')
        server.BillingDB.ensure_user_row(-9051,'Other fixture','other-fixture')
        with server.connect() as c:
            pref.forget(c,-9050);pref.forget(c,-9051)
            before=tuple(c.execute('SELECT balance_rub,key_expires_at,sub_id FROM users WHERE tg_id=-9050').fetchone())
        pref.observe(-9050,platform_hint='android')
        self.grant(user_transport=True)
        h=self.mode_handler({'tgId':-9050,'transport':'xhttp'})
        with patch.object(server,'admin_bootstrap_payload',return_value={'ok':True}),patch.object(server,'log_admin_action') as audit:
            h.do_POST();audit.assert_called_once()
            self.assertEqual(audit.call_args.args[1],'user_transport_change')
        self.assertEqual(pref.read(-9050)['transport'],'xhttp')
        self.assertEqual(pref.read(-9051)['transport'],'raw')
        self.assertTrue(pref.account_devices(-9050)['manual']);self.assertIsNone(pref.account_devices(-9050)['autoDueAt'])
        with server.connect() as c:self.assertEqual(tuple(c.execute('SELECT balance_rub,key_expires_at,sub_id FROM users WHERE tg_id=-9050').fetchone()),before)

    def test_protocol_validation_never_creates_users_or_accepts_bad_ids(self):
        import vpn_preferences as pref
        self.grant(user_transport=True)
        for body in ({'tgId':None,'transport':'raw'},{'tgId':True,'transport':'raw'},{'tgId':1.2,'transport':'raw'},
                     {'tgId':0,'transport':'raw'},{'tgId':-999999999,'transport':'raw'},
                     {'tgId':-9050,'transport':'invalid'},{'tgId':-9050,'transport':[]}):
            h=self.mode_handler(body)
            h.do_POST();self.assertEqual(h.json_response.call_args.args[1],400)
        with server.connect() as c:self.assertIsNone(c.execute('SELECT 1 FROM users WHERE tg_id=-999999999').fetchone())

    def test_owner_can_change_protocol_without_delegated_flag(self):
        import vpn_preferences as pref
        h=self.mode_handler({'tgId':-9050,'transport':'raw'},email='owner@example.invalid')
        with patch.object(pref,'save') as save,patch.object(server,'admin_bootstrap_payload',return_value={'ok':True}),patch.object(server,'log_admin_action'):
            h.do_POST();save.assert_called_once_with(-9050,'raw',allow_blocked=True)

    def test_logs_are_independent_of_node_management(self):
        self.grant(technical=True)
        self.assertTrue(self.dispatch(self.handler(path='logs?export=true')))
        self.assertTrue(self.dispatch(self.handler(path='health')))
        self.assertFalse(self.dispatch(self.handler(path='nodes')))
        self.grant(technical=False,nodes=True)
        self.assertFalse(self.dispatch(self.handler(path='logs')))

    def test_credentials_require_both_explicit_grants(self):
        for nodes,secrets,allowed in ((True,False,False),(False,True,False),(True,True,True)):
            self.grant(nodes=nodes,node_credentials=secrets)
            self.assertEqual(self.dispatch(self.handler(path='credentials'),'POST'),allowed)

    def test_network_configuration_requires_both_node_and_network_access(self):
        routes=(('node-network','GET'),('node-connection','POST'),('node-routes','POST'),('node-rollback','POST'))
        for nodes,network,allowed in ((True,False,False),(False,True,False),(True,True,True)):
            self.grant(nodes=nodes,vpn_config=network)
            for path,method in routes:self.assertEqual(self.dispatch(self.handler(path=path),method),allowed)
        self.grant(nodes=True,vpn_config=False)
        for path,method in (('node-order','GET'),('node-order','POST'),('node-job?id=11111111-1111-4111-a111-111111111111','GET')):
            self.assertTrue(self.dispatch(self.handler(path=path),method))
        self.assertFalse(self.dispatch(self.handler(verified=False,path='node-order')))
        self.assertFalse(self.dispatch(self.handler(email='regular@example.invalid',path='node-order')))
        self.assertTrue(self.dispatch(self.handler(email='owner@example.invalid',path='node-network')))

    def test_review_route_requires_network_permission_but_hide_does_not(self):
        self.grant(nodes=True,vpn_config=False)
        self.assertTrue(self.dispatch(self.handler(path='review-action',body={'operation':'hide'}),'POST'))
        self.assertFalse(self.dispatch(self.handler(path='review-action',body={'operation':'via'}),'POST'))
        self.grant(vpn_config=True)
        self.assertTrue(self.dispatch(self.handler(path='review-action',body={'operation':'via'}),'POST'))
        self.assertFalse(self.dispatch(self.handler(path='node-visibility',verified=False),'POST'))

    def test_retired_raw_configuration_api_is_not_publicly_available(self):
        for path,method in (('vpn-config','GET'),('vpn-validate','POST'),('vpn-apply','POST'),('vpn-rollback','POST')):
            h=self.handler(email='owner@example.invalid',path=path)
            self.assertFalse(self.dispatch(h,method));self.assertEqual(h.json_response.call_args.args[1],404)

    def test_two_factor_regular_user_and_csrf_are_still_required(self):
        self.grant(nodes=True,technical=True,node_credentials=True)
        h=self.handler(verified=False);self.assertFalse(self.dispatch(h));self.assertEqual(h.json_response.call_args.args[0]['error'],'admin_2fa_required')
        self.assertFalse(self.dispatch(self.handler(email='regular@example.invalid')))
        h=self.handler(path='add');h.validate_post_request.return_value=False
        self.assertFalse(self.dispatch(h,'POST'))

    def test_owner_has_access_and_only_owner_can_manage_grants(self):
        self.assertTrue(self.dispatch(self.handler(email='owner@example.invalid',path='credentials'),'POST'))
        h=self.handler(path='admins/permissions')
        h.handle_admin_permissions();self.assertEqual(h.json_response.call_args.args[0]['error'],'owner_required')
        with server.connect() as c:target=c.execute("SELECT id FROM web_admins WHERE email='delegate@example.invalid'").fetchone()[0]
        h=self.handler(email='owner@example.invalid',body={'id':target,'permissions':{'nodes':True,'technical':True,'node_credentials':False,'vpn_config':True}})
        with patch.object(server,'admin_bootstrap_payload',return_value={'ok':True}),patch.object(server,'log_admin_action') as audit:
            h.handle_admin_permissions();audit.assert_called_once()
        with server.connect() as c:
            row=c.execute('SELECT can_nodes,can_technical,can_node_credentials,can_vpn_config FROM web_admins WHERE id=?',(target,)).fetchone()
            self.assertEqual(tuple(row),(1,1,0,1))

class NetworkLookupTests(unittest.TestCase):
    def test_blank_geo_is_retried_and_positive_geo_retained(self):
        server.upsert_xui_ip('lookup-fixture',None,'192.0.2.210','DE',server.iso_now())
        with server.connect() as c:
            c.execute("UPDATE web_xui_ip_history SET geo_updated_at=? WHERE username='lookup-fixture'",((server.utc_now()-timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),))
        self.assertIsNone(server.cached_geo_for_ip('192.0.2.210'))
        server.store_geo_for_user_ip('lookup-fixture','192.0.2.210',{'isp':'JSC ER-Telecom Holding','asn':'21353'})
        self.assertEqual(server.cached_geo_for_ip('192.0.2.210')['isp'],'JSC ER-Telecom Holding')
        self.assertEqual(server.network_kind_from_geo({'isp':'JSC ER-Telecom Holding'}),'home')
        self.assertEqual(server.network_kind_from_geo({'isp':'Beeline Broadband'}),'home')
        self.assertEqual(server.network_kind_from_geo({'isp':'Vimpelcom'}),'mobile')

    def test_asn_fallback_and_no_public_lookup_for_private_ips(self):
        response=lambda payload:Mock(__enter__=Mock(return_value=Mock(read=lambda *args:json.dumps(payload).encode())),__exit__=Mock(return_value=False))
        with patch.object(server.urllib.request,'urlopen',side_effect=[OSError('unavailable'),response({'data':{'asns':[21353]}}),response({'data':{'holder':'ER-Telecom'}})]):
            self.assertEqual(server.fetch_geo_for_ip('192.0.2.210')['isp'],'ER-Telecom')
        with patch.object(server.urllib.request,'urlopen') as lookup:
            self.assertEqual(server.fetch_geo_for_ip('127.0.0.1')['isp'],'');lookup.assert_not_called()

class SubscriptionDeviceTests(unittest.TestCase):
    def test_friendly_subscription_forwards_desktop_identity_and_rejects_invalid_ids(self):
        os.environ.setdefault('TELEMETRY_DB_PATH',str(Path(fixture.name)/'telemetry.db'))
        os.environ.setdefault('SUBSCRIPTION_INTERNAL_URL','http://subscription-guard:8080')
        import staging_server
        from io import BytesIO
        expiry=(server.utc_now()+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        handler=object.__new__(staging_server.Handler)
        handler.path='/sub/business-fixture?device=WorkPC-123456789'
        handler.headers={'User-Agent':'v2rayN/7','Cookie':'private-canary'}
        handler.wfile=BytesIO();handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock();handler.json_response=Mock()
        rows=Mock();rows.__enter__=Mock(return_value=rows);rows.__exit__=Mock(return_value=False)
        rows.execute.return_value.fetchone.return_value={'short_uuid':'opaque-native-token','is_blocked':False,'expires':expiry}
        upstream=Mock();upstream.__enter__=Mock(return_value=upstream);upstream.__exit__=Mock(return_value=False)
        upstream.headers={'Content-Type':'text/plain','X-Hwid-Active':'true'};upstream.read.return_value=b'vless://fixture'
        with patch.object(staging_server,'links_connection',return_value=rows),patch.object(staging_server,'Remnawave') as panel,patch.object(staging_server.urllib.request,'urlopen',return_value=upstream) as fetch:
            panel.return_value.find.return_value={'status':'ACTIVE','expireAt':expiry}
            handler.subscription('business-fixture')
            request=fetch.call_args.args[0]
            self.assertTrue(request.full_url.endswith('/opaque-native-token?device=WorkPC-123456789'))
            self.assertIsNone(request.get_header('Cookie'))
            self.assertEqual(handler.wfile.getvalue(),b'vless://fixture')
            handler.send_header.assert_any_call('X-Hwid-Active','true')
            fetch.reset_mock();handler.path='/sub/business-fixture?device=bad'
            handler.subscription('business-fixture');fetch.assert_not_called()
            self.assertEqual(handler.json_response.call_args.args[1],400)

    def test_proxy_passes_only_required_device_headers(self):
        os.environ.setdefault('TELEMETRY_DB_PATH',str(Path(fixture.name)/'telemetry.db'))
        import staging_server
        from email.message import Message
        headers=Message()
        for k,v in {'User-Agent':'v2rayTun/2.4.7','Accept':'*/*','x-hwid':'DeviceTest0001','x-device-os':'iOS','x-ver-os':'18','x-device-model':'Phone','Authorization':'private-canary','Cookie':'cookie-canary'}.items():headers[k]=v
        forwarded=staging_server.subscription_request_headers(headers)
        self.assertEqual(forwarded['x-hwid'],'DeviceTest0001')
        self.assertNotIn('Authorization',forwarded);self.assertNotIn('Cookie',forwarded)

    def test_online_reports_locations_and_ips_separately_from_devices(self):
        os.environ.setdefault('TELEMETRY_DB_PATH',str(Path(fixture.name)/'telemetry.db'))
        import staging_server
        snap={'at':int(server.time.time()),'nodes':{'node-a':{'code':'DE'}},'users':[{'username':'online-fixture','status':'ACTIVE','deviceCount':2,'deviceLimit':3,'traffic':{'onlineAt':server.iso_now(),'lastConnectedNodeUuid':'node-a'}}]}
        class Rows:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def execute(self,*args):return self
            def fetchall(self):return [{'username':'online-fixture','server':'DE-Germany-01','ip':'203.0.113.1'},{'username':'online-fixture','server':'DK-Denmark-01','ip':'203.0.113.2'}]
        with patch.object(staging_server,'native_snapshot',return_value=snap),patch.object(server,'xui_online_name_map',return_value={}),patch.object(server,'connect',return_value=Rows()):
            result=staging_server.native_online()['users'][0]
        self.assertEqual(result['registeredDevices'],2)
        self.assertEqual(result['activeIpCount'],2)
        self.assertEqual(result['panels'],['DE','DK'])
        self.assertNotIn('REMNA',json.dumps(result))

class ConnectivityAssetTests(unittest.TestCase):
    def test_fixed_asset_range_integrity_and_no_arbitrary_range(self):
        import probe_asset
        from io import BytesIO
        handler=Mock();handler.path='/api/connectivity/probe.bin';handler.headers={'Range':'bytes=0-2047'};handler.wfile=BytesIO();handler.client_ip.return_value='203.0.113.8'
        self.assertTrue(probe_asset.handle(handler));self.assertEqual(handler.wfile.getvalue(),probe_asset.BODY[:2048]);handler.send_response.assert_called_with(206)
        handler.wfile=BytesIO();handler.headers={'Range':'bytes=999-1000'}
        probe_asset.handle(handler);handler.send_response.assert_called_with(416);self.assertEqual(handler.wfile.getvalue(),b'')
        handler.wfile=BytesIO();handler.headers={};probe_asset.handle(handler)
        self.assertEqual(len(handler.wfile.getvalue()),1048576)


class AccountDeviceCaptureTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault('TELEMETRY_DB_PATH',str(Path(fixture.name)/'telemetry.db'))
        import staging_server
        self.site=staging_server
        with server.connect() as c:
            c.execute("INSERT OR IGNORE INTO web_accounts(email,password_hash,email_verified,web_tg_id) VALUES('device@example.invalid','fixture-only',1,-9010)")
            self.account=c.execute("SELECT * FROM web_accounts WHERE email='device@example.invalid'").fetchone()
        self.actor,_=server.ensure_web_user(self.account)
        import vpn_preferences
        self.pref=vpn_preferences
        with server.connect() as c:self.pref.forget(c,self.actor)

    def handler(self,headers=None):
        h=object.__new__(self.site.Handler);h.headers=headers or {}
        return h

    def payload(self,h):
        with patch.object(server.Handler,'auth_payload',return_value={'ok':True,'subscription':{}}):
            return h.auth_payload(self.account)

    def test_authenticated_website_and_miniapp_record_os_without_immediate_change(self):
        h=self.handler({'User-Agent':'Mozilla/5.0 (Linux; Android 15)','X-Kingline-Client':'site'})
        self.assertEqual(self.payload(h)['subscription']['transport'],'raw')
        h.headers={'User-Agent':'opaque-webview','X-Kingline-Client':'miniapp','X-Kingline-Platform':'android'}
        self.payload(h)
        observation=self.pref.account_devices(self.actor)
        self.assertEqual(observation['devices'][0]['source'],'site_and_miniapp')
        self.assertEqual(observation['devices'][0]['family'],'android')
        self.assertIsNotNone(observation['autoDueAt'])

    def test_ipad_hint_and_manual_lock_are_preserved(self):
        self.pref.save(self.actor,'xhttp')
        h=self.handler({'User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)','X-Kingline-Platform':'ios'})
        self.assertEqual(self.payload(h)['subscription']['transport'],'xhttp')
        observation=self.pref.account_devices(self.actor)
        self.assertEqual(observation['devices'][0]['family'],'ios')
        self.assertTrue(observation['manual']);self.assertIsNone(observation['autoDueAt'])

    def test_guest_and_invalid_telegram_init_data_do_not_record_any_account(self):
        h=self.handler({'User-Agent':'Android','X-Kingline-Client':'miniapp'})
        h.session_token=lambda:'invalid-session';h.json_response=Mock()
        with patch.object(self.pref,'observe') as observe:
            h.handle_me();observe.assert_not_called()
            h.read_json=lambda:{'initData':'invalid','tg_id':self.actor}
            with patch.object(server,'validate_telegram_webapp_init_data',side_effect=ValueError('telegram_auth_failed')):
                h.handle_telegram_webapp_auth();observe.assert_not_called()

    def test_analytics_attaches_only_the_requested_users_observations(self):
        self.pref.observe(self.actor,platform_hint='android')
        username=server.normalize_xui_online_username(server.get_user_row(self.actor)['username'])
        with patch.object(self.site,'original_insights',return_value={'username':username,'device':{}}):
            result=self.site.account_device_insights({'username':[username]})
        self.assertEqual(result['accountDevices']['devices'][0]['family'],'android')
        with patch.object(self.site,'original_insights',return_value={'username':'another-account','device':{}}):
            self.assertNotIn('accountDevices',self.site.account_device_insights({'username':['another-account']}))

if __name__=='__main__':unittest.main()
