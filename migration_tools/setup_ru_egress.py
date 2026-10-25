"""Core-only preparation; customer routing is applied separately after canaries."""
import json,os,sys,time,subprocess
from pathlib import Path
from datetime import datetime,timedelta,timezone
sys.path[:0]=['/opt/kingline-manager/app','/opt/kingline-control']
from remna_api import RemnaAPI
from node_manager.ssh import SSH
from node_manager.store import Store,atomic_json
os.umask(0o077)
root=Path('/opt/kingline-control');state_file=root/'ru-egress.json'
remote=(root/'ru_mesh_remote.py').read_text()
pi_request={'ip':'10.79.0.2','ssh_port':22,'host_key_sha256':'SHA256:0OpK1b0GqTwfUQAx0bp5NWFG7fbfCPL4t1FMi00iivY'}
data=json.loads(state_file.read_text()) if state_file.exists() else {'enabled':False,'pi':pi_request,'mesh_peers':[]}
api=RemnaAPI()
def ssh(request):return SSH('/opt/kingline-manager/data',request)
def invoke(request,payload,timeout=90):
    s=ssh(request)
    try:return json.loads(s.python(remote,payload,timeout=timeout))
    finally:s.close()
pi=ssh(pi_request)
try:
    # Online backup stays on Raspberry; existing service configuration is untouched.
    pi.python('''import sqlite3,os,hashlib,json,subprocess
from pathlib import Path
os.umask(0o077)
p=Path('/opt/kingline-ru-node/backups/before-20261023');p.mkdir(parents=True,exist_ok=True)
if not (p/'x-ui.db').exists():
 with sqlite3.connect('file:/etc/x-ui/x-ui.db?mode=ro',uri=True) as source,sqlite3.connect(p/'x-ui.db') as dest:source.backup(dest)
config=Path('/usr/local/x-ui/bin/config.json')
summary={'x_ui_active':subprocess.check_output(['systemctl','is-active','x-ui'],text=True).strip(),'config_sha256':hashlib.sha256(config.read_bytes()).hexdigest() if config.exists() else None}
if not (p/'summary.json').exists():(p/'summary.json').write_text(json.dumps(summary))
print(json.dumps({'backup':True,'x_ui_active':summary['x_ui_active']}))''')
finally:pi.close()
nodes=[n for n in Store('/opt/kingline-manager/data').nodes() if n.get('provisioned',True) and n['uuid'] not in data.get('direct_fallback_nodes',[])]
peers=[]
for n in nodes:
    request={'ip':n['public_ip'],'ssh_port':n.get('ssh_port',22),'host_key_sha256':n.get('host_key_sha256')}
    key=invoke(request,{'mode':'key'})['public_key']
    peers.append({'uuid':n['uuid'],'public_ip':n['public_ip'],'public_key':key,'address':'10.78.0.'+n['management_ip'].split('.')[-1],'request':request})
invoke(pi_request,{'mode':'mesh','role':'pi','address':'10.78.0.1','peers':peers})
for p in peers:invoke(p['request'],{'mode':'mesh','role':'head','address':p['address'],'peers':[{'address':'10.78.0.1','public_key':'gHjJzI6KkedDabm6OnYRZDt2jx530c6o+XPadMap+UI='}]})
data['mesh_peers']=peers;atomic_json(state_file,data)
if not data.get('profile_uuid'):
    profile=api.call('config-profiles','POST',{'name':'Kingline RU egress private','config':{
      'log':{'loglevel':'warning','access':'none'},
      'inbounds':[{'tag':'KINGLINE_RU_PRIVATE','listen':'0.0.0.0','port':15443,'protocol':'vless','settings':{'clients':[],'decryption':'none'},'streamSettings':{'network':'tcp','security':'none'},'sniffing':{'enabled':True,'destOverride':['http','tls','quic'],'routeOnly':True}}],
      'outbounds':[{'tag':'direct','protocol':'freedom','settings':{'domainStrategy':'UseIPv4'}},{'tag':'blocked','protocol':'blackhole'}],
      'routing':{'domainStrategy':'IPIfNonMatch','rules':[{'type':'field','ip':['geoip:private'],'outboundTag':'blocked'}]},
      # Home DNS returns synthetic 198.18/15 addresses for some sites. Resolve
      # inside this isolated service over HTTPS, without changing the old setup.
      'dns':{'servers':['https://1.1.1.1/dns-query','https://1.0.0.1/dns-query'],'queryStrategy':'UseIPv4'},'stats':{}}})
    data.update(profile_uuid=profile['uuid'],inbound_uuid=profile['inbounds'][0]['uuid']);atomic_json(state_file,data)
if not data.get('squad_uuid'):
    squad=api.call('internal-squads','POST',{'name':'Kingline RU routing service','inbounds':[data['inbound_uuid']]});data['squad_uuid']=squad['uuid'];atomic_json(state_file,data)
if not data.get('user_id'):
    user=api.call('users','POST',{'username':'kingline_ru_router','tag':'RU_ROUTER','hwidDeviceLimit':0,'expireAt':(datetime.now(timezone.utc)+timedelta(days=3650)).isoformat(),'trafficLimitBytes':0,'trafficLimitStrategy':'NO_RESET','activeInternalSquads':[data['squad_uuid']]})
    data['user_id']=user['id'];data['vless_uuid']=user['vlessUuid'];atomic_json(state_file,data)
image=json.loads(Path('/opt/kingline-manager/data/settings.json').read_text())['node_image']
invoke(pi_request,{'mode':'container','image':image,'secret':api.call('keygen')['secretKey']},timeout=800)
if not data.get('node_uuid'):
    node=api.call('nodes','POST',{'name':'RU-Raspberry-Egress','address':'10.79.0.2','port':2222,'countryCode':'RU','isTrafficTrackingActive':False,'configProfile':{'activeConfigProfileUuid':data['profile_uuid'],'activeInbounds':[data['inbound_uuid']]}})
    data['node_uuid']=node['uuid'];atomic_json(state_file,data)
for _ in range(90):
    node=api.call('nodes/'+data['node_uuid'])
    if node['isConnected'] and node.get('xrayUptime',0)>0:break
    time.sleep(2)
else:raise RuntimeError('ru_node_not_ready')
print(json.dumps({'prepared':True,'mesh_heads':len(peers),'remnawave_connected':True,'old_xui_preserved':True,'customer_routing_changed':False}))
