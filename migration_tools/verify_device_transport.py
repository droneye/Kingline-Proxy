"""Server-only checks of a pre-existing synthetic account. No new VPN/HWID identity."""
from contextlib import closing
import hashlib
import http.cookiejar
import json
from pathlib import Path
import sqlite3
import subprocess
import urllib.error
import urllib.request

ROOT=Path('/opt/kingline-staging');CONTROL=Path('/opt/kingline-control')
fixture=json.loads((ROOT/'secrets/test-access.json').read_text());site=fixture['site']
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def request(path,body=None,headers=None,anonymous=False):
    values={'Origin':site,'X-Kingline-Request':'fetch','User-Agent':'Mozilla/5.0 (Linux; Android 15) KinglineQA',
            'X-Kingline-Client':'site',**(headers or {})}
    data=None if body is None else json.dumps(body).encode()
    if data is not None:values['Content-Type']='application/json'
    req=urllib.request.Request(site+path,headers=values,data=data)
    try:r=(urllib.request.urlopen if anonymous else opener.open)(req,timeout=30)
    except urllib.error.HTTPError as e:r=e
    with r:raw=r.read();return r.status,raw

def db():return sqlite3.connect('file:'+str(ROOT/'data/vpn_bot.db')+'?mode=ro',uri=True)
report={}
with closing(sqlite3.connect('file:/opt/kingline-control/before-device-transport-20261024/business.sqlite?mode=ro',uri=True)) as c:
    legacy=c.execute('SELECT tg_id,transport FROM vpn_preferences').fetchall()
status,raw=request('/api/auth/login',{'email':fixture['test_email'],'password':fixture['test_password']})
assert status==200,'fixture_login_failed'
account=json.loads(raw);before=account['subscription']
status,raw=request('/api/auth/me',headers={'User-Agent':'opaque-webview','X-Kingline-Client':'miniapp','X-Kingline-Platform':'android'})
assert status==200 and json.loads(raw)['subscription']['transport']==before['transport']
with closing(db()) as c:
    observed=c.execute("SELECT seen_on_site,seen_in_miniapp FROM account_platforms WHERE tg_id=-900000001 AND platform='android'").fetchone()
    assert observed==(1,1),'missing_observation'
    count=c.execute('SELECT COUNT(*) FROM account_platforms WHERE tg_id=-900000001').fetchone()[0]
report['authenticated_site_and_miniapp_signals_recorded']=True
status,raw=request('/api/auth/telegram-webapp',{'initData':'invalid','tg_id':-900000001},
                   headers={'X-Kingline-Client':'miniapp','X-Kingline-Platform':'ios'},anonymous=True)
assert status==401,'invalid_telegram_accepted'
status,_=request('/api/auth/me',anonymous=True);assert status==200
with closing(db()) as c:
    assert c.execute('SELECT COUNT(*) FROM account_platforms WHERE tg_id=-900000001').fetchone()[0]==count
report['guest_and_invalid_telegram_not_recorded']=True
status,raw=request('/api/account/vpn-mode',{'transport':before['transport']})
assert status==200
after=json.loads(raw)['subscription']
assert all(after[k]==before[k] for k in ['transport','subscriptionUrl','balanceRub','expiresAt'])
with closing(db()) as c:
    row=c.execute('SELECT selection_source,manual_selected_at,auto_due_at FROM vpn_preferences WHERE tg_id=-900000001').fetchone()
    assert row[0]=='manual' and row[1] is not None and row[2] is None
    assert all(c.execute('SELECT transport,selection_source,manual_selected_at FROM vpn_preferences WHERE tg_id=?',(actor,)).fetchone()[:2]==(mode,'manual') for actor,mode in legacy)
report['legacy_choices_preserved_and_manual_locked']=True
report['same_mode_link_balance_expiry']=True
for path in ['/api/admin/analytics/user-insights?username=fixture','/api/admin/documentation','/api/admin/documentation/export']:
    status,_=request(path,anonymous=True);assert status==401,'admin_data_exposed'
report['admin_routes_protected']=True
published=subprocess.check_output(['docker','exec','kingline-staging-api-1','cat','/app/docs/service.md'])
assert hashlib.sha256(published).digest()==hashlib.sha256((ROOT/'docs/service.md').read_bytes()).digest()
report['documentation_current']=True
status,_=request('/api/health',anonymous=True);assert status==200
report['healthy']=True
target=CONTROL/'device-transport-verification.json';target.write_text(json.dumps(report,indent=2));target.chmod(0o600)
print(json.dumps(report))
