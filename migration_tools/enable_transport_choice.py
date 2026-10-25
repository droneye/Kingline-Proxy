"""Server-only parallel XHTTP rollout; the existing RAW listener is preserved."""
import copy,json,os,secrets,sys,subprocess,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
sys.path[:0]=['/opt/kingline-manager/app','/opt/kingline-control']
from node_manager.manager import Manager
from node_manager.cover import Cover,certificate_lock
from node_manager.store import atomic_json
from node_manager.ssh import SSH
from node_manager.probe import probe_identity,verify_tunnel
from node_manager.xhttp import add_inbound,TAG
from remna_api import RemnaAPI
os.umask(0o077)
R=Path('/opt/kingline-manager/data');C=Path('/opt/kingline-control')
PI_TEST='''import types,json,sys,concurrent.futures
p=json.load(sys.stdin);w=types.ModuleType('candidate_worker');exec(p['worker'],w.__dict__)
def check(t):
 r=w.attempt(t,p['manifest']);return {'id':t['id'],'ok':r['ok'],'tcp':r.get('tcp',{}).get('ok'),'bytes':(r.get('large') or {}).get('bytes'),'error':r.get('error')}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:result=list(pool.map(check,p['targets']))
print(json.dumps(result))'''
START='''import pathlib,json,subprocess
p=json.load(__import__('sys').stdin);f=pathlib.Path('/opt/kingline-node/xhttp-canary.json');f.write_text(json.dumps(p['config']));f.chmod(0o600)
subprocess.run(['docker','run','-d','--rm','--name','kingline-xhttp-canary','--network','host','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','128m','-v',str(f)+':/tmp/config.json:ro','--entrypoint','/usr/local/bin/xray',p['image'],'run','-config','/tmp/config.json'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
print('{}')'''
CLEAN='''import pathlib,subprocess
subprocess.run(['docker','rm','-f','kingline-xhttp-canary'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
pathlib.Path('/opt/kingline-node/xhttp-canary.json').unlink(missing_ok=True)
print('{}')'''


def session(n):return SSH(R,{'ip':n['public_ip'],'ssh_port':n.get('ssh_port',22),'host_key_sha256':n['host_key_sha256']})

def rf(m,path):
 manifest=m.reviews.manifest();targets=[t|{'transport':'xhttp','xhttp_path':path} for t in manifest['targets'] if t['id'] in manifest['entry_ids']]
 s=SSH(R,json.loads((C/'ru-monitor.json').read_text())['probe'])
 try:result=json.loads(s.python(PI_TEST,{'worker':Path('/opt/kingline-manager/app/node_manager/ru_monitor_worker.py').read_text(),'manifest':manifest,'targets':targets},timeout=240))
 finally:s.close()
 assert sum(bool(r['ok']) for r in result)>=3,'RF_xhttp_failed'
 assert all(r['ok'] for r in result if r['tcp']),'RF_reachable_xhttp_failed'
 return result


def clean(m):
 for n in m.store.nodes():
  s=session(n)
  try:s.python(CLEAN)
  finally:s.close()


def prepare():
 m=Manager(R);m.vpn_config.installation_guard();m.standard();snap=m.network.snapshot()
 candidate=C/'transport-candidate.json'
 if not candidate.exists():atomic_json(candidate,{'path':'/channels/'+secrets.token_hex(16)+'/'})
 path=json.loads(candidate.read_text())['path'];cfg=m.settings['cover']|{'xhttp_path':path}
 primary={'log':{'loglevel':'none'},'inbounds':[],'outbounds':[{'tag':'direct','protocol':'freedom','settings':{'domainStrategy':'UseIPv4'}},{'tag':'blocked','protocol':'blackhole'}],'routing':{'rules':[{'type':'field','ip':['geoip:private'],'outboundTag':'blocked'}]},'dns':{'servers':['https://1.1.1.1/dns-query','https://1.0.0.1/dns-query'],'queryStrategy':'UseIPv4'}}
 config=add_inbound(primary,path);config['inbounds'][0]['settings']['clients']=[{'id':probe_identity(C,snap['owner'])}]
 subprocess.run(['systemctl','stop','kingline-cover-maintenance.timer'],check=True)
 clean(m)
 def setup(n):
  s=session(n)
  try:
   cover=Cover(cfg).deploy(s);s.python(START,{'config':config,'image':m.settings['node_image']})
   return {'id':n['uuid'],'cover':cover}
  finally:s.close()
 try:
  with certificate_lock(R),ThreadPoolExecutor(max_workers=3) as pool:prepared=list(pool.map(setup,m.store.nodes()))
  def check(n):
   raw=verify_tunnel(C,n['public_ip'],exit_urls=('https://api.ipify.org','https://icanhazip.com'))
   x=verify_tunnel(C,n['public_ip'],transport='xhttp',xhttp_path=path,exit_urls=('https://api.ipify.org','https://icanhazip.com'))
   return {'id':n['uuid'],'raw_https_udp':bool(raw['https'] and raw['udp_dns']),'xhttp_https_udp':bool(x['https'] and x['udp_dns'])}
  with ThreadPoolExecutor(max_workers=3) as pool:checks=list(pool.map(check,m.store.nodes()))
  report={'ok':True,'core':checks,'rf':rf(m,path)}
  atomic_json(C/'transport-prepared.json',{'config':cfg,'nodes':prepared,'revision':m.network.revision(snap)})
  atomic_json(C/'transport-canary-report.json',report);print(json.dumps(report),flush=True)
 finally:
  clean(m)
  subprocess.run(['systemctl','start','kingline-cover-maintenance.timer'],check=True)


def commit():
 m=Manager(R);a=RemnaAPI();prepared=json.loads((C/'transport-prepared.json').read_text());snap=m.network.snapshot();b=Path((C/'transport-release-path').read_text())
 assert json.loads((C/'transport-canary-report.json').read_text())['ok']
 assert m.network.revision(snap)==prepared['revision'],'configuration_changed'
 settings=copy.deepcopy(m.settings);nodes=m.store.nodes();policy_file=C/'transport-policy.json';path=prepared['config']['xhttp_path'];changed=[]
 services=['kingline-node-manager','kingline-site-admin','kingline-node-review-bot']
 subprocess.run(['systemctl','stop','kingline-cover-maintenance.timer','kingline-tunnel-health.timer','kingline-tunnel-health.service',*services],check=True)
 try:
  clean(m)
  pids={n['binding']['activeConfigProfileUuid'] for n in snap['nodes']}
  mapping={}
  for p in snap['profiles']:
   if p['uuid'] not in pids:continue
   tag=TAG if p['uuid']==snap['owner']['profile_uuid'] else 'KL_XH_'+p['uuid'].replace('-','')[:20]
   config=add_inbound(p['config'],path,tag)
   m.vpn_config.validate_core(next(n for n in snap['nodes'] if n['binding']['activeConfigProfileUuid']==p['uuid']),config)
   changed.append(p);a.call('config-profiles','PATCH',{'uuid':p['uuid'],'name':p['name'],'config':config})
   current=a.call('config-profiles/'+p['uuid']);mapping[p['uuid']]=next(i['uuid'] for i in current['inbounds'] if i['tag']==tag)
  for squad in snap['squads']:
   required=[mapping[p['uuid']] for p in changed if any(i['uuid'] in squad['inbounds'] for i in p['inbounds'])]
   if required:a.call('internal-squads','PATCH',{'uuid':squad['uuid'],'inbounds':list(dict.fromkeys(squad['inbounds']+required))})
  for node in snap['nodes']:
   binding=node['binding'];a.call('nodes','PATCH',{'uuid':node['uuid'],'configProfile':{'activeConfigProfileUuid':binding['activeConfigProfileUuid'],'activeInbounds':list(dict.fromkeys(binding['activeInbounds']+[mapping[binding['activeConfigProfileUuid']]]))}})
  time.sleep(3)
  core=[]
  for n in nodes:
   raw=verify_tunnel(C,n['public_ip'],exit_urls=('https://api.ipify.org','https://icanhazip.com'))
   x=verify_tunnel(C,n['public_ip'],transport='xhttp',xhttp_path=path,exit_urls=('https://api.ipify.org','https://icanhazip.com'))
   core.append({'id':n['uuid'],'raw_https_udp':bool(raw['https'] and raw['udp_dns']),'xhttp_https_udp':bool(x['https'] and x['udp_dns'])})
  result_rf=rf(m,path)
  # This rule only chooses response encoding; native HWID/expiry/block checks remain enabled.
  sub_settings=a.call('subscription-settings');atomic_json(b/'subscription-settings-at-commit.json',sub_settings)
  rules=sub_settings['responseRules'];rule={'name':'Kingline XHTTP encoding','enabled':True,'operator':'AND','conditions':[{'headerName':'x-kingline-transport','operator':'EQUALS','value':'XHTTP','caseSensitive':True}],'responseType':'XRAY_BASE64','responseModifications':{'ignoreServeJsonAtBaseSubscription':True}}
  rules['rules']=[r for r in rules['rules'] if r['name']!=rule['name']];rules['rules'].insert(1,rule)
  a.call('subscription-settings','PATCH',{'uuid':sub_settings['uuid'],'responseRules':rules})
  m.settings.update(cover=prepared['config'],xhttp={'path':path,'inbound_uuid':mapping[snap['owner']['profile_uuid']]})
  atomic_json(R/'settings.json',m.settings)
  covers={row['id']:row['cover'] for row in prepared['nodes']}
  for n in nodes:m.store.save_node(n|{'cover':covers[n['uuid']]})
  m.export_inventory()
  atomic_json(policy_file,{'enabled':True,'business_db':'/opt/kingline-staging/data/vpn_bot.db','sni':snap['owner']['sni'],'path':path,'aliases':{snap['owner']['user']['shortUuid']:json.loads((C/'ru-monitor.json').read_text())['owner_id']}})
  shutil=__import__('shutil');shutil.copy2('/opt/kingline-manager/app/subscription_gateway.py',C/'subscription_gateway.py');shutil.copy2('/opt/kingline-manager/app/subscription_modes.py',C/'subscription_modes.py')
  atomic_json(C/'transport-final-report.json',{'ok':True,'core':core,'rf':result_rf,'raw_preserved':True,'single_subscription':True})
  print(json.dumps({'ok':True,'core':core,'rf':result_rf}),flush=True)
 except Exception:
  for n in snap['nodes']:a.call('nodes','PATCH',{'uuid':n['uuid'],'configProfile':n['binding']})
  for squad in snap['squads']:a.call('internal-squads','PATCH',{'uuid':squad['uuid'],'inbounds':squad['inbounds']})
  for p in changed:a.call('config-profiles','PATCH',{'uuid':p['uuid'],'name':p['name'],'config':p['config']})
  if (b/'subscription-settings-at-commit.json').exists():a.call('subscription-settings','PATCH',{'uuid':json.loads((b/'subscription-settings-at-commit.json').read_text())['uuid'],'responseRules':json.loads((b/'subscription-settings-at-commit.json').read_text())['responseRules']})
  atomic_json(R/'settings.json',settings)
  for n in nodes:m.store.save_node(n)
  m.export_inventory();policy_file.unlink(missing_ok=True)
  raise
 finally:subprocess.run(['systemctl','start',*services,'kingline-tunnel-health.timer','kingline-cover-maintenance.timer'],check=True)
 subprocess.run(['systemctl','restart','kingline-subscriptions'],check=True)

if __name__=='__main__':{'prepare':prepare,'commit':commit}[sys.argv[1]]()
