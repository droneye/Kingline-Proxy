#!/usr/bin/env python3
"""Run on core: expose the normal authenticated login page without an entry cookie."""
import datetime
import os
from pathlib import Path
import subprocess

os.umask(0o077)
path = Path('/opt/remnawave/caddy/Caddyfile')
old = path.read_text()
start = old.index('panel.kingline.online {')
end = old.index('connect.kingline.online {', start)
panel = '''panel.kingline.online {
    encode zstd gzip
    header {
        Referrer-Policy "no-referrer"
        Cache-Control "no-store"
        X-Robots-Tag "noindex, nofollow, noarchive"
        -Server
    }
    handle /entry/* {
        redir / 303
    }
    handle {
        reverse_proxy remnawave:3000
    }
}
'''
new = old[:start] + panel + old[end:]
check = subprocess.run(['docker', 'exec', '-i', 'remnawave-caddy', 'caddy', 'validate',
    '--config', '/dev/stdin', '--adapter', 'caddyfile'], input=new.encode(), capture_output=True)
assert check.returncode == 0, 'Caddy rejected the candidate; live configuration unchanged'
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
backup = path.with_name('Caddyfile.before-normal-login-' + stamp)
backup.write_text(old)
# The file is bind-mounted: preserve its inode when updating it.
path.write_text(new)
command = ['docker', 'exec', 'remnawave-caddy', 'caddy', 'reload',
           '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile']
result = subprocess.run(command, capture_output=True)
if result.returncode:
    path.write_text(old)
    subprocess.run(command, check=True, capture_output=True)
    raise RuntimeError('Caddy reload failed; original configuration restored')
print('Panel now opens at its normal address; Remnawave login remains required')
