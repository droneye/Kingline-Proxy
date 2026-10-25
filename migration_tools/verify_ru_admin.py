"""Read-only deployed release checks; VPN requests use authorized SERVICE_PROBE."""
import hashlib,json,subprocess,sys,time,urllib.request,urllib.error
from pathlib import Path
sys.path[:0]=['/opt/kingline-manager/app','/opt/kingline-control']
from node_manager.store import Store
from node_manager.ru_egress import RUEgress
from node_manager.probe import verify_tunnel
from node_manager.ssh import SSH
from remna_api import RemnaAPI
root=Path('/opt/kingline-manager/data');control=Path('/opt/kingline-control')
state=json.loads((control/'ru-egress.json').read_text());gateway=RUEgress();api=RemnaAPI(root/'panel.token')
release=Path(Path('/opt/kingline-staging/run/sticky-ru-release-path').read_text().strip())
before=json.loads((release/'profiles-before-ru.json').read_text())
assert all(api.call('config-profiles/'+uid)['config']['inbounds']==profile['config']['inbounds'] for uid,profile in before.items()),'public_inbounds_changed'
checks=[]
for node in Store(root).nodes():
 if not node.get('provisioned',True):continue
 ordinary=verify_tunnel(control,node['public_ip'])
 expected=state['public_ip'] if gateway.selected(node,state) else node['public_ip']
 routed=verify_tunnel(control,node['public_ip'],exit_urls=('https://ipv4-internet.yandex.net/api/v0/ip',),expected_exit_ip=expected)
 check={'ip':node['public_ip'],'ordinary_https':ordinary['exit_ip']==node['public_ip'],'ru_exit_correct':routed['exit_ip']==expected,'udp_dns':ordinary['udp_dns'] and routed['udp_dns']}
 checks.append(check);print(json.dumps(check),flush=True)
ssh=SSH(root,state['pi'])
try:
 legacy=json.loads(ssh.python('''from pathlib import Path
import hashlib,json,subprocess
p=Path('/opt/kingline-ru-node/backups/before-20261023/summary.json')
old=json.loads(p.read_text())
config=Path('/usr/local/x-ui/bin/config.json')
print(json.dumps({'active':subprocess.check_output(['systemctl','is-active','x-ui'],text=True).strip()=='active','config_unchanged':hashlib.sha256(config.read_bytes()).hexdigest()==old['config_sha256']}))
''',{}))
finally:ssh.close()
assert legacy['active'] and legacy['config_unchanged'],'legacy_pi_changed'
report={'at':time.time(),'public_inbounds_unchanged':True,'checks':checks,'legacy_pi':legacy,'actual_new_ru_installation':False,'current_generation':state.get('generation',0)}
(control/'ru-admin-verification.json').write_text(json.dumps(report));print(json.dumps({'legacy_pi':legacy,'public_inbounds_unchanged':True,'checks':len(checks)}),flush=True)
