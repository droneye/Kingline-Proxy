"""Core-only: copy the exact old RU domains, validate, apply and compensate failures."""
import copy,fcntl,json,os,subprocess,sys,time
from pathlib import Path
sys.path[:0]=['/opt/kingline-manager/app','/opt/kingline-control']
from remna_api import RemnaAPI
from node_manager.ru_policy import build
from node_manager.cover import certificate_lock
from node_manager.store import Store,atomic_json
from node_manager.probe import verify_tunnel
os.umask(0o077);root=Path('/opt/kingline-control');state=json.loads((root/'ru-egress.json').read_text());api=RemnaAPI()
domains=json.loads((root/'ru-domains.json').read_text())
assert domains and all(v.startswith('domain:') for v in domains)
preflight=json.loads((root/'ru-egress-preflight.json').read_text());assert len(preflight['checks'])==3 and all(v['https'] and v['udp_dns'] for v in preflight['checks'])
backup=Path('/opt/kingline-staging/run/sticky-ru-release-path').read_text().strip();backup=Path(backup)
store=Store('/opt/kingline-manager/data')
with store.db() as db:
    assert not db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone(),'node_job_in_progress'
heads=[n for n in store.nodes() if n.get('provisioned',True)]
nodes={n['uuid']:n for n in api.items('nodes','nodes')}
profiles={nodes[n['uuid']]['configProfile']['activeConfigProfileUuid'] for n in heads}
assert state['profile_uuid'] not in profiles
before={p:api.call('config-profiles/'+p) for p in profiles};changed=[]
(backup/'profiles-before-ru.json').write_text(json.dumps(before));(backup/'profiles-before-ru.json').chmod(0o600)
with certificate_lock('/opt/kingline-manager/data'):
    try:
        for uid,profile in before.items():
            candidate=build(profile['config'],state['vless_uuid'],domains)
            temp=backup/'ru-candidate.json';temp.write_text(json.dumps(candidate));temp.chmod(0o600)
            test=subprocess.run([str(root/'client-26.6.27/xray'),'run','-test','-config',str(temp)],capture_output=True,env={**os.environ,'XRAY_LOCATION_ASSET':str(root/'diagnostics/assets')})
            assert test.returncode==0,'xray_config_validation_failed'
            api.call('config-profiles','PATCH',{'uuid':uid,'config':candidate});changed.append(uid)
        # Check the actual public client path, not only the private RU socket.
        # Xray starts its first health probe after the profile reload.
        checks=[]
        for node in heads:
            ordinary=verify_tunnel(root,node['public_ip'])
            expected=node['public_ip'] if node['uuid'] in state.get('direct_fallback_nodes',[]) else state['public_ip']
            for attempt in range(4):
                try:
                    routed=verify_tunnel(root,node['public_ip'],exit_urls=('https://ipv4-internet.yandex.net/api/v0/ip',),expected_exit_ip=expected)
                    break
                except Exception:
                    if attempt==3:raise
                    time.sleep(10)
            checks.append({'node':node['public_ip'],'ordinary':ordinary,'russian_domains':routed})
            print(json.dumps({'node':node['public_ip'],'ordinary_exit':ordinary['exit_ip'],'russian_exit':routed['exit_ip']}),flush=True)
        atomic_json(root/'ru-routing-verification.json',{'at':time.time(),'checks':checks})
        state['enabled']=True;state['domains']=domains;atomic_json(root/'ru-egress.json',state)
    except BaseException:
        for uid in changed:api.call('config-profiles','PATCH',{'uuid':uid,'config':before[uid]['config']})
        raise
print(json.dumps({'applied_profiles':len(changed),'domain_rules':domains,'native_fallback':'direct if RU probe fails','user_credentials_changed':False}))
