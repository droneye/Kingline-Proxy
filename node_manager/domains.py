"""Stable subscription names. DNS points at the actual entry, never a new proxy.

Only our n-<UUID> A records are reconciled; other zone data is left untouched.
Actual IPs remain in inventory/probes so a resolver cannot silently change checks.
"""
import ipaddress
import fcntl
import json
from pathlib import Path
import re
import subprocess
import uuid

from .cover_dns_hook import DOMAIN, dns_api
from .ssh import SafeError

MANAGED = re.compile(r'n-[0-9a-f]{32}\.' + re.escape(DOMAIN) + r'\.')


def hostname(node):
    return 'n-' + uuid.UUID(node['uuid']).hex + '.' + DOMAIN


def enabled(manager):
    return bool(getattr(manager, 'settings', {}).get('node_domains'))


def published_address(manager, node, entry_ip):
    return hostname(node) if enabled(manager) else entry_ip


def desired_records(nodes, network):
    from .node_network import route_paths
    active = {n['uuid']: n for n in nodes if n.get('provisioned', True)}
    paths = route_paths({i: network.get('via', {}).get(i) for i in active}, active)
    return {hostname(n) + '.': str(ipaddress.IPv4Address(active[paths[i][0]]['public_ip']))
            for i, n in active.items()}


def verify_records(replica, desired):
    for name, address in desired.items():
        result = subprocess.run(['dig', '@' + replica['public_ip'], name, 'A', '+short',
                                 '+time=2', '+tries=1'], capture_output=True, text=True, timeout=5)
        if result.returncode or set(result.stdout.split()) != {address}:
            raise SafeError('node_domain_resolution_failed')


def sync(control, nodes, network):
    with (Path(control) / 'node-domains.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _sync(control, nodes, network)


def _sync(control, nodes, network):
    # Pending DNS enables a phone test without adding the node to public inventory.
    # Every reconciler must retain these names while the human review is pending.
    pending_file = Path(control) / 'pending-nodes.json'
    pending = json.loads(pending_file.read_text()) if pending_file.exists() else []
    known = {n['uuid'] for n in nodes if n.get('provisioned', True)}
    desired = desired_records([*nodes, *(n for n in pending if n['uuid'] not in known)], network)
    replicas = json.loads((Path(control) / 'dns-replicas.json').read_text())
    failed = False
    for replica in replicas:
        try:
            zone = dns_api(replica)
            old = {r['name'] for r in zone['rrsets']
                   if r['type'] == 'A' and MANAGED.fullmatch(r['name'])}
            records = [{'name': name, 'type': 'A', 'ttl': 60, 'changetype': 'REPLACE',
                        'records': [{'content': address, 'disabled': False}]}
                       for name, address in sorted(desired.items())]
            records += [{'name': name, 'type': 'A', 'changetype': 'DELETE'}
                        for name in sorted(old - desired.keys())]
            if records:
                dns_api(replica, 'PATCH', {'rrsets': records})
            verify_records(replica, desired)
        except Exception:
            failed = True
    if failed:
        raise SafeError('node_domains_replica_update_failed')
    return desired
