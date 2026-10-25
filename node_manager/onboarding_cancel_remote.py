"""Remove only this candidate's service. Leave updates and tested SSH key access."""
import json
from pathlib import Path
import subprocess
import sys

if __name__ == '__main__':
    payload = json.load(sys.stdin)
    marker = Path('/opt/kingline-node/identity.json')
    assert json.loads(marker.read_text())['job_id'] == payload['job_id'], 'candidate_identity_mismatch'
    def run(*args):
        subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    for directory in ('/opt/remnanode', '/opt/kingline-cover'):
        file = Path(directory) / 'docker-compose.yml'
        if file.exists(): run('docker', 'compose', '-f', str(file), 'down')
    run('systemctl', 'disable', '--now', 'wg-quick@wg-control')
    for file in ('/opt/remnanode/.env', '/opt/remnanode/docker-compose.yml',
                 '/etc/wireguard/wg-control.conf'):
        Path(file).unlink(missing_ok=True)
    # Cover contains the owned wildcard private key; remove its service directory.
    import shutil
    shutil.rmtree('/opt/kingline-cover', ignore_errors=False) if Path('/opt/kingline-cover').exists() else None
    print(json.dumps({'ok': True}))
