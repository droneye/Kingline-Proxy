"""Create a private monitoring user; never change an existing subscriber's UUID."""
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import secrets
import sys
sys.path.insert(0,'/opt/kingline-control')
from remna_api import RemnaAPI
os.umask(0o077)
root=Path('/opt/kingline-control');path=root/'probe-user.json'
assert not path.exists(),'probe_identity_already_exists'
state=json.loads((root/'owner-profile.json').read_text())
a=RemnaAPI();owner=a.call('users/'+str(state['user']['id']))
squads=[s['uuid'] if isinstance(s,dict) else s for s in owner['activeInternalSquads']]
user=a.call('users','POST',{'username':'service_probe_'+secrets.token_hex(5),'tag':'SERVICE_PROBE','hwidDeviceLimit':0,
    'description':'Private automated tunnel health checks. Excluded from customer analytics.',
    'expireAt':(datetime.now(timezone.utc)+timedelta(days=3650)).isoformat(),'activeInternalSquads':squads,
    'trafficLimitBytes':0,'trafficLimitStrategy':'NO_RESET'})
temp=path.with_suffix('.tmp')
temp.write_text(json.dumps({'id':user['id'],'vlessUuid':user['vlessUuid']}));temp.chmod(0o600);temp.replace(path)
print('Dedicated monitoring identity created; subscriber credentials unchanged')
