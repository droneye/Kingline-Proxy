#!/usr/bin/env python3
"""Run on a server to avoid local VPN/TUN synthetic TCP handshakes."""
import concurrent.futures
import datetime
import json
import socket
import sys

role = sys.argv[1]
if role == 'core':
    targets = [(host, port, port in [22, 53, 443]) for host, oldport in [('198.51.100.31', 44106), ('198.51.100.32', 55825)]
               for port in [22, 53, 443, 2222, 8081, 36871, 2096, oldport]]
    targets += [(host, 2222, True) for host in ['10.77.0.2', '10.77.0.3']]
    targets += [(host, 8081, True) for host in ['10.77.0.2', '10.77.0.3']]
else:
    targets = [('198.51.100.10', port, port in [22, 53, 80, 443])
               for port in [22, 53, 80, 443, 3000, 3001, 3010, 5432, 6379, 6767, 8081, 18090, 65529]]
def probe(target):
    host, port, expected = target
    try:
        with socket.create_connection((host, port), timeout=4):
            reachable = True
    except OSError:
        reachable = False
    return {'host': host, 'port': port, 'reachable': reachable,
            'expected_reachable': expected, 'passed': reachable == expected}
with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
    checks = list(pool.map(probe, targets))
report = {'source': role, 'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'checks': checks}
print(json.dumps(report, indent=2))
assert all(check['passed'] for check in checks), 'Unexpected port reachability'
