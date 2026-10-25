#!/usr/bin/env python3
"""Quota-aware authoritative DNS controller. Packet handling and health checks stay in PowerDNS."""
import argparse
import datetime
import ipaddress
import json
import logging
import math
import socket
import ssl
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
from remna_api import RemnaAPI
from tunnel_health_policy import standard_identity, tunnel_state
from sticky_dns import StickyDNS,preferred_record

ZONE = 'edge.kingline.online.'
SMART = 'auto.' + ZONE
ROOT = Path('/opt/kingline-control')
log = logging.getLogger('kingline-smart-dns')

def remaining_weight(limit, used, disabled=False, speed_mbps=None):
    if disabled:
        return 0
    if not all(isinstance(n, (int, float)) and math.isfinite(n) and n >= 0 for n in [limit, used]) or limit <= 0:
        raise ValueError('Invalid quota metrics')
    if speed_mbps is not None and (not isinstance(speed_mbps, (int, float))
            or not math.isfinite(speed_mbps) or speed_mbps <= 0):
        raise ValueError('Invalid declared link speed')
    # Unknown existing tariff speeds have a neutral 1 Gbit/s weighting factor;
    # this is not a measurement or an assertion about their actual bandwidth.
    speed_factor = (speed_mbps if speed_mbps is not None else 1000) / 1000
    # Leave 5% headroom for traffic not counted by Xray. Manual endpoints stay available.
    remaining = max(0, limit * 0.95 - used)
    return max(1, int(remaining / 1_000_000_000 * speed_factor)) if remaining else 0

def regional_blocked(node_uuid, observations, now=None):
    """Optional future regional checks. Replayed or stale reports cannot disable nodes."""
    now = time.time() if now is None else now
    return any(o['node'] == node_uuid and o.get('required') and not o['ok']
               and o['failures'] >= 3 and 0 <= now - o['received'] <= 300 for o in observations)


def https_cover_ready(address, domain):
    """Validate the real TLS identity and website through the public REALITY fallback."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(['http/1.1'])
    try:
        with socket.create_connection((address, 443), timeout=3) as tcp:
            with context.wrap_socket(tcp, server_hostname=domain) as tls:
                tls.settimeout(3)
                tls.sendall(('HEAD / HTTP/1.1\r\nHost: ' + domain + '\r\nConnection: close\r\n\r\n').encode())
                header = b''
                while b'\r\n' not in header and len(header) < 1024:
                    part = tls.recv(1024)
                    if not part:
                        break
                    header += part
                return header.split(b'\r\n', 1)[0] == b'HTTP/1.1 200 OK'
    except (OSError, ssl.SSLError):
        return False

def lua_record(candidates):
    """One address per answer; native independent TCP checks keep working without the controller."""
    eligible = [(str(ipaddress.IPv4Address(ip)), int(weight)) for ip, weight in candidates if weight > 0]
    if not eligible:
        return 'A "; return {}"'
    largest = max(weight for _, weight in eligible)
    if largest > 1_000_000:
        eligible = [(ip, max(1, round(weight * 1_000_000 / largest))) for ip, weight in eligible]
    addresses = '{' + ','.join("'" + ip + "'" for ip, _ in eligible) + '}'
    weights = '{' + ','.join("['" + ip + "']=" + str(weight) for ip, weight in eligible) + '}'
    # PowerDNS 5.0 does not support backupSelector='empty'. A loopback-only
    # fallback tier marks both the all-down and initial unchecked states. Never
    # publish this sentinel; a fallback-all answer also contains it.
    lua = ("; local live=ifportup(443,{" + addresses + ",{'127.0.0.1'}}"
           + ",{selector='all',backupSelector='all',timeout=2,interval=10,minimumFailures=2});"
           + " for _,ip in ipairs(live) do if ip=='127.0.0.1' then return {}; end; end;"
           + " local weights=" + weights + "; local pool={};"
           + " for _,ip in ipairs(live) do table.insert(pool,{weights[ip],ip}); end;"
           + " if #pool==0 then return {}; end; return pickwrandom(pool)")
    return 'A ' + json.dumps(lua)

def dns_api(replica, path, method='GET', body=None):
    request = urllib.request.Request('http://' + replica['management_ip'] + ':8081/api/v1/servers/localhost/' + path,
        method=method, headers={'X-API-Key': replica['api_key'], 'Content-Type': 'application/json'},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request, timeout=8) as response:
        raw = response.read()
        return json.loads(raw) if raw else None

def rr(name, kind, records, ttl=60):
    return {'name': name, 'type': kind, 'ttl': ttl, 'changetype': 'REPLACE',
            'records': [{'content': value, 'disabled': False} for value in records]}

def ensure_zone(replica, replicas):
    try:
        return dns_api(replica, 'zones/' + ZONE)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
    nameservers = ['ns' + str(i + 1) + '.' + ZONE for i in range(len(replicas))]
    records = [rr(ZONE, 'NS', nameservers, 3600)]
    for ns, server in zip(nameservers, replicas):
        records.append(rr(ns, 'A', [server['public_ip']], 3600))
    records += [rr('de.' + ZONE, 'A', ['198.51.100.31']),
                rr('dk.' + ZONE, 'A', ['198.51.100.32'])]
    dns_api(replica, 'zones', 'POST', {'name': ZONE, 'kind': 'Native', 'nameservers': nameservers})
    dns_api(replica, 'zones/' + ZONE, 'PATCH', {'rrsets': records})
    return dns_api(replica, 'zones/' + ZONE)

def synchronize():
    inventory = json.loads((ROOT / 'nodes.json').read_text())
    replicas = json.loads((ROOT / 'dns-replicas.json').read_text())
    nodes = RemnaAPI(ROOT / 'nodes-read.token').items('nodes', 'nodes')
    by_id = {node['uuid']: node for node in nodes}
    regional = ROOT / 'regional-probes.json'
    observations = json.loads(regional.read_text())['observations'] if regional.exists() else []
    cover_file = ROOT / 'cover-policy.json'
    cover = json.loads(cover_file.read_text()) if cover_file.exists() else None
    tunnel_file = ROOT / 'tunnel-health.json'
    tunnel_report = json.loads(tunnel_file.read_text()) if tunnel_file.exists() else {}
    owner_file = ROOT / 'owner-profile.json'
    identity = standard_identity(json.loads(owner_file.read_text())) if owner_file.exists() else None
    report = {'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'nodes': [], 'replicas': []}
    usage_path=ROOT/'relay-usage.json'
    relay_usage=json.loads(usage_path.read_text()) if usage_path.exists() else {}
    candidates = []
    for entry in inventory:
        node = by_id[entry['uuid']]
        limit, used = node['trafficLimitBytes'], node['trafficUsedBytes']
        physical=relay_usage.get(entry['uuid'],{})
        accounting_ready=not entry.get('relay_accounted',False) or (
            physical.get('reset_day')==entry.get('reset_day',19) and
            0 <= time.time()-physical.get('updated_at',0) < 240 and time.time()<physical.get('period_until',0))
        if accounting_ready and physical and time.time()<physical.get('period_until',0):
            used=max(used,physical.get('used_bytes',0))
        if not node['isTrafficTrackingActive'] or node['trafficResetDay'] != entry.get('reset_day', 19):
            raise ValueError('Node traffic accounting configuration changed')
        regional_down = regional_blocked(entry['uuid'], observations)
        cover_ready = (entry.get('cover', {}).get('domain') == cover['domain'] and
                       https_cover_ready(entry['public_ip'], cover['domain'])) if cover else True
        vpn_state = tunnel_state(tunnel_report, entry, identity, time.time())
        vpn_down = vpn_state == 'excluded'
        excluded = (node['isDisabled'] or entry.get('effective_hidden',entry.get('hidden',False)) or not entry.get('smart_enabled', True) or entry.get('maintenance', False)
                    or not entry.get('network_auto_eligible',True) or not accounting_ready
                    or not entry.get('provisioned', True) or regional_down or not cover_ready or vpn_down)
        weight = remaining_weight(limit, used, excluded, entry.get('speed_mbps'))
        candidates.append((entry['public_ip'], weight))
        report['nodes'].append({'name': entry['name'], 'ip': entry['public_ip'], 'weight': weight,
            'quota_bytes': limit, 'used_bytes': used, 'management_connected': node['isConnected'],
            'remaining_before_reserve_bytes': max(0, limit - used),
            'speed_mbps': entry.get('speed_mbps'), 'reset_day': node['trafficResetDay'],
            'maintenance': entry.get('maintenance', False), 'smart_enabled': entry.get('smart_enabled', True),
            'network_auto_eligible':entry.get('network_auto_eligible',True),'via_uuid':entry.get('via_uuid'),
            'relay_accounting_ready':bool(accounting_ready),
            'regional_excluded': regional_down,
            'provisioned': entry.get('provisioned', True), 'https_cover_ready': cover_ready,
            'vpn_probe_state': vpn_state, 'vpn_probe_excluded': vpn_down})
    content = lua_record(candidates)
    sticky=None
    if (ROOT/'sticky-policy.json').exists() and json.loads((ROOT/'sticky-policy.json').read_text()).get('enabled'):
        api=RemnaAPI();users=[];start=0
        while True:
            page=api.call(f'users?start={start}&size=100');part=page.get('users',[])
            users.extend(u for u in part if u.get('status')=='ACTIVE' and u.get('tag') not in {'SERVICE_PROBE','RU_ROUTER','QA_DEVICE_LIMIT','NODE_TEST'})
            start+=len(part)
            if not part or start>=page.get('total',start):break
        sticky_store=StickyDNS(ROOT)
        sticky=sticky_store.reconcile(users,candidates)
        if sticky['legacy']:content=preferred_record(sticky['legacy']['ip'] if sticky['legacy']['eligible'] else None,sticky['legacy']['backups'])
        report['sticky']={'enabled':True,'subscriptions':len(sticky['leases']),'minimum_hold_seconds':86400}
    successes = 0
    for replica in replicas:
        try:
            zone = ensure_zone(replica, replicas)
            soa = next(item for item in zone['rrsets'] if item['type'] == 'SOA')
            fields = soa['records'][0]['content'].split()
            if fields[-1] != '5':
                # Don't cache a cold-start, fail-closed DNS answer for a full minute.
                fields[-1] = '5'
                dns_api(replica, 'zones/' + ZONE, 'PATCH', {'rrsets': [rr(ZONE, 'SOA', [' '.join(fields)])]})
            old = next((item for item in zone['rrsets'] if item['name'] == SMART and item['type'] == 'LUA'), None)
            changed = old is None or old['records'][0]['content'] != content
            if changed:
                dns_api(replica, 'zones/' + ZONE, 'PATCH', {'rrsets': [rr(SMART, 'LUA', [content])]})
            if sticky is not None:
                desired={lease['name']+'.':preferred_record(lease['ip'] if lease['eligible'] else None,lease['backups']) for lease in sticky['leases']}
                existing={r['name']:r for r in zone['rrsets'] if r['type']=='LUA' and __import__('re').fullmatch(r'u-[a-f0-9]{24}\.auto\.edge\.kingline\.online\.',r['name'])}
                updates=[rr(name,'LUA',[value]) for name,value in desired.items() if name not in existing or existing[name]['records'][0]['content']!=value]
                updates.extend({'name':name,'type':'LUA','changetype':'DELETE'} for name in existing if name not in desired)
                if updates:dns_api(replica,'zones/'+ZONE,'PATCH',{'rrsets':updates})
            if cover:
                # The owned identity lists all provisioned locations, including
                # manual-only ones whose smart traffic quota has been exhausted.
                addresses = sorted({n['public_ip'] for n in inventory if n.get('provisioned', True)
                                    and n.get('cover', {}).get('domain') == cover['domain']})
                name = cover['domain'] + '.'
                previous = next((r for r in zone['rrsets'] if r['name'] == name and r['type'] == 'A'), None)
                actual = sorted(r['content'] for r in previous['records']) if previous else []
                if actual != addresses:
                    record = rr(name, 'A', addresses)
                    if not addresses:
                        record['changetype'] = 'DELETE'
                    dns_api(replica, 'zones/' + ZONE, 'PATCH', {'rrsets': [record]})
            # Start/keep native health probes warm even when there are no users.
            probe = subprocess.run(['dig', '@' + replica['public_ip'], SMART, 'A', '+short', '+time=1', '+tries=1'],
                                   capture_output=True, text=True, timeout=3)
            answers = probe.stdout.strip().splitlines()
            ready = probe.returncode == 0 and len(answers) == 1 and answers[0] in {ip for ip, weight in candidates if weight > 0}
            report['replicas'].append({'role': replica['role'], 'ok': True, 'changed': changed, 'dns_answer_ready': ready})
            successes += 1
        except Exception as error:
            report['replicas'].append({'role': replica['role'], 'ok': False, 'error': type(error).__name__})
            log.error('DNS replica %s synchronization failed: %s', replica['role'], type(error).__name__)
    if successes == 0:
        raise RuntimeError('No DNS replica synchronized')
    if sticky is not None:sticky_store.publish_index(sticky)
    temporary = ROOT / 'smart-dns-status.json.tmp'
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(ROOT / 'smart-dns-status.json')
    return report

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    if args.once:
        print(json.dumps(synchronize(), ensure_ascii=False))
        return
    while True:
        try:
            result = synchronize()
            if not any(n['weight'] > 0 for n in result['nodes']):
                log.error('All automatic routes exhausted their traffic budget; manual routes remain unchanged')
        except Exception as error:
            # Preserve last good DNS data. Each replica continues checking TCP availability.
            log.error('Quota refresh failed; retaining previous routing: %s', type(error).__name__)
        time.sleep(30)

if __name__ == '__main__':
    main()
