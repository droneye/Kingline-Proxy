#!/usr/bin/env python3
import datetime
import os
from pathlib import Path
import subprocess
import sys
import tarfile

os.umask(0o077)
role = sys.argv[1]
base = Path('/etc/fail2ban')
legacy = [p for folder in ['jail.d', 'action.d', 'filter.d']
          for p in (base / folder).glob('3x-ipl*.conf')]
backup = Path('/root/backups') / ('fail2ban-before-remnawave-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '.tar.gz')
with tarfile.open(backup, 'w:gz') as archive:
    archive.add(base, arcname='etc/fail2ban')
for path in legacy:
    path.unlink()
backend = 'auto' if role == 'de' else 'systemd'
logpath = 'logpath = /var/log/secure\n' if role == 'de' else ''
config = '[DEFAULT]\nbanaction = nftables-multiport\nbanaction_allports = nftables-allports\n\n[sshd]\nenabled = true\nbackend = ' + backend + '\n' + logpath + 'maxretry = 5\nfindtime = 10m\nbantime = 1h\n'
(base / 'jail.d/99-kingline-sshd.local').write_text(config)
check = subprocess.run(['fail2ban-client', '-t'], capture_output=True, text=True)
if check.returncode:
    raise RuntimeError('Fail2Ban configuration validation failed')
subprocess.run(['systemctl', 'enable', '--now', 'fail2ban'], check=True)
subprocess.run(['systemctl', 'restart', 'fail2ban'], check=True)
print('Removed old 3x-ui Fail2Ban configurations:', len(legacy))
print('Backup:', backup)
print('SSH protection configuration validated and service restarted')
