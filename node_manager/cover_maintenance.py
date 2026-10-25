"""Renew the owned certificate and reconcile its deployment, including retries."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from .cover import Cover, certificate_lock
from .ssh import SSH
from .store import Store, atomic_json
from . import domains


def main():
    os.umask(0o077)
    root = Path('/opt/kingline-manager/data')
    control = Path('/opt/kingline-control')
    sys.path.insert(0, str(control))
    from smart_dns import https_cover_ready
    settings = json.loads((root / 'settings.json').read_text())
    cover = Cover(settings['cover'])
    report = {'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'nodes': []}
    with certificate_lock(root):
        # Always reconcile nodes after renew: a previous successful issuance may
        # have been followed by an unreachable node, which must be retried.
        result = subprocess.run(['/opt/kingline-manager/certbot-venv/bin/certbot', 'renew', '--quiet',
            '--config-dir', str(root / 'cover/acme'), '--work-dir', str(root / 'cover/work'),
            '--logs-dir', str(root / 'cover/logs')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=600)
        report['renewal_check_ok'] = result.returncode == 0
        directory = Path(settings['cover']['certificate_directory'])
        revision = hashlib.sha256(((directory / 'fullchain.pem').read_text() +
                                  (directory / 'privkey.pem').read_text() +
                                  (settings['cover'].get('xhttp_path') or '')).encode()).hexdigest()[:24]
        store = Store(root)
        if settings.get('node_domains'):
            state = json.loads((root / 'node-network.json').read_text())
            domains.sync(control, store.nodes(), state)
        for node in store.nodes():
            if not node.get('provisioned', True):
                continue
            item = {'uuid': node['uuid'], 'ip': node['public_ip']}
            ssh = None
            try:
                unchanged = node.get('cover', {}).get('revision') == revision
                if unchanged and https_cover_ready(node['public_ip'], settings['cover']['domain']):
                    item.update(ok=True, changed=False)
                else:
                    ssh = SSH(root, {'ip': node['public_ip'], 'ssh_port': node.get('ssh_port', 22),
                                      'host_key_sha256': node['host_key_sha256']})
                    node['cover'] = cover.deploy(ssh)
                    store.save_node(node)
                    item.update(ok=https_cover_ready(node['public_ip'], settings['cover']['domain']), changed=True)
            except Exception:
                item.update(ok=False, error='certificate_deployment_failed')
            finally:
                if ssh:
                    ssh.close()
            report['nodes'].append(item)
        # Node lifecycle operations hold the same lock, so this cannot resurrect
        # a retired node from a stale snapshot during certificate maintenance.
        atomic_json(control / 'nodes.json', store.nodes())
        report['ok'] = report['renewal_check_ok'] and bool(report['nodes']) and all(n['ok'] for n in report['nodes'])
        atomic_json(control / 'cover-status.json', report)
    print(json.dumps(report))
    if not report['ok']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
