#!/usr/bin/env python3
"""Consistent local daily business backups, verified before rotation."""
from contextlib import closing
from datetime import datetime,timezone
import os
from pathlib import Path
import sqlite3
import subprocess
ROOT=Path('/opt/kingline-staging')
import sys
sys.path.insert(0, str(ROOT/'bot'))
import diagnostics as diag
os.environ['KINGLINE_DIAGNOSTICS_PATH']=str(ROOT/'logs/operations.jsonl')
diag.configure('backup')

day=datetime.now(timezone.utc).strftime('%Y%m%d')
folder=ROOT/'backups'/('daily-'+day);folder.mkdir(mode=0o700,exist_ok=True)
for name in ('vpn_bot.db','ref.db','promos.db'):
    tmp=folder/(name+'.tmp')
    with closing(sqlite3.connect('file:'+str(ROOT/'data'/name)+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(tmp)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('backup verification failed')
    os.chmod(tmp,0o600);tmp.replace(folder/name)
panel=folder/'remnawave.dump.tmp'
with panel.open('wb') as f:
    os.chmod(panel,0o600)
    subprocess.run(['docker','exec','remnawave-db','sh','-c','exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom'],stdout=f,stderr=subprocess.PIPE,check=True,timeout=60)
with panel.open('rb') as f:
    subprocess.run(['docker','exec','-i','remnawave-db','pg_restore','--list'],stdin=f,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True,timeout=30)
panel.replace(folder/'remnawave.dump')
# Infrastructure state and decryption/SSH keys remain on this server only.
import shutil
manager=Path('/opt/kingline-manager/data')
for name in ('manager.sqlite3','site-admin.sqlite3'):
    source=manager/name
    if not source.exists():continue
    tmp=folder/(name+'.tmp')
    with closing(sqlite3.connect('file:'+str(source)+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(tmp)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('infrastructure backup verification failed')
    os.chmod(tmp,0o600);tmp.replace(folder/name)
for name in ('credentials.key','id_ed25519','id_ed25519.pub','settings.json','known_hosts','node-network.json'):
    if (manager/name).exists():
        target=folder/('manager-'+name);shutil.copyfile(manager/name,target);os.chmod(target,0o600)
if (manager/'cover/acme').is_dir():
    target=folder/'manager-cover-acme.tar.gz'
    with target.open('wb') as output:
        os.chmod(target,0o600)
        subprocess.run(['tar','-czf','-','-C',str(manager),'cover/acme'],stdout=output,
                       stderr=subprocess.PIPE,check=True,timeout=30)
# Routing identities, sticky leases and the Core's WireGuard keys are required
# to restore the new RU receiver. These copies never leave this server.
control=Path('/opt/kingline-control')
for name in ('owner-profile.json','probe-user.json','ru-egress.json','nodes.json','node-paths.json',
             'sticky-policy.json','sticky-dns.key','sticky-subscriptions.json','dns-replicas.json',
             'cover-policy.json','regional-probes.json','relay-usage.json','ru-monitor.json'):
    if (control/name).is_file():
        target=folder/('control-'+name);shutil.copyfile(control/name,target);os.chmod(target,0o600)
if (control/'sticky-dns.sqlite3').exists():
    with closing(sqlite3.connect('file:'+str(control/'sticky-dns.sqlite3')+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(folder/'sticky-dns.sqlite3')) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('sticky backup verification failed')
    os.chmod(folder/'sticky-dns.sqlite3',0o600)
for source in Path('/etc/wireguard').iterdir():
    if source.is_file() and source.suffix in {'.conf','.key','.pub'}:
        target=folder/('wireguard-'+source.name);shutil.copyfile(source,target);os.chmod(target,0o600)
for name in ('panel.token','admin.token'):
    if (manager/name).is_file():
        target=folder/('manager-'+name);shutil.copyfile(manager/name,target);os.chmod(target,0o600)
manual=ROOT/'docs/service.md'
if manual.is_file():shutil.copyfile(manual,folder/'service.md')
for old in sorted((ROOT/'backups').glob('daily-*'))[:-7]:
    shutil.rmtree(old)
diag.event('backup_verified')
print('Daily business and infrastructure backup verified; seven copies retained.')
