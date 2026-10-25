#!/usr/bin/env python3
"""Test all-unavailable answers on a temporary name without stopping live nodes."""
import datetime
import json
from pathlib import Path
import subprocess
import time
from smart_dns import ROOT, ZONE, dns_api, lua_record, rr

replicas = json.loads((ROOT / 'dns-replicas.json').read_text())
name = '_closed-port-probe.' + ZONE
record = lua_record([('198.51.100.31', 5), ('198.51.100.32', 1)]).replace('ifportup(443,', 'ifportup(1,')
report = {'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'checks': []}
created = []
try:
    for replica in replicas:
        zone = dns_api(replica, 'zones/' + ZONE)
        assert not any(r['name'] == name for r in zone['rrsets']), 'Test name already exists'
        dns_api(replica, 'zones/' + ZONE, 'PATCH', {'rrsets': [rr(name, 'LUA', [record], 5)]})
        created.append(replica)
        subprocess.run(['dig', '@' + replica['public_ip'], name, '+short'], stdout=subprocess.DEVNULL, check=True)
    time.sleep(32)
    for replica in replicas:
        for transport in ['+notcp', '+tcp']:
            query = subprocess.check_output(['dig', '@' + replica['public_ip'], name, 'A',
                transport, '+time=2', '+tries=2', '+noall', '+comments', '+answer']).decode()
            assert 'status: NOERROR' in query and 'ANSWER: 0' in query
        recursion = subprocess.check_output(['dig', '@' + replica['public_ip'], 'example.com', 'A',
            '+recurse', '+time=2', '+tries=2', '+noall', '+comments', '+answer']).decode()
        assert 'status: REFUSED' in recursion
        report['checks'].append({'role': replica['role'], 'all_unavailable_returns_no_address': True,
                                 'tcp_and_udp': True, 'public_recursion_refused': True})
    (ROOT / 'verification-dns-safety.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))
finally:
    for replica in created:
        dns_api(replica, 'zones/' + ZONE, 'PATCH', {'rrsets': [{'name': name, 'type': 'LUA', 'changetype': 'DELETE'}]})
