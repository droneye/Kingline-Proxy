"""Server-only read checks. No announcements, account creation or outgoing messages."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

sys.path[:0]=['/opt/kingline-control','/opt/kingline-manager/app']
from remna_api import RemnaAPI
from subscription_modes import announcement

report={}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(url,headers=None):
    try:
        with opener.open(urllib.request.Request(url,headers=headers or {}),timeout=20) as r:
            return r.status,r.read(),{k.lower():v for k,v in r.headers.items()}
    except urllib.error.HTTPError as e:return e.code,b'',{}
    except OSError as e:return 0,b'',{'error':type(e).__name__}

status,html,_=get('https://test.edge.kingline.online/')
report['site_http']=status
report['health_http']=get('https://test.edge.kingline.online/api/health')[0]
report['anonymous_announcement_http']=get('https://test.edge.kingline.online/api/admin/messages/announcement')[0]
report['anonymous_documentation_http']=get('https://test.edge.kingline.online/api/admin/documentation')[0]
doc=Path('/opt/kingline-staging/docs/service.md').read_bytes()
image_doc=subprocess.check_output(['docker','compose','exec','-T','api','python','-c',
    'import hashlib;from pathlib import Path;print(hashlib.sha256(Path("/app/docs/service.md").read_bytes()).hexdigest())'],cwd='/opt/kingline-staging').decode().strip()
report['documentation_matches_image']=hashlib.sha256(doc).hexdigest()==image_doc
services=['kingline-subscriptions','kingline-node-manager','kingline-site-admin','kingline-node-review-bot']
report['services_active']=all(subprocess.run(['systemctl','is-active','--quiet',name]).returncode==0 for name in services)
probe_id=json.loads(Path('/opt/kingline-control/probe-user.json').read_text())['id']
probe=RemnaAPI().call('users/'+str(probe_id))
headers={'User-Agent':'v2raytun/2.4.7','Accept':'*/*','X-Forwarded-Proto':'https','X-Forwarded-For':'127.0.0.1'}
status,body,native_headers=get('http://127.0.0.1:3010/'+probe['shortUuid'],headers)
gateway_status,gateway_body,gateway_headers=get('https://connect.kingline.online/'+probe['shortUuid'],headers)
report['technical_subscription_native_http']=status
report['technical_subscription_gateway_http']=gateway_status
report['technical_subscription_body_preserved']=status==gateway_status==200 and bool(body) and body==gateway_body
report['native_announcement_preserved']=native_headers.get('announce')==gateway_headers.get('announce')
expected_announcement=announcement(Path('/opt/kingline-control'))
report['announcement_matches_saved_state']=(
    report['native_announcement_preserved'] if expected_announcement is None else
    gateway_headers.get('announce')==(expected_announcement or None) and 'announce-url' not in gateway_headers)
report['empty_announcement_headers_absent']=(
    'announce' not in gateway_headers and 'announce-url' not in gateway_headers) if expected_announcement=='' else None
with opener.open(urllib.request.Request('https://connect.kingline.online/'+probe['shortUuid'],headers=headers,method='HEAD'),timeout=20) as response:
    head_headers={k.lower():v for k,v in response.headers.items()}
    report['head_matches_get_metadata']=response.status==200 and all(head_headers.get(k)==gateway_headers.get(k) for k in ('announce','announce-url'))
report['technical_subscription_profiles_present']=False
if gateway_status==200:
    try:
        text=gateway_body if gateway_body.startswith(b'vless://') else base64.b64decode(gateway_body,validate=True)
        report['technical_subscription_profiles_present']=any(line.startswith(b'vless://') for line in text.splitlines())
    except ValueError:pass
Path('/opt/kingline-control/messages-verification.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
assert report['site_http']==report['health_http']==200
assert report['anonymous_announcement_http']==report['anonymous_documentation_http']==401
assert all(report[k] for k in ('documentation_matches_image','services_active','technical_subscription_body_preserved','technical_subscription_profiles_present','announcement_matches_saved_state','head_matches_get_metadata'))
