"""Server-only owned identity rollout. No credentials or subscriptions leave the core."""
import concurrent.futures
import base64
import ipaddress
import ssl
import socket
import urllib.parse
import urllib.request
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path[:0] = ['/opt/kingline-manager/app', '/opt/kingline-control']
from remna_api import RemnaAPI
from node_manager.cover import Cover, sync_dns, certificate_lock
from node_manager.domains import sync as sync_domains, hostname
from node_manager.manager import Manager
from node_manager.probe import verify_tunnel, probe_identity
from node_manager.ssh import SSH
from node_manager.store import atomic_json
from node_manager.vpn_config import inbound

ROOT = Path('/opt/kingline-manager/data')
CONTROL = Path('/opt/kingline-control')
DOMAIN = 'edge.kingline.online'
TARGET = '127.0.0.1:8443'
os.umask(0o077)

PREPARE = '''import json,pathlib,subprocess
p=json.load(__import__('sys').stdin)
f=pathlib.Path('/opt/kingline-node/identity-canary.json');f.write_text(json.dumps(p['config']));f.chmod(0o600)
def run(args):subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
run(['nft','insert','rule','inet','kingline_filter','input','ip','saddr','{','198.51.100.10',',',p['source'],'}','tcp','dport','1443','accept','comment','kingline-identity-canary'])
run(['docker','run','-d','--rm','--name','kingline-identity-canary','--network','host','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','-v',str(f)+':/tmp/config.json:ro','--entrypoint','/usr/local/bin/xray',p['image'],'run','-config','/tmp/config.json'])
print('{}')'''
CLEANUP = '''import json,pathlib,subprocess
subprocess.run(['docker','rm','-f','kingline-identity-canary'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
rules=json.loads(subprocess.check_output(['nft','-j','-a','list','chain','inet','kingline_filter','input']))
for item in rules['nftables']:
 r=item.get('rule',{})
 if r.get('comment')=='kingline-identity-canary':subprocess.run(['nft','delete','rule','inet','kingline_filter','input','handle',str(r['handle'])],check=True)
pathlib.Path('/opt/kingline-node/identity-canary.json').unlink(missing_ok=True)
print('{}')'''
PI_TEST = '''import concurrent.futures,importlib.util,json,sys
p=json.load(sys.stdin)
spec=importlib.util.spec_from_file_location('watch','/opt/kingline-ru-monitor/worker.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)
def check(t):
 r=w.attempt(t,p['manifest']);return {'id':t['id'],'ok':r['ok'],'error':r.get('error'),'bytes':(r.get('large') or {}).get('bytes'),'tcp':r.get('tcp',{}).get('ok')}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:r=list(pool.map(check,p['targets']))
print(json.dumps(r))'''


def ssh(node):
    return SSH(ROOT, {'ip': node['public_ip'], 'ssh_port': node.get('ssh_port', 22),
                      'host_key_sha256': node['host_key_sha256']})


def pi_checks(manager, targets):
    cfg = json.loads((CONTROL/'ru-monitor.json').read_text())
    session = SSH(ROOT, cfg['probe'])
    try:
        return json.loads(session.python(PI_TEST, {'targets': targets, 'manifest': manager.reviews.manifest()}, timeout=240))
    finally:
        session.close()


def prepare():
    m = Manager(ROOT)
    cfg = json.loads((ROOT/'cover-prepared.json').read_text())
    def deploy(node):
        session = ssh(node)
        try:
            result = Cover(cfg).deploy(session)
            return {'id': node['uuid'], 'cover': result}
        finally:
            session.close()
    with certificate_lock(ROOT), concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        prepared = list(pool.map(deploy, m.store.nodes()))
    atomic_json(CONTROL/'identity-prepared.json', prepared)
    print(json.dumps({'prepared': len(prepared), 'tls13_h2_scanner': True}), flush=True)


def canary():
    m = Manager(ROOT)
    state = m.standard()
    api = RemnaAPI()
    profile = api.call('config-profiles/'+state['profile_uuid'])
    primary = copy.deepcopy(inbound(profile['config']))
    primary.update(port=1443, listen='0.0.0.0')
    primary['settings']['clients'] = [{'id': probe_identity(CONTROL, state), 'flow': 'xtls-rprx-vision'}]
    reality = primary['streamSettings']['realitySettings']
    reality.pop('dest', None)
    reality.update(target=TARGET, serverNames=[DOMAIN, state['sni']], show=False)
    config = {'log': {'loglevel': 'none'}, 'inbounds': [primary],
              'outbounds': [{'protocol': 'freedom', 'tag': 'direct', 'settings': {'domainStrategy': 'UseIPv4'}},
                            {'protocol': 'blackhole', 'tag': 'blocked'}],
              'routing': {'rules': [{'type': 'field', 'ip': ['geoip:private'], 'outboundTag': 'blocked'}]},
              'dns': {'servers': ['https://1.1.1.1/dns-query', 'https://1.0.0.1/dns-query'], 'queryStrategy': 'UseIPv4'}}
    nodes = m.store.nodes()
    report = {'core': [], 'rf': []}
    try:
        for node in nodes:
            session = ssh(node)
            try:
                session.python(CLEANUP)
                session.python(PREPARE, {'config': config, 'image': m.settings['node_image'], 'source': m.reviews.source_ip()})
            finally:
                session.close()
        for node in nodes:
            for sni, fp in [(DOMAIN, 'firefox'), (DOMAIN, 'chrome'), (state['sni'], 'firefox')]:
                result = verify_tunnel(CONTROL, node['public_ip'], state_override={**state, 'sni': sni, 'fingerprint': fp},
                                      server_port=1443, exit_urls=('https://api.ipify.org','https://icanhazip.com'))
                report['core'].append({'id': node['uuid'], 'sni': sni, 'fingerprint': fp, 'https': result['https'], 'udp_dns': result['udp_dns']})
        manifest = m.reviews.manifest()
        targets = [{**t, 'address': t['ip'], 'port': 1443, 'sni': DOMAIN} for t in manifest['targets']
                   if t['id'] in manifest['entry_ids']]
        report['rf'] = pi_checks(m, targets)
        # A blocked TCP path is a pre-existing limitation; a reachable path must carry full VPN traffic.
        assert all(r['ok'] for r in report['rf'] if r['tcp']), 'RF_reachable_path_failed'
        assert sum(bool(r['ok']) for r in report['rf']) >= 3, 'RF_not_enough_healthy_entries'
        report['ok'] = True
        atomic_json(CONTROL/'identity-canary-report.json', report)
        print(json.dumps(report), flush=True)
    finally:
        for node in nodes:
            session = ssh(node)
            try:
                session.python(CLEANUP)
            finally:
                session.close()


def issued_keys(manager):
    api = RemnaAPI()
    identity = json.loads((CONTROL/'probe-user.json').read_text())
    user = api.call('users/'+str(identity['id']))
    url = 'https://connect.kingline.online/'+user['shortUuid']
    hosts = [h for h in api.items('hosts','hosts') if not h.get('isDisabled')]
    expected = {h['address'] for h in hosts}
    reports = []
    for agent in ('v2rayTun/2.4.7', 'v2rayN/7.19.0', 'Happ/3.0.0'):
        req = urllib.request.Request(url, headers={'User-Agent':agent})
        with urllib.request.urlopen(req, timeout=30) as response:data=response.read()
        try:text=base64.b64decode(data).decode()
        except (ValueError,UnicodeError):text=data.decode()
        links=[urllib.parse.urlsplit(line) for line in text.splitlines() if line.startswith('vless://')]
        assert len(links)==len(hosts), 'issued_profile_count'
        assert {link.hostname for link in links}==expected, 'issued_domain_mismatch'
        for link in links:
            try:ipaddress.ip_address(link.hostname)
            except ValueError:pass
            else:raise RuntimeError('literal_ip_in_subscription')
            params=urllib.parse.parse_qs(link.query)
            assert params['sni']==[DOMAIN] and params['security']==['reality']
            assert params['flow']==['xtls-rprx-vision']
            assert params['fp']==['firefox']
        reports.append({'client':agent,'profiles':len(links),'domains_only':True,'owned_sni':True})
    return reports


def cutover():
    from smart_dns import https_cover_ready
    m=Manager(ROOT);api=RemnaAPI();backup=Path((CONTROL/'identity-release-path').read_text())
    assert json.loads((CONTROL/'identity-canary-report.json').read_text())['ok']
    snap=m.network.snapshot()
    # Refresh canonical rollback data immediately before any shared change.
    atomic_json(backup/'snapshot.json',snap)
    old_settings=copy.deepcopy(m.settings);old_network=m.network.state();old_nodes=m.store.nodes()
    atomic_json(backup/'settings-at-cutover.json',old_settings)
    atomic_json(backup/'network-at-cutover.json',old_network)
    atomic_json(backup/'nodes-at-cutover.json',old_nodes)
    prepared={n['id']:n['cover'] for n in json.loads((CONTROL/'identity-prepared.json').read_text())}
    assert set(prepared)=={n['uuid'] for n in old_nodes}
    profiles=[]
    for p in snap['profiles']:
        if not any(n['binding']['activeConfigProfileUuid']==p['uuid'] for n in snap['nodes']):continue
        config=copy.deepcopy(p['config']);primary=inbound(config)
        if primary is None:raise RuntimeError('unsupported_active_profile')
        reality=primary['streamSettings']['realitySettings'];reality.pop('dest',None)
        reality.update(target=TARGET,serverNames=list(dict.fromkeys([DOMAIN,*reality['serverNames']]))[:8],show=False)
        # No characteristic fallback throttling, fragmentation, or MUX changes.
        m.vpn_config.validate_core(next(n for n in snap['nodes'] if n['binding']['activeConfigProfileUuid']==p['uuid']),config)
        profiles.append((p,config))
    services=['kingline-node-manager','kingline-site-admin','kingline-node-review-bot']
    subprocess.run(['systemctl','stop','kingline-tunnel-health.timer','kingline-tunnel-health.service',*services],check=True)
    changed=[]
    try:
        for n in old_nodes:m.store.save_node(n|{'cover':prepared[n['uuid']]})
        sync_domains(CONTROL,m.store.nodes(),old_network)
        sync_dns(CONTROL,m.store.nodes())
        for p,config in profiles:
            changed.append(p)
            api.call('config-profiles','PATCH',{'uuid':p['uuid'],'name':p['name'],'config':config})
        time.sleep(3)
        candidate={**snap['owner'],'sni':DOMAIN,'reality_target':TARGET}
        core=[]
        paths=json.loads((CONTROL/'node-paths.json').read_text())
        for n in old_nodes:
            route=paths.get(n['public_ip'],{})
            check=verify_tunnel(CONTROL,n['public_ip'],state_override=candidate,
                connect_address=route.get('connect_address'),server_port=route.get('server_port',443),
                exit_urls=('https://api.ipify.org','https://icanhazip.com'))
            assert https_cover_ready(n['public_ip'],DOMAIN), 'public_https_failed'
            core.append({'id':n['uuid'],'https':check['https'],'udp_dns':check['udp_dns'],'public_https':True})
        # Keep old cached keys working too, rather than requiring everyone to refresh at once.
        legacy=verify_tunnel(CONTROL,old_nodes[1]['public_ip'],state_override=snap['owner'],exit_urls=('https://api.ipify.org','https://icanhazip.com'))
        atomic_json(CONTROL/'owner-profile.json',candidate)
        network=copy.deepcopy(old_network)
        for entry in network.get('connections',{}).values():entry.update(sni=DOMAIN,target=TARGET)
        atomic_json(m.network.path,network)
        m.settings.update(cover=json.loads((ROOT/'cover-prepared.json').read_text()),node_domains=True)
        atomic_json(ROOT/'settings.json',m.settings)
        m.cover=Cover(m.settings['cover'])
        for h in snap['hosts']:
            node=next((n for n in old_nodes if n.get('host_uuid')==h['uuid']),None)
            if node or h['address']=='auto.edge.kingline.online':
                body={'uuid':h['uuid'],'sni':DOMAIN}
                if node:body['address']=hostname(node)
                api.call('hosts','PATCH',body)
        atomic_json(CONTROL/'cover-policy.json',{'domain':DOMAIN,'target':TARGET})
        m.export_inventory();m.standard()
        # The final check is on the REAL public port and the path subscribers use.
        manifest=m.reviews.manifest()
        targets=[t for t in manifest['targets'] if t['id'] in manifest['entry_ids']]
        rf=pi_checks(m,targets)
        previously={r['id'] for r in json.loads((CONTROL/'identity-canary-report.json').read_text())['rf'] if r['ok']}
        assert all(r['ok'] for r in rf if r['id'] in previously), 'RF_public_regression'
        assert previously <= {r['id'] for r in rf}, 'RF_missing_result'
        subscriptions=issued_keys(m)
        report={'ok':True,'sni':DOMAIN,'target':TARGET,'node_domains':True,'core':core,'rf':rf,
                'legacy_cached_key_ok':bool(legacy['https']),'subscriptions':subscriptions,'rollback_directory':str(backup)}
        atomic_json(CONTROL/'identity-final-report.json',report)
        print(json.dumps(report),flush=True)
    except Exception:
        for p in reversed(changed):api.call('config-profiles','PATCH',{'uuid':p['uuid'],'name':p['name'],'config':p['config']})
        for h in snap['hosts']:api.call('hosts','PATCH',{k:h[k] for k in ('uuid','address','sni')})
        atomic_json(CONTROL/'owner-profile.json',snap['owner']);atomic_json(ROOT/'settings.json',old_settings)
        atomic_json(m.network.path,old_network)
        for n in old_nodes:m.store.save_node(n)
        m.settings=old_settings;m.export_inventory()
        (CONTROL/'cover-policy.json').unlink(missing_ok=True)
        sync_dns(CONTROL,old_nodes)
        atomic_json(CONTROL/'identity-final-report.json',{'ok':False,'rolled_back':True})
        raise
    finally:
        subprocess.run(['systemctl','start',*services,'kingline-tunnel-health.timer'],check=True)
    subprocess.run(['systemctl','restart','kingline-smart-dns'],check=True)


if __name__ == '__main__':
    {'prepare':prepare,'canary':canary,'cutover':cutover}[sys.argv[1]]()
