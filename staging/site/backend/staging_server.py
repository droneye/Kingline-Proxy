"""Self-hosted staging entry point, using the migrated website and native panel API."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
from html import escape
import json
import mimetypes
import os
from pathlib import Path
import sqlite3
import threading
import time
import urllib.request
from urllib.parse import urlparse, unquote,parse_qs,urlencode

import infrastructure
import documentation
import announcements
import probe_asset
import compact_metrics
import server as legacy
import vpn_preferences
from handlers.durable_subscription import retry_panel_sync, schema
from remnawave import Remnawave, connection as links_connection
from test_payments import TestPaymentGateway, connection as payment_connection

legacy.YooKassaGateway = TestPaymentGateway
legacy.is_mail_configured = lambda: True
PUBLIC = Path(os.environ.get('STATIC_ROOT', '/app/site/dist')).resolve()
TELEMETRY = os.environ['TELEMETRY_DB_PATH']

def prepare_metrics():
    with closing(legacy.connect()) as business:
        return compact_metrics.prepare(TELEMETRY, business)

def update_location(account, location):
    if location not in {'auto', 'lt', 'nl', 'dk', 'de'}: raise ValueError('invalid_location')
    tg_id, _ = legacy.ensure_web_user(account)
    with legacy.connect() as c:
        c.execute('UPDATE users SET location_pref=? WHERE tg_id=?', (location, tg_id))
        c.commit()

legacy.set_location = update_location
legacy.schedule_delayed_xray_restart = lambda reason: None
# Old access-file importers and provider-specific writers are never started.
legacy.store_xui_analytics_snapshot = lambda: None
legacy.ensure_recent_xui_analytics = lambda: None

original_insights=legacy.admin_user_insights_payload
def account_device_insights(query):
    result=original_insights(query)
    with closing(legacy.connect()) as c:
        rows=c.execute('SELECT tg_id,username FROM users').fetchall()
    matches=[r['tg_id'] for r in rows if legacy.normalize_xui_online_username(r['username'])==result['username']]
    if len(matches)==1:return {**result,'accountDevices':vpn_preferences.account_devices(matches[0])}
    return result
legacy.admin_user_insights_payload=account_device_insights

original_bootstrap=legacy.admin_bootstrap_payload
def transport_bootstrap(account):
    result=original_bootstrap(account);modes=vpn_preferences.admin_modes()
    return {**result,'users':[{**u,**modes.get(u.get('tgId'),{'transport':'raw','transportManual':False,'transportAutoCompleted':False}),
                              'xhttpAvailable':vpn_preferences.available()} for u in result.get('users',[])]}
legacy.admin_bootstrap_payload=transport_bootstrap

class Handler(legacy.Handler):
    def log_message(self, fmt, *args):
        # Paths may contain bearer subscription tokens; never write them to logs.
        return

    def auth_payload(self, account_row):
        payload = super().auth_payload(account_row)
        payload.get('subscription', {}).pop('backupSubscriptionUrl', None)
        actor,_=legacy.ensure_web_user(account_row)
        # This happens only after authentication (including validated Telegram initData).
        # Retain coarse OS aggregates; a client hint does not grant access to anything.
        headers=getattr(self,'headers',{})
        try:
            vpn_preferences.observe(actor,headers.get('User-Agent',''),headers.get('Sec-CH-UA-Platform',''),
                headers.get('X-Kingline-Platform',''),source='miniapp' if headers.get('X-Kingline-Client')=='miniapp' else 'site')
        except Exception as exc:
            from diagnostics import event
            event('account_platform_record_failed','WARNING',error=type(exc).__name__)
        payload['subscription'].update(vpn_preferences.read(actor))
        with legacy.connect() as c:
            job = c.execute("SELECT * FROM web_account_jobs WHERE account_id=? AND status IN ('pending','running','retrying') ORDER BY created_at DESC LIMIT 1", (account_row['id'],)).fetchone()
        if job:
            payload['subscription']['pendingJobId'] = job['job_id']
            payload['subscription']['provisioningStage'] = legacy.account_job_stage(job['job_id'], job['status'])
        return payload

    def do_GET(self):
        if probe_asset.handle(self): return
        if announcements.handle(self): return
        if documentation.handle(self,'GET'): return
        if infrastructure.handle(self, 'GET'): return
        path = urlparse(self.path).path
        if path == '/staging-gate':
            return self.redirect_home()
        if path == '/api/admin/layout-preview':
            if not self.require_admin_permission('analytics'): return
            from urllib.parse import parse_qs
            q=parse_qs(urlparse(self.path).query)
            width=int(q.get('width',['390'])[0])
            if width not in (320,375,390,768,1280): return self.json_response({'ok':False},400)
            target={'admin':'/admin','nodes':'/admin?section=nodes','technical':'/admin?section=technical','support':'/support'}.get(q.get('view',[''])[0],'/')
            scale=min(1,780/width)
            data=(f'<!doctype html><html lang="ru"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Проверка экрана {width}px</title>'
                f'<style>body{{margin:0;background:#0c0f14;color:#b6bfce;font:13px system-ui;padding:12px}}iframe{{display:block;border:1px solid #394150;width:{width}px;height:1400px;transform:scale({scale});transform-origin:top left}}p{{margin:0 0 10px}}</style>'
                f'<p>Экран {width}px · <a style="color:#ff8da0" href="/admin">Вернуться</a> <span id="size"></span></p><iframe title="Кабинет, {width}px" src="{target}"></iframe>'
                '<script>const f=document.querySelector("iframe");const fit=()=>{f.style.transform="scale("+Math.min(1,(innerWidth-24)/parseInt(f.style.width||getComputedStyle(f).width))+ ")"};addEventListener("resize",fit);fit();f.onload=()=>{const d=f.contentDocument;const check=()=>document.querySelector("#size").textContent=" · Контент "+d.documentElement.scrollWidth+" / экран "+d.documentElement.clientWidth+" px";new ResizeObserver(check).observe(d.body);check()}</script></html>').encode()
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(data);return
        if path == '/api/health':
            try:
                with legacy.connect() as c: c.execute('SELECT 1 FROM users LIMIT 1').fetchone()
                healthy = time.monotonic() - WORKER_TICK < 300
            except Exception: healthy = False
            return self.json_response({'ok': healthy}, 200 if healthy else 503)
        if path.startswith('/sub/'):
            return self.subscription(path.removeprefix('/sub/'))
        if path.startswith('/test-payment/'):
            return self.test_payment(path.rsplit('/', 1)[-1], confirm=False)
        if path.startswith('/downloads/'):
            return self.send_file(Path('/data/downloads'), path.removeprefix('/downloads/'))
        if path.startswith('/api/'):
            return super().do_GET()
        if path.startswith('/support_uploads/'):
            account, _ = self.require_account()
            if not account: return
            with legacy.connect() as c:
                rows = c.execute('SELECT account_id,attachments_json FROM web_support_messages WHERE attachments_json LIKE ?', ('%' + path + '%',)).fetchall()
            permitted = any((r['account_id'] == account['id'] or legacy.is_admin_account(account))
                and any(a.get('url') == path for a in json.loads(r['attachments_json'] or '[]')) for r in rows)
            if not permitted: return self.json_response({'ok': False}, 404)
            return self.send_file(Path(legacy.SUPPORT_UPLOAD_DIR), path.removeprefix('/support_uploads/'))
        resolved = (PUBLIC / unquote(path).lstrip('/')).resolve()
        if not resolved.is_relative_to(PUBLIC):
            return self.json_response({'ok': False}, 404)
        if not resolved.is_file():
            if Path(path).suffix: return self.json_response({'ok': False}, 404)
            resolved = PUBLIC / 'index.html'
        data = resolved.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(resolved)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache' if resolved.name == 'index.html' else 'public, max-age=31536000, immutable')
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, directory, name):
        directory = directory.resolve()
        path = (directory / unquote(name)).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            return self.json_response({'ok': False}, 404)
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(path)[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(path.stat().st_size))
        self.send_header('Cache-Control', 'private, no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        with path.open('rb') as f:
            while block := f.read(128 * 1024): self.wfile.write(block)

    def do_POST(self):
        if announcements.handle(self, 'POST'): return
        if infrastructure.handle(self, 'POST'): return
        path = urlparse(self.path).path
        if path == '/api/admin/user/vpn-mode':
            if self.validate_post_request(path) is not True:return
            account=self.require_admin_permission('user_transport')
            if not account:return
            try:
                body=self.read_json()
                if not isinstance(body,dict):raise ValueError('invalid_transport')
                raw_actor=body.get('tgId')
                if isinstance(raw_actor,bool) or not isinstance(raw_actor,(int,str)) or not str(raw_actor).lstrip('-').isdigit():raise ValueError('invalid_user')
                actor=int(raw_actor)
                if not actor:raise ValueError('invalid_user')
                vpn_preferences.save(actor,body.get('transport'),allow_blocked=True)
            except (ValueError,TypeError) as error:
                code=str(error) if str(error) in ('invalid_user','invalid_transport','xhttp_not_ready','permission_denied') else 'invalid_user'
                return self.json_response({'ok':False,'error':code},400)
            except sqlite3.Error as error:
                from diagnostics import event
                event('admin_user_transport_failed','ERROR',error=type(error).__name__)
                return self.json_response({'ok':False,'error':'operation_failed'},503)
            legacy.log_admin_action(account,'user_transport_change',target=f'tg:{actor}',summary={'transport':body['transport'],'automaticDisabled':True},
                                    client_ip=self.client_ip(),user_agent=self.headers.get('User-Agent',''))
            return self.json_response(legacy.admin_bootstrap_payload(account))
        if path == '/api/account/vpn-mode':
            if self.validate_post_request(path) is not True:return
            account,_=self.require_account()
            if not account:return
            actor,_=legacy.ensure_web_user(account)
            body=self.read_json()
            if not isinstance(body,dict):return self.json_response({'ok':False,'error':'invalid_transport'},400)
            try:vpn_preferences.save(actor,body.get('transport'))
            except ValueError as error:
                return self.json_response({'ok':False,'error':str(error)},400)
            return self.json_response(self.auth_payload(account))
        if path == '/api/account/topup':
            account, _ = self.require_account()
            if not account: return
            tg_id, _ = legacy.ensure_web_user(account)
            if str(tg_id) not in os.environ.get('SANDBOX_ALLOWED_TG_IDS','').split(','):
                return self.json_response({'ok':False,'error':'sandbox_payments_restricted'},503)
        if path == '/api/admin/dns/location':
            account = self.require_admin_permission('dns')
            if account: return self.json_response({'ok':False,'error':'legacy_dns_read_only'},410)
            return
        if path.startswith('/api/staging/pay/'):
            if self.validate_post_request(path) is not True: return
            return self.test_payment(path.rsplit('/', 1)[-1], confirm=True)
        if path == '/api/client-error':
            if self.validate_post_request(path) is not True: return
            if legacy.is_rate_limited(self.client_ip(), '/api/client-error'):
                return self.json_response({'ok':False},429)
            payload = self.read_json()
            # Low-cardinality counters, no page URL, form data or client stack.
            kind = str(payload.get('kind', 'unknown'))
            if kind not in {'error','unhandledrejection','react-boundary','network'}: kind='other'
            with closing(compact_metrics.open_db(TELEMETRY)) as c, c:
                c.execute("INSERT INTO client_error_counts VALUES(date('now'),?,1) ON CONFLICT(day,kind) DO UPDATE SET count=count+1", (kind,))
            from diagnostics import event
            fields={k:str(payload[k])[:120] for k in ('asset','route','error') if k in payload and isinstance(payload[k],str)}
            event('client_error', 'ERROR', kind=kind, **fields)
            return self.json_response({'ok': True})
        return super().do_POST()

    def test_payment(self, pid, confirm):
        account, _ = self.require_account()
        if not account: return
        account_tg, _ = legacy.ensure_web_user(account)
        if not pid.startswith('stg_'): return self.json_response({'ok': False}, 404)
        with closing(payment_connection()) as c, c:
            row = c.execute('SELECT amount,tg_id FROM sandbox_payments WHERE id=?', (pid,)).fetchone()
            if not row: return self.json_response({'ok': False, 'error': 'payment_not_found'}, 404)
            tg_id = row[1]
            if tg_id != account_tg or str(tg_id) not in os.environ.get('SANDBOX_ALLOWED_TG_IDS','').split(','):
                return self.json_response({'ok':False,'error':'payment_not_found'},404)
            if confirm:
                c.execute("UPDATE sandbox_payments SET status='succeeded' WHERE id=?", (pid,))
                # The normal verified payment ledger credits once, even on repeat.
        if confirm:
            payment = asyncio.run(TestPaymentGateway().get_payment(pid))
            legacy.validate_yookassa_payment(payment, legacy.account_payment_row(pid, tg_id))
            legacy.credit_succeeded_payment(pid)
            return self.json_response({'ok': True, 'test': True})
        html = f'''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Пополнение · Kingline</title><style>body{{background:#101216;color:#f4f4f5;font:17px system-ui;max-width:480px;margin:12vh auto;padding:24px;line-height:1.6}}button,a{{background:#ff667c;color:#14151a;padding:16px;border:0;border-radius:14px;display:block;margin-top:24px;font:inherit}}small{{color:#b3b7c0}}</style><small>KINGLINE · ДЕМОНСТРАЦИЯ ОПЛАТЫ</small><h1>Пополнение на {row[0]} ₽</h1><p>Деньги с карты не списываются. Это проверка пополнения без настоящей оплаты.</p><button id="confirm">Проверить пополнение</button><a href="/">Вернуться в кабинет</a><script>document.querySelector('#confirm').onclick=async()=>{{const r=await fetch('/api/staging/pay/{escape(pid)}',{{method:'POST',headers:{{'Content-Type':'application/json','X-Kingline-Request':'fetch'}},body:'{{}}'}});if(r.ok)location.href='/?testPayment=complete';else alert('Не удалось подтвердить платёж');}}</script></html>'''.encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(html)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(html)

    def subscription(self, sub_id):
        if not sub_id or len(sub_id) > 128 or '/' in sub_id:
            return self.json_response({'ok': False}, 404)
        with links_connection() as c:
            row = c.execute('''SELECT l.short_uuid,u.is_blocked,COALESCE(u.key_expires_at,u.subscription_expires_at) expires
                FROM remna_links l JOIN users u ON u.sub_id=l.sub_id WHERE l.sub_id=?''', (sub_id,)).fetchone()
        expiry = legacy.parse_iso(row['expires']) if row else None
        if not row or row['is_blocked'] or not expiry or expiry <= datetime.now(timezone.utc):
            return self.json_response({'ok': False}, 404)
        try:
            user = Remnawave().find(sub_id)
            if not user or user['status'] != 'ACTIVE' or datetime.fromisoformat(user['expireAt'].replace('Z','+00:00')) <= datetime.now(timezone.utc):
                return self.json_response({'ok': False}, 404)
            device=parse_qs(urlparse(self.path).query).get('device',[''])[0]
            if device and not __import__('re').fullmatch(r'[A-Za-z0-9-]{10,64}',device):return self.json_response({'ok':False,'error':'invalid_device'},400)
            request = urllib.request.Request(os.environ['SUBSCRIPTION_INTERNAL_URL'].rstrip('/') + '/' + row['short_uuid']+('?' + urlencode({'device':device}) if device else ''), headers=subscription_request_headers(self.headers))
            with urllib.request.urlopen(request, timeout=10) as r:
                data = r.read(2_000_000)
                headers = dict(r.headers)
            self.send_response(200)
            for k, v in headers.items():
                if k.lower() in {'content-type','subscription-userinfo','profile-update-interval','profile-title','support-url','announce','announce-url','x-hwid-active','x-hwid-limit','x-hwid-not-supported','x-hwid-max-devices-reached'}:
                    self.send_header(k, v)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as exc:
            self.send_response(exc.code if exc.code in (400,403,404,429) else 503)
            self.send_header('Cache-Control','no-store')
            for name in ('x-hwid-active','x-hwid-not-supported','x-hwid-max-devices-reached','x-hwid-limit'):
                if exc.headers.get(name):self.send_header(name,exc.headers[name])
            self.send_header('Content-Length','0');self.end_headers()
        except Exception:
            return self.json_response({'ok': False, 'error': 'subscription_unavailable'}, 503)

ALIASES = {}
WORKER_TICK = time.monotonic()

def subscription_request_headers(headers):
    allowed=('User-Agent','Accept','x-hwid','x-device-os','x-ver-os','x-device-model')
    return {name:headers[name][:512] for name in allowed if headers.get(name)}

def enrich_recent_networks():
    # Bounded background work. External lookups never delay an admin request.
    with legacy.connect() as c:
        rows=c.execute('SELECT username,ip FROM web_xui_ip_history WHERE last_seen_at>? ORDER BY last_seen_at DESC LIMIT 80', (compact_metrics.iso(int(time.time())-30*86400),)).fetchall()
    pending={}
    for row in rows:
        if row['ip'] and not legacy.cached_geo_for_ip(row['ip']):pending.setdefault(row['ip'],[]).append(row['username'])
    for ip,names in list(pending.items())[:4]:
        geo=legacy.fetch_geo_for_ip(ip)
        for name in set(names):legacy.store_geo_for_user_ip(name,ip,geo)

def native_snapshot():
    try:
        data=json.loads(Path('/logs/native-snapshot.json').read_text())
        if time.time()-data['at']<300: return data
    except (OSError,ValueError,KeyError): pass
    return None

def sample_metrics():
    snapshot=native_snapshot()
    if snapshot:
        users=snapshot['users']
    else:
        users=[]
        with links_connection() as links:
            for u in Remnawave().users():
                row=links.execute('SELECT username FROM users WHERE sub_id=(SELECT sub_id FROM remna_links WHERE remna_id=?)',(u['id'],)).fetchone()
                if row: users.append({'id':u['id'],'nativeUsername':u['username'],'username':row['username'],'traffic':u.get('userTraffic') or {}})
    now=datetime.now(timezone.utc);sampled_at=compact_metrics.iso(int(now.timestamp())//60*60);aliases={}
    with links_connection() as links, closing(compact_metrics.open_db(TELEMETRY)) as metrics, metrics:
        # Preserve a consistent baseline across the change of collection method.
        if not metrics.execute("SELECT 1 FROM sqlite_master WHERE name='native_counters'").fetchone():
            metrics.execute('UPDATE metrics_web_xui_user_samples SET up_bytes=0,down_bytes=0')
        grouped={}
        for u in users:
            row=links.execute('SELECT username,tg_id,tg_name FROM users WHERE username=?',(u['username'],)).fetchone()
            if not row:continue
            for value in (str(u['id']),u['nativeUsername'],str(u['id'])+'.'+u['nativeUsername']):
                aliases[value.lower()]=legacy.normalize_xui_online_username(row['username'])
            traffic=u['traffic'];at=traffic.get('onlineAt')
            recent=int(bool(at and (now-datetime.fromisoformat(at.replace('Z','+00:00'))).total_seconds()<180))
            used=int(traffic.get('usedTrafficBytes',0));total=int(traffic.get('lifetimeUsedTrafficBytes',used))
            tracked=compact_metrics.traffic_delta(metrics,u['id'],total)
            g=grouped.setdefault(legacy.normalize_xui_online_username(row['username']),{'row':row,'tracked':0,'total':0,'recent':0,'at':None,'panels':set()})
            code=(snapshot or {}).get('nodes',{}).get(traffic.get('lastConnectedNodeUuid'),{}).get('code')
            if code:g['panels'].add(code)
            g['tracked']+=tracked;g['total']+=total;g['recent']=max(g['recent'],recent)
            if at:g['at']=max(g['at'] or at,at)
        for username,g in grouped.items():
            row=g['row'];at=g['at'];recent=g['recent']
            previous=metrics.execute('SELECT sampled_at,down_bytes,online_clients,panels_json FROM metrics_web_xui_user_samples WHERE username=? ORDER BY sampled_at DESC LIMIT 1',(username,)).fetchone()
            changed=not previous or (previous[1],previous[2],previous[3])!=(g['tracked'],recent,json.dumps(sorted(g['panels'])))
            if changed or previous[0]<compact_metrics.iso(int(time.time())-900):
                metrics.execute('DELETE FROM metrics_web_xui_user_samples WHERE username=? AND sampled_at=?',(username,sampled_at))
                metrics.execute('INSERT INTO metrics_web_xui_user_samples(sampled_at,username,tg_id,name,up_bytes,down_bytes,total_bytes,online_clients,panels_json) VALUES(?,?,?,?,0,?,?,?,?)',
                    (sampled_at,username,row['tg_id'],row['tg_name'],g['tracked'],g['total'],recent,json.dumps(sorted(g['panels']))))
            if at:
                metrics.execute("""INSERT INTO metrics_web_xui_user_last_seen VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(username) DO UPDATE SET last_seen_at=excluded.last_seen_at,
                    online_clients=excluded.online_clients,panels_json=excluded.panels_json,updated_at=excluded.updated_at""",
                    (username,row['tg_id'],row['tg_name'],at,recent,json.dumps(sorted(g['panels'])),compact_metrics.iso(int(now.timestamp()))))
    ALIASES.clear();ALIASES.update(aliases)


def native_online():
    snapshot=native_snapshot()
    if not snapshot:return {'ok':True,'stale':True,'count':0,'rawCount':0,'users':[],'panels':[],'updatedAt':None}
    names=legacy.xui_online_name_map();by_username={}
    cutoff=compact_metrics.iso(int(time.time())-180)
    with legacy.connect() as c:
        active=c.execute('SELECT username,server,ip,MAX(seen_at) at FROM web_xui_access_events WHERE seen_at>? GROUP BY username,server,ip',(cutoff,)).fetchall()
    relay_ips={n.get('ip') for n in snapshot.get('nodes',{}).values()}
    # Ignore older synthetic probes from the control host during the 3-minute
    # transition to the dedicated service identity.
    active=[r for r in active if r['ip']!='198.51.100.10']
    for u in snapshot['users']:
        at=legacy.parse_iso(u['traffic'].get('onlineAt'))
        if u.get('status')!='ACTIVE' or not at or (legacy.utc_now()-at).total_seconds()>180:continue
        username=legacy.normalize_xui_online_username(u['username'])
        meta=names.get(username,{})
        rows=[r for r in active if r['username']==username]
        locations={}
        for row in rows:
            code=row['server'].split('-')[0]
            locations.setdefault(code,set())
            if row['ip'] and row['ip'] not in relay_ips:locations[code].add(row['ip'])
        fallback=snapshot.get('nodes',{}).get(u['traffic'].get('lastConnectedNodeUuid'),{}).get('code')
        ips={r['ip'] for r in rows if r['ip'] and r['ip'] not in relay_ips}
        by_username[username]={'username':username,**meta,'clientCount':len(ips),'activeIpCount':len(ips),
            'registeredDevices':u.get('deviceCount'),'deviceLimit':u.get('deviceLimit') or 3,
            'locations':[{'code':code,'activeIps':len(values) or None} for code,values in sorted(locations.items())],
            'panels':sorted(locations) or ([fallback] if fallback else []),'lastSeenAt':u['traffic'].get('onlineAt')}
    users=list(by_username.values())
    return {'ok':True,'stale':False,'loading':False,'refreshing':False,'count':len(users),'rawCount':sum(u['activeIpCount'] for u in users),'users':users,'panels':[],'updatedAt':compact_metrics.iso(snapshot['at'])}


def native_live_sites(online):
    result=[]
    with legacy.connect() as c:
        for u in online:
            rows=c.execute("""SELECT domain,MAX(seen_at) seen_at FROM web_xui_access_events
                WHERE username=? AND seen_at>? GROUP BY domain ORDER BY seen_at DESC LIMIT 30""",
                (u['username'],compact_metrics.iso(int(time.time())-180))).fetchall()
            sites=[{'name':legacy.site_display_name(r['domain']),'domain':legacy.registrable_domain(r['domain']),'seenAt':r['seen_at']}
                for r in rows if legacy.is_user_site_event({'domain':r['domain'],'port':443})]
            result.append({'username':u['username'],'name':u.get('name') or u['username'],'clientCount':u.get('activeIpCount',0),'sites':sites[:6]})
    return result

legacy.admin_online_snapshot=native_online
legacy.xui_live_sites_for_online_users=native_live_sites

def worker():
    global WORKER_TICK
    sampled = maintained = automatic_checked = 0
    while True:
        if time.monotonic()-automatic_checked>=60:
            try:
                changed=vpn_preferences.apply_due()
                if changed['completed']:
                    from diagnostics import event
                    event('vpn_auto_transport_applied',**changed)
            except Exception as exc:
                from diagnostics import event
                event('vpn_auto_transport_failed','ERROR',error=type(exc).__name__)
            automatic_checked=time.monotonic()
        try:
            asyncio.run(retry_panel_sync())
            # Resume requests that crashed before saving a provisioning intent.
            with legacy.connect() as c:
                rows = c.execute("SELECT job_id,account_id,action FROM web_account_jobs WHERE status IN ('pending','running') AND julianday(updated_at)<julianday('now','-2 minutes') LIMIT 5").fetchall()
            for row in rows: legacy.run_account_job(*row)
            if time.monotonic() - sampled >= 60:
                sample_metrics()
                enrich_recent_networks()
                sampled = time.monotonic()
            compact_metrics.ingest(TELEMETRY, '/logs/ingest', ALIASES)
            if time.monotonic() - maintained >= 3600:
                compact_metrics.maintain(TELEMETRY)
                maintained = time.monotonic()
        except Exception as e:
            legacy.logger.warning('Reconciliation cycle: %s', type(e).__name__)
        WORKER_TICK = time.monotonic()
        # Rate-limit memory stays bounded even with many public visitors.
        if len(legacy.RATE_LIMITS) > 4096:
            cutoff=time.time()-legacy.RATE_LIMIT_WINDOW_SECONDS
            for key in list(legacy.RATE_LIMITS):
                if not legacy.RATE_LIMITS[key] or legacy.RATE_LIMITS[key][-1]<cutoff: legacy.RATE_LIMITS.pop(key,None)
            while len(legacy.RATE_LIMITS)>4096: legacy.RATE_LIMITS.pop(next(iter(legacy.RATE_LIMITS)))
        time.sleep(15)

class BoundedServer(legacy.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    slots = threading.BoundedSemaphore(48)
    def process_request(self, request, address):
        request.settimeout(20)
        if not self.slots.acquire(blocking=False):
            try: request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            finally: self.shutdown_request(request)
            return
        try: super().process_request(request,address)
        except BaseException:
            self.slots.release(); raise
    def process_request_thread(self, request, address):
        try: super().process_request_thread(request,address)
        finally: self.slots.release()

if __name__ == '__main__':
    legacy.configure_diagnostics('staging-api')
    legacy.init_db()
    with closing(legacy.connect()) as business, business:
        vpn_preferences.schema(business)
        announcements.schema(business)
    tables = prepare_metrics()
    legacy.connect = lambda: compact_metrics.connect(legacy.DB_PATH, TELEMETRY)
    threading.Thread(target=worker, daemon=True).start()
    BoundedServer(('0.0.0.0', 8788), Handler).serve_forever()
