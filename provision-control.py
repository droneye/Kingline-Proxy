#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import sys

os.umask(0o077)
hosts = {
    'core': ('198.51.100.10', '10.77.0.1', 'YI6KLejrE0s//vIZl55FCJFy4LYFfzhCG3n6JWQRBzI='),
    'de': ('198.51.100.31', '10.77.0.2', 'shguzEuLEaQgU75b3H+HHuEQ8By66dGPlvUTq2Ct/nE='),
    'dk': ('198.51.100.32', '10.77.0.3', 'II+Zq+bGJ7fAC+VEJhPriBNN8UiUz703g68XmhRs0yU='),
}
role = sys.argv[1]
public_ip, private_ip, public_key = hosts[role]
key = Path('/etc/wireguard/control.key').read_text().strip()
assert Path('/etc/wireguard/control.pub').read_text().strip() == public_key
config = '[Interface]\nAddress = ' + private_ip + '/32\nListenPort = 51820\nPrivateKey = ' + key + '\nMTU = 1420\n'
peers = ['de', 'dk'] if role == 'core' else ['core']
for peer in peers:
    endpoint, address, peer_key = hosts[peer]
    config += '\n[Peer]\nPublicKey = ' + peer_key + '\nAllowedIPs = ' + address + '/32\nEndpoint = ' + endpoint + ':51820\nPersistentKeepalive = 25\n'
path = Path('/etc/wireguard/wg-control.conf')
path.write_text(config)
path.chmod(0o600)

public_tcp = '22, 80, 443' if role == 'core' else '22, 443'
wg_sources = ', '.join(hosts[peer][0] for peer in peers)
private_rule = 'iifname "wg-control" ip saddr 10.77.0.1 tcp dport 2222 accept' if role != 'core' else ''
rules = '''table inet kingline_filter {
    chain input {
        type filter hook input priority -10; policy drop;
        ct state invalid drop
        ct state established,related accept
        iifname "lo" accept
        ip protocol icmp accept
        meta l4proto ipv6-icmp accept
        tcp dport { TCP_PORTS } accept
        ip saddr { WG_SOURCES } udp dport 51820 accept
        PRIVATE_RULE
    }
}
'''.replace('TCP_PORTS', public_tcp).replace('WG_SOURCES', wg_sources).replace('PRIVATE_RULE', private_rule)
Path('/etc/kingline-firewall.nft').write_text(rules)
loader = Path('/usr/local/sbin/kingline-firewall')
loader.write_text('''#!/bin/bash
set -euo pipefail
temp=$(mktemp)
trap 'rm -f "$temp"' EXIT
if nft list table inet kingline_filter >/dev/null 2>&1; then
    printf 'delete table inet kingline_filter\\n' > "$temp"
fi
cat /etc/kingline-firewall.nft >> "$temp"
nft --check -f "$temp"
nft -f "$temp"
''')
loader.chmod(0o700)
Path('/etc/systemd/system/kingline-firewall.service').write_text('''[Unit]
Description=Kingline input firewall
Wants=network-pre.target
After=network-pre.target
Before=docker.service wg-quick@wg-control.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/kingline-firewall
ExecReload=/usr/local/sbin/kingline-firewall

[Install]
WantedBy=multi-user.target
''')
docker_dropin = Path('/etc/systemd/system/docker.service.d')
docker_dropin.mkdir(exist_ok=True)
(docker_dropin / '20-kingline-firewall.conf').write_text('[Unit]\nRequires=kingline-firewall.service\nAfter=kingline-firewall.service\n')
subprocess.run(['systemctl', 'daemon-reload'], check=True)
subprocess.run(['systemctl', 'enable', '--now', 'kingline-firewall.service'], check=True)
if role == 'de':
    subprocess.run(['systemctl', 'disable', '--now', 'firewalld'], check=True)
    subprocess.run(['systemctl', 'mask', 'firewalld'], check=True)
subprocess.run(['systemctl', 'enable', '--now', 'wg-quick@wg-control.service'], check=True)
print('CONTROL_NETWORK_READY', role, private_ip)
