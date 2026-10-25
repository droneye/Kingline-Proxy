#!/usr/bin/env python3
"""Install one authoritative DNS replica; no open recursive resolver."""
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys

os.umask(0o077)
role = sys.argv[1]
hosts = {'core': ('198.51.100.10', '10.77.0.1'), 'de': ('198.51.100.31', '10.77.0.2'),
         'dk': ('198.51.100.32', '10.77.0.3')}
public, management = hosts[role]
image = 'powerdns/pdns-auth-50@sha256:4d6cc4fc42a28f2df7fb55f6f36d8323f96e0da66135ccdad057ca7349b223b4'
root = Path('/opt/kingline-dns')
root.mkdir(mode=0o700, exist_ok=True)
data = root / 'data'
data.mkdir(mode=0o700, exist_ok=True)
subprocess.run(['docker', 'pull', image], check=True, stdout=subprocess.DEVNULL)
keyfile = root / 'api.key'
if not keyfile.exists():
    keyfile.write_text(secrets.token_urlsafe(32))
config = f'''launch=gsqlite3
setuid=953
setgid=953
gsqlite3-database=/var/lib/powerdns/pdns.sqlite3
local-address={public}
local-port=53
api=yes
api-key={keyfile.read_text().strip()}
webserver=yes
webserver-address={management}
webserver-port=8081
webserver-allow-from=10.77.0.1/32
enable-lua-records=yes
lua-records-exec-limit=100000
lua-health-checks-interval=10
cache-ttl=0
query-cache-ttl=0
negquery-cache-ttl=0
max-cache-entries=0
default-ttl=60
default-soa-content=ns1.edge.kingline.online. hostmaster.kingline.online. 0 3600 600 604800 5
allow-axfr-ips=
disable-axfr=yes
version-string=anonymous
security-poll-suffix=
loglevel=4
log-dns-queries=no
log-dns-details=no
edns-subnet-processing=no
udp-truncation-threshold=1232
any-to-tcp=yes
reuseport=yes
receiver-threads=1
distributor-threads=1
disable-syslog=yes
daemon=no
guardian=no
'''
(root / 'pdns.conf').write_text(config)
database = data / 'pdns.sqlite3'
if not database.exists():
    schema = subprocess.check_output(['docker', 'run', '--rm', '--entrypoint', 'cat', image,
                                     '/usr/local/share/doc/pdns/schema.sqlite3.sql']).decode()
    with sqlite3.connect(database) as connection:
        connection.executescript(schema)
for path in [data, database]:
    os.chown(path, 953, 953)
os.chown(root / 'pdns.conf', 0, 953)
(root / 'pdns.conf').chmod(0o640)
(root / 'docker-compose.yml').write_text(f'''services:
  dns:
    image: {image}
    container_name: kingline-dns
    restart: always
    network_mode: host
    user: "0:0"
    entrypoint: ["/usr/bin/tini", "--", "/usr/local/sbin/pdns_server"]
    command: ["--config-dir=/etc/kingline-dns"]
    cap_drop: [ALL]
    cap_add: [NET_BIND_SERVICE, SETUID, SETGID, CHOWN]
    security_opt: [no-new-privileges:true]
    read_only: true
    mem_limit: 192m
    pids_limit: 128
    volumes:
      - ./pdns.conf:/etc/kingline-dns/pdns.conf:ro
      - ./data:/var/lib/powerdns
    tmpfs:
      - /run/pdns:uid=0,gid=953,mode=0770
      - /tmp
    logging:
      driver: local
      options:
        max-size: "5m"
        max-file: "3"
''')
# Keep management ports restricted to WireGuard.
firewall = Path('/etc/kingline-firewall.nft')
rules = firewall.read_text()
if 'tcp dport 53 accept' not in rules:
    marker = '        ip protocol icmp accept\n'
    assert marker in rules
    dns_rules = ('        tcp dport 53 accept\n'
                 '        udp dport 53 meter dns_per_ip { ip saddr timeout 1m limit rate 100/second burst 200 packets } accept\n')
    if role != 'core':
        dns_rules += '        iifname "wg-control" ip saddr 10.77.0.1 tcp dport 8081 accept\n'
    firewall.with_suffix('.nft.before-dns').write_text(rules)
    firewall.write_text(rules.replace(marker, marker + dns_rules))
    subprocess.run(['systemctl', 'reload', 'kingline-firewall'], check=True)
subprocess.run(['docker', 'compose', 'config', '--quiet'], cwd=root, check=True)
subprocess.run(['docker', 'compose', 'up', '-d'], cwd=root, check=True)
print('Authoritative DNS replica started:', role, public)
