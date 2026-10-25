"""Fresh authenticated tunnel observations used by the DNS controller."""
import hashlib
import json

MAX_AGE = 600
FAILURE_THRESHOLD = 2


def standard_identity(state):
    # A credential/config change invalidates observations without exposing credentials.
    values = {key: state.get(key) for key in ('sni', 'transport', 'fingerprint', 'short_id')}
    values['public_key'] = state.get('reality', {}).get('publicKey')
    values['user'] = state.get('user', {}).get('vlessUuid')
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def current_observation(report, node, identity, now):
    if report.get('identity') != identity or not report.get('dependencies_ready', False):
        return None
    for row in report.get('nodes', []):
        if (row.get('uuid') == node['uuid'] and row.get('ip') == node['public_ip']
                and row.get('path_revision') == node.get('path_revision')
                and 0 <= now - row.get('checked_at', 0) <= MAX_AGE):
            return row
    return None


def tunnel_state(report, node, identity, now):
    row = current_observation(report, node, identity, now)
    if row is None:
        return 'unknown'
    if row['ok']:
        return 'healthy'
    return 'excluded' if row['failures'] >= FAILURE_THRESHOLD else 'retrying'


def observation(node, identity, previous, now, result=None):
    old = current_observation(previous, node, identity, now)
    failures = 0 if result else (old.get('failures', 0) if old else 0) + 1
    return {'uuid': node['uuid'], 'ip': node['public_ip'], 'checked_at': now,
            'path_revision':node.get('path_revision'),
            'ok': result is not None, 'failures': failures,
            **({'https_ms': result['https_ms'], 'udp_dns_ms': result['udp_dns_ms']}
               if result else {'error': 'authenticated_tunnel_check_failed'})}
