"""Acceptance tests against the private sandbox, using only newly created fixtures."""
import base64
import json
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import time
from urllib.parse import urlparse, parse_qs
import httpx

access=json.loads(Path('private/staging-test-access.json').read_text())
base=access['site']
email='migration-check-'+str(int(time.time()))+'@kingline.online'
password=secrets.token_urlsafe(24)
results={}

def mail_for(recipient):
    script='''import json,pathlib
files=sorted(pathlib.Path('/opt/kingline-staging/data/mail').glob('*.json'),reverse=True)
for p in files:
 r=json.loads(p.read_text())
 if r['to']==''' + repr(recipient) + ''':print(json.dumps(r));break
'''
    r=subprocess.run(['ssh','198.51.100.10','python3 -c '+shlex.quote(script)],capture_output=True,text=True,check=True)
    return json.loads(r.stdout)

with httpx.Client(base_url=base,timeout=20,
        headers={'Origin':base,'X-Kingline-Request':'fetch'}) as c:
    r=c.post('/api/auth/register',json={'email':email,'password':password,'name':'Проверка регистрации'})
    assert r.status_code==202,(r.status_code,r.text)
    code=re.search(r'\b\d{6}\b',mail_for(email)['text']).group()
    r=c.post('/api/auth/confirm-registration',json={'email':email,'code':code})
    assert r.status_code==201,(r.status_code,r.text)
    results['registration_and_captured_email']=True
    r=c.post('/api/auth/confirm-registration',json={'email':email,'code':code})
    assert r.status_code==400
    results['confirmation_code_one_use']=True
    r=c.post('/api/auth/password-reset',json={'email':email});assert r.status_code==202,r.text
    text=mail_for(email)['text']
    reset_url=next(u for u in re.findall(r'https://[^\s]+',text) if 'reset_token=' in u)
    reset_token=parse_qs(urlparse(reset_url).query)['reset_token'][0]
    new_password=secrets.token_urlsafe(24)
    r=c.post('/api/auth/password-reset-confirm',json={'token':reset_token,'password':new_password})
    assert r.status_code==200,r.text
    r=c.post('/api/auth/password-reset-confirm',json={'token':reset_token,'password':new_password})
    assert r.status_code==400
    results['password_reset_one_use']=True
    r=c.post('/api/auth/login',json={'email':email,'password':password});assert r.status_code==401
    r=c.post('/api/auth/login',json={'email':email,'password':new_password});assert r.status_code==200
    results['old_password_rejected_new_password_accepted']=True
    r=c.post('/api/account/trial',json={});assert r.status_code in (200,202),r.text
    job=r.json()['job']['id']
    for attempt in range(40):
        d=c.post('/api/account/job-status',json={'jobId':job}).json()
        if d['job']['status'] in {'succeeded','failed'}:break
        time.sleep(.3)
    assert d['job']['status']=='succeeded',d['job']
    results['trial_native_provisioning']=True
    r=c.post('/api/account/trial',json={});assert r.status_code==400
    results['trial_cannot_be_claimed_twice']=True
    subscription=d['subscription']['subscriptionUrl']
    r=httpx.get(subscription,headers={'User-Agent':'v2rayTun/2.4.7'},timeout=20)
    assert r.status_code==200
    uris=base64.b64decode(r.content).decode().splitlines()
    assert sum(u.startswith('vless://') for u in uris)==4
    results['phone_compatible_subscription']=True
    png='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j7yQAAAAASUVORK5CYII='
    r=c.post('/api/support/send',json={'text':'Автотест переноса: сообщение и вложение тестового аккаунта.','attachments':[{'name':'test.png','type':'image/png','data':png}]})
    assert r.status_code==200,(r.status_code,r.text)
    messages=c.get('/api/support/messages').json()['messages']
    attachment=next(a['url'] for m in messages for a in m.get('attachments',[]))
    assert c.get(attachment).status_code==200
    with httpx.Client(base_url=base,auth=(access['gate_user'],access['gate_password'])) as guest:
        assert guest.get(attachment).status_code==401
    results['support_and_private_attachments']=True
    Path('private/staging-registration-fixture.json').write_text(json.dumps({'email':email,'password':new_password,'subscription':subscription}))

results['public_site']=httpx.get(base,timeout=20).status_code==200
assert all(results.values())
Path('staging/verification-http.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,ensure_ascii=False,indent=2))
