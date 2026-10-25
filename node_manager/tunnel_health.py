"""Check actual VPN forwarding independently of TCP reachability."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

from .probe import verify_tunnel
from .store import atomic_json

CONTROL = Path('/opt/kingline-control')
sys.path.insert(0, str(CONTROL))
from tunnel_health_policy import observation, standard_identity

EXIT_URLS = ('https://api.ipify.org', 'https://ipv4.icanhazip.com')


def dependencies_ready():
    # Failure of a shared test endpoint must not disable every VPN location.
    for url in EXIT_URLS:
        try:
            with urllib.request.urlopen(url, timeout=6) as response:
                value = response.read(128).decode().strip()
            if ipaddress.ip_address(value).is_global:
                return True
        except Exception:
            pass
    return False


def main():
    os.umask(0o077)
    with (CONTROL / 'tunnel-health.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads((CONTROL / 'owner-profile.json').read_text())
        nodes = [n for n in json.loads((CONTROL / 'nodes.json').read_text()) if n.get('provisioned', True)]
        identity = standard_identity(state)
        path = CONTROL / 'tunnel-health.json'
        previous = json.loads(path.read_text()) if path.exists() else {}
        report = {'identity': identity, 'source': 'LT-control, not a Russian access network',
                  'dependencies_ready': dependencies_ready(), 'nodes': []}
        if report['dependencies_ready']:
            def check(node):
                selected=node.get('manual_connection') or {}
                try:
                    result = verify_tunnel(CONTROL, node['public_ip'], state_override={**state,**selected},
                                           connect_address=selected.get('connect_address'),server_port=selected.get('port',443),
                                           exit_urls=EXIT_URLS, dns_resolvers=('9.9.9.9', '1.1.1.1'))
                except Exception:
                    result = None
                row = observation(node, identity, previous, time.time(), result)
                manual = node.get('manual_connection')
                if manual:
                    try:
                        check = result or verify_tunnel(CONTROL, node['public_ip'], state_override={**state, **manual},
                            connect_address=manual.get('connect_address'),
                            server_port=manual['port'], exit_urls=EXIT_URLS,
                            dns_resolvers=('9.9.9.9', '1.1.1.1'))
                        row['manual_profile'] = {'ok': True, 'sni': manual['sni'], 'port': manual['port'],
                                                 'https_ms': check['https_ms'], 'udp_dns_ms': check['udp_dns_ms']}
                    except Exception:
                        row['manual_profile'] = {'ok': False, 'error': 'manual_tunnel_check_failed'}
                return row
            with ThreadPoolExecutor(max_workers=3) as pool:
                report['nodes'] = list(pool.map(check, nodes))
        report['checked_at'] = time.time()
        report['ok'] = report['dependencies_ready'] and bool(report['nodes']) and all(
            n['ok'] and n.get('manual_profile', {}).get('ok', True) for n in report['nodes'])
        atomic_json(path, report)
    print(json.dumps(report))
    if not report['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
