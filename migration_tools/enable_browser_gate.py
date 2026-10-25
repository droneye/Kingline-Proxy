"""Add a normal browser sign-in screen while retaining the CLI's existing gate."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
os.umask(0o077)
root=Path('/opt/kingline-staging')
a=json.loads((root/'secrets/test-access.json').read_text())
token_file=root/'secrets/gate-cookie.token'
if not token_file.exists():token_file.write_text(secrets.token_urlsafe(40))
token=token_file.read_text().strip()
env=root/'.env'
lines=[line for line in env.read_text().splitlines() if not line.startswith(('GATE_COOKIE_TOKEN=','GATE_PASSWORD_HASH='))]
env.write_text('\n'.join(lines)+'\nGATE_COOKIE_TOKEN='+token+'\nGATE_PASSWORD_HASH='+hashlib.sha256(a['gate_password'].encode()).hexdigest()+'\n')
hashed=subprocess.run(['docker','exec','-i','remnawave-caddy','caddy','hash-password'],input=(a['gate_password']+'\n').encode(),capture_output=True,check=True).stdout.decode().strip()
site='''test.edge.kingline.online {
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
    handle /api/staging/gate-login {
        reverse_proxy kingline-staging-api:8788
    }
    @browserGate header_regexp Cookie "(^|;[ ]*)__Host-Kingline-Staging=TOKEN(;|$)"
    handle @browserGate {
        reverse_proxy kingline-staging-api:8788
    }
    @commandGate header Authorization "Basic *"
    handle @commandGate {
        basic_auth {
            kingline HASH
        }
        reverse_proxy kingline-staging-api:8788
    }
    handle {
        rewrite * /staging-gate
        reverse_proxy kingline-staging-api:8788
    }
}
'''.replace('TOKEN',token).replace('HASH',hashed)
caddy=Path('/opt/remnawave/caddy/Caddyfile')
old=caddy.read_text();prefix=old.split('test.edge.kingline.online {',1)[0]
assert prefix.count('panel.kingline.online {')==1 and prefix.count('connect.kingline.online {')==1
(root/('Caddyfile.before-browser-gate-'+datetime.datetime.now().strftime('%Y%m%d%H%M%S'))).write_text(old)
caddy.write_text(prefix+site)
for action in ['validate','reload']:
 r=subprocess.run(['docker','exec','remnawave-caddy','caddy',action,'--config','/etc/caddy/Caddyfile','--adapter','caddyfile'],capture_output=True)
 if r.returncode:
  caddy.write_text(old);raise RuntimeError('Gate configuration '+action+' failed')
print('Browser gate prepared; panel and subscription production routes retained.')
