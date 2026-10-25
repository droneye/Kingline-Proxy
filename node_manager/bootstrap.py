"""One-time installation on the existing control server; does not reinstall VPN nodes."""
import json
import os
from pathlib import Path
import secrets
import subprocess

from remna_api import RemnaAPI
from node_manager.store import Store, atomic_json

os.umask(0o077)
root = Path('/opt/kingline-manager/data')
root.mkdir(mode=0o700, parents=True, exist_ok=True)
control = Path('/opt/kingline-control')
api = RemnaAPI()
state = json.loads((control / 'owner-profile.json').read_text())
atomic_json(root / 'settings.json', {
    'core_public_ip': '198.51.100.10', 'control_directory': str(control),
    'protected_ips': ['198.51.100.10', '198.51.100.20'],
    'node_image': 'remnawave/node@sha256:1f97485b4bc7e4944f1ae95cc57d176376813b0022e9567b705f384f1a2e909d'})
if not (root / 'admin.token').exists():
    (root / 'admin.token').write_text(secrets.token_urlsafe(48))
if not (root / 'panel.token').exists():
    token = api.call('tokens', 'POST', {'name': 'Kingline node manager', 'expiresInDays': 365,
        'scopes': ['nodes:read', 'nodes:write', 'hosts:read', 'hosts:write', 'config-profiles:read', 'keygen:read']})
    (root / 'panel.token').write_text(token['token'])
    atomic_json(root / 'panel-token-info.json', {k: v for k, v in token.items() if k != 'token'})
if not (root / 'id_ed25519').exists():
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'kingline-node-manager',
                    '-f', str(root / 'id_ed25519')], check=True)
store = Store(root)
if not store.nodes():
    inventory = json.loads((control / 'nodes.json').read_text())
    hosts = api.items('hosts', 'hosts')
    for entry in inventory:
        host = next(h for h in hosts if h['address'] == entry['public_ip'])
        store.save_node({**entry, 'label': host['remark'], 'host_uuid': host['uuid'],
                         'speed_mbps': None, 'maintenance': False, 'provisioned': True,
                         'ssh_port': 22, 'origin': 'existing verified deployment'})
    atomic_json(root / 'inventory-before-manager.json', inventory)
    atomic_json(control / 'nodes.json', store.nodes())
# Future users assigned the panel's Default-Squad get the same standard inbound.
# Keep any existing inbound permissions, and retain all squad UUIDs.
for squad in api.items('internal-squads', 'internalSquads'):
    if squad['name'] == 'Default-Squad':
        ids = [i['uuid'] for i in squad['inbounds']]
        if state['inbound_uuid'] not in ids:
            atomic_json(root / 'default-squad-before-manager.json',
                        {'uuid': squad['uuid'], 'name': squad['name'], 'inbounds': ids})
            api.call('internal-squads', 'PATCH', {'uuid': squad['uuid'], 'inbounds': ids + [state['inbound_uuid']]})
for path in root.iterdir():
    if path.is_file():
        path.chmod(0o600)
print('Backend credentials and existing-node inventory initialized; no VPN nodes restarted.')
