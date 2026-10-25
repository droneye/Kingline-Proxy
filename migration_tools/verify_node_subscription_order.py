"""Read only: subscription order across clients. Never print/store links or keys."""
import base64
import json
from pathlib import Path
import shlex
import subprocess
from urllib.parse import urlparse,unquote
import httpx

source='''import json,urllib.request
from pathlib import Path
r=urllib.request.Request('http://127.0.0.1:8090/v1/node-network',headers={'Authorization':'Bearer '+Path('/opt/kingline-manager/data/admin.token').read_text().strip()})
with urllib.request.urlopen(r) as response:data=json.load(response)
print(json.dumps([k['label'] for k in data['keys']]))
'''
expected=json.loads(subprocess.check_output(['ssh','root@198.51.100.10','/opt/kingline-manager/venv/bin/python -c '+shlex.quote(source)]))
access=json.loads(Path('private/staging-test-access.json').read_text());report={}
with httpx.Client(timeout=25,headers={'Origin':access['site'],'X-Kingline-Request':'fetch'}) as client:
    login=client.post(access['site']+'/api/auth/login',json={'email':access['test_email'],'password':access['test_password']})
    assert login.status_code==200
    link=login.json()['subscription']['subscriptionUrl']
    for agent in ('v2rayTun/2.4.7','Happ/3.0.0','Streisand/1.6.75','v2rayNG/2.2.6','v2rayN/7.19.0'):
        response=client.get(link,headers={'User-Agent':agent});assert response.status_code==200
        value=response.text.strip()
        try:value=base64.b64decode(value+'===').decode()
        except (ValueError,UnicodeError):pass
        names=[unquote(urlparse(line).fragment) for line in value.splitlines() if line.startswith('vless://')]
        assert names==expected,'subscription_order_does_not_match_host_order'
        report[agent]={'profiles':len(names),'order_matches_admin':True}
Path('staging/verification-node-subscription-order.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
