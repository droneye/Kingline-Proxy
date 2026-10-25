"""Run on core only. Backup settings there, test an isolated subscription, then keep 3 slots."""
import base64
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sys
import urllib.error
import urllib.request
sys.path.insert(0,'/opt/kingline-control')
from remna_api import RemnaAPI

os.umask(0o077)
a=RemnaAPI()
release=Path('/opt/kingline-staging/run/observability-release-path').read_text().strip()
all_users=a.items('users?size=100&start=0','users')
assert all(u.get('tag') in {'STAGING','OWNER_TEST','SERVICE_PROBE'} for u in all_users),'unexpected_user_scope'
users=[u for u in all_users if u.get('tag') in {'STAGING','OWNER_TEST'}]
current_settings=a.call('subscription-settings')
settings=current_settings['hwidSettings']
settings_uuid=current_settings['uuid']
backup={'hwidSettings':settings,'users':[{'id':u['id'],'hwidDeviceLimit':u.get('hwidDeviceLimit')} for u in users]}
path=Path(release)/'device-limits-before.json'
if path.exists():
    assert not (Path(release)/'device-limit-verification.json').exists(),'already_verified'
    backup=json.loads(path.read_text());settings=backup['hwidSettings']
    assert {u['id'] for u in users}=={u['id'] for u in backup['users']},'users_changed_since_backup'
else:
    path.write_text(json.dumps(backup));path.chmod(0o600)
changed=[];qa=None
try:
    for u in users:
        a.call('users','PATCH',{'id':u['id'],'hwidDeviceLimit':3});changed.append(u['id'])
    a.call('subscription-settings','PATCH',{'uuid':settings_uuid,'hwidSettings':{'enabled':True,'fallbackDeviceLimit':3,'maxDevicesAnnounce':'Подписка доступна на 3 устройствах. Удалить старое устройство поможет служба поддержки.'}})
    squad=next(u['activeInternalSquads'] for u in users if u.get('activeInternalSquads'))
    squad=[s['uuid'] if isinstance(s,dict) else s for s in squad]
    qa=a.call('users','POST',{'username':'qa_devices_'+secrets.token_hex(6),'tag':'QA_DEVICE_LIMIT','hwidDeviceLimit':3,
        'expireAt':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(), 'trafficLimitBytes':1048576,'trafficLimitStrategy':'NO_RESET','activeInternalSquads':squad})
    prefix='KinglineQA'+secrets.token_hex(5)
    def fetch(number):
        headers={'User-Agent':'v2raytun/2.4.7','x-hwid':prefix+str(number),'x-device-os':'iOS','x-device-model':'QA'}
        request=urllib.request.Request(qa['subscriptionUrl'],headers=headers)
        try:
            with urllib.request.urlopen(request,timeout=20) as r: body=r.read(2000000);response=dict((k.lower(),v) for k,v in r.headers.items())
        except urllib.error.HTTPError as exc:
            body=exc.read(2000000);response=dict((k.lower(),v) for k,v in exc.headers.items())
        try:decoded=base64.b64decode(body+ b'='*((-len(body))%4)).decode(errors='replace')
        except ValueError:decoded=body.decode(errors='replace')
        contains_key=qa['vlessUuid'] in decoded or qa['vlessUuid'] in body.decode(errors='replace')
        return contains_key,response
    accepted=[]
    for number in (1,2,3):
        usable,headers=fetch(number);assert usable, 'first_three_rejected';accepted.append(usable)
    usable,headers=fetch(4)
    assert not usable and (headers.get('x-hwid-max-devices-reached')=='true' or headers.get('x-hwid-limit')=='true'),'fourth_not_rejected'
    assert a.call('hwid/devices/'+str(qa['id']))['total']==3
    assert fetch(1)[0], 'existing_device_lost_access'
    result={'first_three_allowed':all(accepted),'fourth_blocked':True,'existing_device_can_refresh':True,'applied_users':len(changed)}
    (Path(release)/'device-limit-verification.json').write_text(json.dumps(result))
    print(json.dumps(result))
except BaseException:
    a.call('subscription-settings','PATCH',{'uuid':settings_uuid,'hwidSettings':settings})
    for u in backup['users']:
        if u['id'] in changed:a.call('users','PATCH',u)
    raise
finally:
    if qa:a.call('users/'+str(qa['id']),'DELETE')
