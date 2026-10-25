"""Provision the common, owned HTTPS identity before exposing a VPN node."""
import hashlib
import contextlib
import fcntl
import json
from pathlib import Path

from .cover_dns_hook import DOMAIN, dns_api
from .ssh import SafeError


@contextlib.contextmanager
def certificate_lock(root):
    with (Path(root) / 'cover.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def cover_addresses(nodes):
    return sorted({n['public_ip'] for n in nodes if n.get('provisioned', True)
                   and n.get('cover', {}).get('domain') == DOMAIN})


def sync_dns(control, nodes):
    addresses = cover_addresses(nodes)
    record = {'name': DOMAIN + '.', 'type': 'A', 'ttl': 60,
              'changetype': 'REPLACE' if addresses else 'DELETE',
              'records': [{'content': ip, 'disabled': False} for ip in addresses]}
    replicas = json.loads((Path(control) / 'dns-replicas.json').read_text())
    failed = False
    for replica in replicas:
        try:
            dns_api(replica, 'PATCH', {'rrsets': [record]})
        except Exception:
            failed = True
    if failed:
        raise SafeError('cover_dns_replica_update_failed')


class Cover:
    def __init__(self, settings):
        self.settings = settings
        if settings['domain'] != DOMAIN or settings['target'] != '127.0.0.1:8443':
            raise SafeError('unsupported_cover_identity')

    def deploy(self, ssh):
        cfg = self.settings
        scanner = Path(cfg['scanner_path'])
        if hashlib.sha256(scanner.read_bytes()).hexdigest() != cfg['scanner_sha256']:
            raise SafeError('scanner_checksum_mismatch')
        certificates = Path(cfg['certificate_directory'])
        payload = {'domain': cfg['domain'], 'image': cfg['image'],
                   'certificate': (certificates / 'fullchain.pem').read_text(),
                   'private_key': (certificates / 'privkey.pem').read_text(),
                   'xhttp_path': cfg.get('xhttp_path'),
                   'scanner_sha256': cfg['scanner_sha256']}
        ssh.run('install -d -m 700 /opt/kingline-node')
        ssh.upload(scanner, '/opt/kingline-node/RealiTLScanner')
        ssh.run('chmod 700 /opt/kingline-node/RealiTLScanner')
        ssh.upload(Path(__file__).with_name('cover_remote.py'), '/opt/kingline-node/cover-installer.py')
        result = json.loads(ssh.run('python3 /opt/kingline-node/cover-installer.py',
                                    json.dumps(payload), timeout=480))
        if not result.get('scanner_verified') or result.get('domain') != DOMAIN:
            raise SafeError('cover_target_verification_failed')
        return result


REMOVE_COVER = '''if test -f /opt/kingline-cover/docker-compose.yml; then
 cd /opt/kingline-cover && docker compose down && cd / && rm -rf /opt/kingline-cover
fi
python3 - <<'PY'
from pathlib import Path
import subprocess
p=Path('/etc/kingline-firewall.nft')
old=p.read_text(); new=''.join(line for line in old.splitlines(keepends=True) if 'comment "kingline HTTPS redirect"' not in line)
if new!=old:
 p.write_text(new)
 try: subprocess.run(['/usr/local/sbin/kingline-firewall'],check=True)
 except Exception:
  p.write_text(old);raise
PY'''
