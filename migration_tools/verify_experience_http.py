"""Read-only acceptance: account/owner/2FA boundaries and one subscription URL."""
import json
from pathlib import Path
import re
import httpx
access=json.loads(Path('private/staging-test-access.json').read_text());base=access['site']
headers={'Origin':base,'X-Kingline-Request':'fetch'}
result={}
with httpx.Client(base_url=base,timeout=20,headers=headers) as client:
    for action in ('nodes','job?id=11111111-1111-4111-a111-111111111111','logs','health','node-network','node-order','node-job?id=11111111-1111-4111-a111-111111111111'):
        r=client.get('/api/admin/infrastructure/'+action);assert r.status_code==401
        assert re.fullmatch(r'[a-f0-9]{32}',r.headers.get('x-request-id',''))
    for action in ('preflight','add','update','remove','credentials','retry','node-connection','node-routes','node-order','node-rollback'):
        r=client.post('/api/admin/infrastructure/'+action,json={});assert r.status_code==401
    result['infrastructure_requires_account']=True
    r=client.post('/api/auth/login',json={'email':access['test_email'],'password':access['test_password']});assert r.status_code==200
    subscription=r.json()['subscription'];assert 'backupSubscriptionUrl' not in subscription and subscription.get('subscriptionUrl')
    for action in ('nodes','job?id=11111111-1111-4111-a111-111111111111','logs','health','node-network','node-order','node-job?id=11111111-1111-4111-a111-111111111111'):
        r=client.get('/api/admin/infrastructure/'+action);assert r.status_code==403 and r.json()['error']=='admin_2fa_required'
    for action in ('preflight','add','update','remove','credentials','retry','node-connection','node-routes','node-order','node-rollback'):
        r=client.post('/api/admin/infrastructure/'+action,json={});assert r.status_code==403
    result['owner_requires_two_factor_for_reads_and_writes']=True
    result['single_subscription_url']=True
    fixture=json.loads(Path('private/staging-registration-fixture.json').read_text())
    r=client.post('/api/auth/login',json={'email':fixture['email'],'password':fixture['password']});assert r.status_code==200
    for action in ('nodes','job?id=11111111-1111-4111-a111-111111111111','logs','health','node-network','node-order','node-job?id=11111111-1111-4111-a111-111111111111'):
        r=client.get('/api/admin/infrastructure/'+action);assert r.status_code==403
    result['regular_account_cannot_access_infrastructure']=True
    result['request_ids_returned']=True
Path('staging/verification-experience-http.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
