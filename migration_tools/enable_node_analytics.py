"""Only logging changes; transport, credentials, routing and DNS remain identical."""
import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,'/opt/kingline-manager/app')
from remna_api import RemnaAPI
api=RemnaAPI();state=json.loads(Path('/opt/kingline-control/owner-profile.json').read_text())
p=api.call('config-profiles/'+state['profile_uuid']);candidate=copy.deepcopy(p['config'])
candidate['log']={**candidate.get('log',{}),'access':'','dnsLog':False,'loglevel':'warning'}
old=copy.deepcopy(p['config']);old.pop('log',None);check=copy.deepcopy(candidate);check.pop('log',None);assert old==check
folder=Path('/opt/kingline-staging/backups');stamp=datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
backup=folder/('profile-before-analytics-'+stamp+'.json');backup.write_text(json.dumps(p));backup.chmod(0o600)
path=folder/'analytics-candidate.json';path.write_text(json.dumps(candidate));path.chmod(0o600)
settings=json.loads(Path('/opt/kingline-manager/data/settings.json').read_text())
subprocess.run(['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL','-v',str(path)+':/tmp/config.json:ro','--entrypoint','/usr/local/bin/xray',settings['node_image'],'run','-test','-config','/tmp/config.json'],check=True,capture_output=True,timeout=30)
api.call('config-profiles','PATCH',{'uuid':p['uuid'],'name':p['name'],'config':candidate})
print('Only bounded Xray connection logging enabled; VPN connection settings unchanged.')
