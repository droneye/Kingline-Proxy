"""Read-only subscription delivery compatibility; never print user keys or URLs."""
import base64
import json
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import httpx
access=json.loads(Path('private/staging-test-access.json').read_text())
result={}
with httpx.Client(timeout=20,headers={'Origin':access['site'],'X-Kingline-Request':'fetch'}) as client:
    response=client.post(access['site']+'/api/auth/login',json={'email':access['test_email'],'password':access['test_password']})
    assert response.status_code==200
    url=response.json()['subscription']['subscriptionUrl']
    assert urlparse(url).hostname==urlparse(access['site']).hostname
    for agent in ('v2rayTun/2.4.7','Happ/3.0.0','Streisand/1.6.75','v2rayNG/2.2.6','v2rayN/7.19.0'):
        r=client.get(url,headers={'User-Agent':agent});assert r.status_code==200,(agent,r.status_code)
        content=r.text.strip()
        try:decoded=base64.b64decode(content+'===').decode()
        except (ValueError,UnicodeError):decoded=content
        profiles=[s for s in decoded.splitlines() if s.startswith('vless://')]
        assert profiles,(agent,'expected VLESS subscription')
        for profile in profiles:
            options=parse_qs(urlparse(profile).query)
            assert options.get('security')==['reality'] and options.get('flow')==['xtls-rprx-vision']
            assert options.get('type') in (['tcp'],['raw'])
        result[agent]={'http_status':r.status_code,'profiles':len(profiles),'transport':'TCP/RAW + REALITY Vision'}
Path('staging/verification-followup-clients.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result,indent=2))
