"""Read existing owner metadata in memory; print booleans only, never names/URLs."""
import base64
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

sys.path[:0]=['/opt/kingline-control']
from subscription_modes import profile_title
from remna_api import RemnaAPI

root=Path('/opt/kingline-control')
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
user=json.loads((root/'owner-profile.json').read_text())['user']
short=user['shortUuid']
expected=profile_title(root,short)
report={'owner_subscription_mapped':expected is not None}
if expected:
    title=base64.b64decode(expected.removeprefix('base64:')).decode()
    report['owner_has_named_title']=title.startswith('Kingline VPN · ') and len(title)>15
    api=RemnaAPI()
    devices=api.call('hwid/devices/'+str(user['id']))['devices']
    before={device['hwid'] for device in devices}
    assert before,'existing_device_required'
    device=devices[0]
    headers={'User-Agent':device.get('userAgent') or 'v2raytun/2.4.7','Accept':'*/*','X-Hwid':device['hwid']}
    for field,header in (('platform','X-Device-OS'),('osVersion','X-Ver-OS'),('deviceModel','X-Device-Model')):
        if device.get(field):headers[header]=str(device[field])[:512]
    for method in ('GET','HEAD'):
        try:
            with opener.open(urllib.request.Request('https://connect.kingline.online/'+short,headers=headers,method=method),timeout=20) as response:
                refused=any(response.headers.get(k,'').lower()=='true' for k in ('x-hwid-not-supported','x-hwid-max-devices-reached','x-hwid-limit'))
                report[method.lower()+'_http']=response.status
                report[method.lower()+'_hwid_refused']=refused
                report[method.lower()+'_title_matches']=response.status==200 and not refused and response.headers.get('profile-title')==expected
                if method=='GET':
                    body=response.read(4_000_001)
                    try:
                        text=body if body.startswith(b'vless://') else base64.b64decode(body,validate=True)
                        report['profiles_present']=any(line.startswith(b'vless://') for line in text.splitlines())
                    except ValueError:report['profiles_present']=False
        except urllib.error.HTTPError as error:
            report[method.lower()+'_http']=error.code
        except OSError as error:
            report[method.lower()+'_error']=type(error).__name__
    report['registered_devices_unchanged']=before=={device['hwid'] for device in api.call('hwid/devices/'+str(user['id']))['devices']}
(root/'profile-title-verification.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
assert report['owner_subscription_mapped']
assert report.get('get_title_matches') and report.get('head_title_matches') and report.get('profiles_present')
assert report.get('registered_devices_unchanged')
