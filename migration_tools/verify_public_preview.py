"""Public-site acceptance checks, using only existing disposable test accounts."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import time
import httpx
access=json.loads(Path('private/staging-test-access.json').read_text());base=access['site'];result={}
headers={'Origin':base,'X-Kingline-Request':'fetch'}
with httpx.Client(base_url=base,timeout=20,headers=headers) as guest:
    r=guest.get('/');assert r.status_code==200
    assert 'staging-gate' not in r.text
    result['public_home_without_extra_password']=True
    assert guest.get('/api/admin/bootstrap').status_code==401
    assert guest.get('/test-payment/stg_unknown').status_code==401
    result['admin_and_checkout_require_account']=True
    assert guest.post('/api/staging/pay/stg_unknown',json={}).status_code==401
with httpx.Client(base_url=base,timeout=20,headers=headers) as owner:
    r=owner.post('/api/auth/login',json={'email':access['test_email'],'password':access['test_password']});assert r.status_code==200
    data=r.json();balance=data['subscription']['balanceRub']
    assert owner.get('/api/admin/online').status_code==403
    result['admin_still_requires_two_factor']=True
    r=owner.post('/api/account/topup',json={'amount':300});assert r.status_code==200,r.status_code
    invoice=r.json()['paymentId'];url=r.json()['confirmationUrl']
    assert owner.get(url).status_code==200
    with httpx.Client(base_url=base,timeout=20,headers=headers) as other:
        fixture=json.loads(Path('private/staging-registration-fixture.json').read_text())
        r=other.post('/api/auth/login',json={'email':fixture['email'],'password':fixture['password']});assert r.status_code==200
        assert other.get(url).status_code==404
        assert other.post('/api/staging/pay/'+invoice,json={}).status_code==404
        assert other.post('/api/account/topup',json={'amount':300}).status_code==503
    assert owner.get('/api/auth/me').json()['subscription']['balanceRub']==balance
    result['sandbox_invoice_owned_no_cross_account_credit']=True
    r=owner.get(data['subscription']['subscriptionUrl'],headers={'User-Agent':'v2rayTun/2.4.7'});assert r.status_code==200
    result['subscription_bridge_still_works']=True
    def request(i):
        t=time.monotonic();r=owner.get('/api/auth/me');return r.status_code,(time.monotonic()-t)*1000
    with ThreadPoolExecutor(max_workers=12) as pool:responses=list(pool.map(request,range(120)))
    assert all(r[0]==200 for r in responses)
    times=sorted(r[1] for r in responses)
    result['parallel_account_reads']={'requests':120,'concurrency':12,'errors':0,'p50_ms':round(statistics.median(times),1),'p95_ms':round(times[int(len(times)*.95)],1)}
Path('staging/verification-polish-http.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
