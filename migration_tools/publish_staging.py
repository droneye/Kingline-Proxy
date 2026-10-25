import datetime
import json
from pathlib import Path
import subprocess
root = Path('/opt/kingline-staging')
access = json.loads((root / 'secrets/test-access.json').read_text())
# Use stdin, keeping the generated gate password out of shell arguments and logs.
hashed = subprocess.run(['docker','exec','-i','remnawave-caddy','caddy','hash-password'],
    input=(access['gate_password']+'\n').encode(), capture_output=True, check=True).stdout.decode().strip()
assert hashed.startswith('$2')
config = Path('/opt/remnawave/caddy/Caddyfile')
site = '''
test.edge.kingline.online {
    encode zstd gzip
    header {
        Referrer-Policy "no-referrer"
        X-Robots-Tag "noindex, nofollow, noarchive"
        X-Content-Type-Options "nosniff"
        -Server
    }
    handle /sub/* {
        reverse_proxy kingline-staging-api:8788
    }
    handle {
        basic_auth {
            kingline HASH
        }
        reverse_proxy kingline-staging-api:8788
    }
}
'''.replace('HASH', hashed)
old = config.read_text()
if 'test.edge.kingline.online {' not in old:
    backup = root / ('Caddyfile.before-staging-' + datetime.datetime.now().strftime('%Y%m%d%H%M%S'))
    backup.write_text(old)
    config.write_text(old + site)
    for action in ['validate', 'reload']:
        r = subprocess.run(['docker','exec','remnawave-caddy','caddy',action,'--config','/etc/caddy/Caddyfile','--adapter','caddyfile'], capture_output=True)
        if r.returncode:
            config.write_text(old)
            raise RuntimeError('Caddy ' + action + ' failed; original file restored')
print('Test site published behind a separate access gate; production routes retained.')
