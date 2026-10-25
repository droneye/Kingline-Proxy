"""Remove only the extra preview gate; normal account/admin controls stay active."""
from datetime import datetime,timezone
from pathlib import Path
import subprocess
p=Path('/opt/remnawave/caddy/Caddyfile');old=p.read_text()
start=old.index('test.edge.kingline.online {')
# The preview block was appended last. Validate balanced braces before replacing.
level=0;end=None
for i in range(start,len(old)):
    if old[i]=='{':level+=1
    elif old[i]=='}':
        level-=1
        if level==0:end=i+1;break
assert end is not None
site='''test.edge.kingline.online {
    encode zstd gzip
    header {
        Referrer-Policy "no-referrer"
        X-Robots-Tag "noindex, nofollow, noarchive"
        X-Content-Type-Options "nosniff"
        -Server
    }
    reverse_proxy kingline-staging-api:8788
}'''
backup=Path('/opt/kingline-staging/backups')/('Caddyfile.before-public-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S'))
backup.write_text(old);backup.chmod(0o600)
p.write_text(old[:start]+site+old[end:])
try:
    for action in ('validate','reload'):
        subprocess.run(['docker','exec','remnawave-caddy','caddy',action,'--config','/etc/caddy/Caddyfile','--adapter','caddyfile'],check=True,capture_output=True,timeout=20)
except BaseException:
    p.write_text(old)
    subprocess.run(['docker','exec','remnawave-caddy','caddy','reload','--config','/etc/caddy/Caddyfile','--adapter','caddyfile'],capture_output=True,timeout=20)
    raise
print('Preview gate removed; other sites unchanged.')
