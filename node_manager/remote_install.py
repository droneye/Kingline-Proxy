#!/usr/bin/env python3
"""Uploaded to a fresh Ubuntu server. Payload only on stdin; never log credentials."""
import json
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys

os.umask(0o077)


def run(*args, **kwargs):
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)


def write(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    temporary.write_text(value)
    temporary.chmod(mode)
    temporary.replace(path)


def base(p):
    root = Path('/opt/kingline-node')
    root.mkdir(mode=0o700, exist_ok=True)
    marker = root / 'identity.json'
    if marker.exists():
        assert json.loads(marker.read_text())['job_id'] == p['job_id']
    else:
        write(marker, json.dumps({'job_id': p['job_id'], 'public_ip': p['ip']}))
    ready = root / 'packages.ready'
    if not ready.exists():
        env = dict(os.environ, DEBIAN_FRONTEND='noninteractive', NEEDRESTART_MODE='a')
        run('apt-get', '-o', 'DPkg::Lock::Timeout=180', 'update', env=env, timeout=300)
        run('apt-get', '-y', '-o', 'DPkg::Lock::Timeout=180', '-o', 'Dpkg::Options::=--force-confold',
            'upgrade', env=env, timeout=1200)
        run('apt-get', '-y', '-o', 'DPkg::Lock::Timeout=180', 'install',
            'ca-certificates', 'curl', 'gnupg', 'wireguard-tools', 'nftables', 'fail2ban', 'systemd-timesyncd',
            env=env, timeout=600)
        run('install', '-m', '0755', '-d', '/etc/apt/keyrings')
        run('curl', '-fsSL', '--max-time', '30', 'https://download.docker.com/linux/ubuntu/gpg',
            '-o', '/etc/apt/keyrings/docker.asc')
        key = subprocess.check_output(['gpg', '--show-keys', '--with-colons', '/etc/apt/keyrings/docker.asc'],
                                      stderr=subprocess.DEVNULL).decode()
        assert '9DC858229FC7DD38854AE2D88D81803C0EBFCD88' in key, 'docker signing key changed'
        Path('/etc/apt/keyrings/docker.asc').chmod(0o644)
        release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        codename = release['VERSION_CODENAME'].strip('"')
        assert re.fullmatch('[a-z]+', codename)
        write('/etc/apt/sources.list.d/kingline-docker.sources',
              f'Types: deb\nURIs: https://download.docker.com/linux/ubuntu\nSuites: {codename}\n'
              'Components: stable\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/docker.asc\n', 0o644)
        run('apt-get', 'update', env=env, timeout=300)
        run('apt-get', '-y', '-o', 'DPkg::Lock::Timeout=180', 'install', 'docker-ce', 'docker-ce-cli',
            'containerd.io', 'docker-compose-plugin', env=env, timeout=600)
        ready.touch(mode=0o600)
    run('systemctl', 'enable', '--now', 'docker', 'systemd-timesyncd')
    Path('/etc/wireguard').mkdir(mode=0o700, exist_ok=True)
    key = Path('/etc/wireguard/control.key')
    if not key.exists():
        write(key, subprocess.check_output(['wg', 'genkey']).decode())
    public = subprocess.check_output(['wg', 'pubkey'], input=key.read_bytes()).decode().strip()
    write('/etc/wireguard/control.pub', public + '\n')
    return {'public_key': public, 'reboot_required': Path('/var/run/reboot-required').exists()}


def harden_ssh(p):
    """Run only after the manager has successfully tested its installed SSH key."""
    main = Path('/etc/ssh/sshd_config')
    snippet = Path('/etc/ssh/sshd_config.d/00-kingline-key-only.conf')
    old_main = main.read_text()
    old_snippet = snippet.read_text() if snippet.exists() else None
    main_mode = main.stat().st_mode & 0o777
    snippet_mode = snippet.stat().st_mode & 0o777 if snippet.exists() else 0o644
    backup = main.with_name('sshd_config.before-kingline')
    if not backup.exists():
        write(backup, old_main, 0o600)
    # Some provider images explicitly include their password-enabled template
    # before the usual wildcard Include. OpenSSH uses the first global value.
    include = 'Include ' + str(snippet)
    original = ''.join(line for line in old_main.splitlines(keepends=True)
                       if line.strip() != include)
    try:
        write(snippet, 'PasswordAuthentication no\nKbdInteractiveAuthentication no\n'
              'PermitRootLogin prohibit-password\nPubkeyAuthentication yes\n', 0o644)
        write(main, include + '\n' + original, main_mode)
        run('/usr/sbin/sshd', '-t')
        for context in [None, p['core_public_ip'], '203.0.113.1']:
            args = ['/usr/sbin/sshd', '-T']
            if context:
                args += ['-C', f'user=root,host={context},addr={context}']
            effective = dict(line.split(None, 1) for line in
                             subprocess.check_output(args, stderr=subprocess.DEVNULL).decode().splitlines())
            assert effective['passwordauthentication'] == 'no'
            assert effective['kbdinteractiveauthentication'] == 'no'
            assert effective['permitrootlogin'] in ['prohibit-password', 'without-password']
            assert effective['pubkeyauthentication'] == 'yes'
        run('systemctl', 'reload', 'ssh')
    except Exception:
        write(main, old_main, main_mode)
        if old_snippet is None:
            snippet.unlink(missing_ok=True)
        else:
            write(snippet, old_snippet, snippet_mode)
        # Keep the previous, working SSH configuration if validation/reload fails.
        run('/usr/sbin/sshd', '-t')
        run('systemctl', 'reload', 'ssh')
        raise
    return {'ssh_key_only': True}


def configure(p):
    node = Path('/opt/remnanode')
    node.mkdir(mode=0o700, exist_ok=True)
    secret = p['node_secret']
    assert secret and '\n' not in secret and '\r' not in secret
    write(node / '.env', 'NODE_PORT=2222\nSECRET_KEY=' + secret + '\n')
    write(node / 'docker-compose.yml', f'''services:
  remnanode:
    image: {p['image']}
    container_name: remnanode
    hostname: remnanode
    restart: always
    network_mode: host
    env_file: .env
    ulimits:
      nofile:
        soft: 1048576
        hard: 1048576
    logging:
      driver: local
      options:
        max-size: "10m"
        max-file: "3"
''')
    key = Path('/etc/wireguard/control.key').read_text().strip()
    write('/etc/wireguard/wg-control.conf',
          f'[Interface]\nAddress = {p["management_ip"]}/32\nListenPort = 51820\nPrivateKey = {key}\nMTU = 1420\n'
          f'\n[Peer]\nPublicKey = {p["core_wg_public_key"]}\nAllowedIPs = 10.77.0.1/32\n'
          f'Endpoint = {p["core_public_ip"]}:51820\nPersistentKeepalive = 25\n')
    write('/etc/kingline-firewall.nft', f'''table inet kingline_filter {{
 chain input {{
  type filter hook input priority -10; policy drop;
  ct state invalid drop
  ct state established,related accept
  iifname "lo" accept
  ip protocol icmp accept
  meta l4proto ipv6-icmp accept
  tcp dport {{ {p['ssh_port']}, 443 }} accept
  ip saddr {p['core_public_ip']} udp dport 51820 accept
  iifname "wg-control" ip saddr 10.77.0.1 tcp dport 2222 accept
 }}
}}
''')
    write('/usr/local/sbin/kingline-firewall', '''#!/bin/bash
set -euo pipefail
temp=$(mktemp)
trap 'rm -f "$temp"' EXIT
if nft list table inet kingline_filter >/dev/null 2>&1; then
 printf 'delete table inet kingline_filter\\n' > "$temp"
fi
cat /etc/kingline-firewall.nft >> "$temp"
nft --check -f "$temp"
nft -f "$temp"
''', 0o700)
    write('/etc/systemd/system/kingline-firewall.service', '''[Unit]
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
''', 0o644)
    write('/etc/systemd/system/docker.service.d/20-kingline-firewall.conf',
          '[Unit]\nRequires=kingline-firewall.service\nAfter=kingline-firewall.service\n', 0o644)
    write('/etc/fail2ban/jail.d/kingline.conf',
          f'[sshd]\nenabled = true\nbackend = systemd\nport = {p["ssh_port"]}\nbanaction = nftables-multiport\n', 0o644)
    write('/etc/modules-load.d/kingline-bbr.conf', 'tcp_bbr\n', 0o644)
    run('modprobe', 'tcp_bbr')
    write('/etc/sysctl.d/90-kingline-vpn.conf',
          'net.core.default_qdisc=fq\nnet.ipv4.tcp_congestion_control=bbr\nnet.ipv4.tcp_mtu_probing=1\n', 0o644)
    run('sysctl', '-p', '/etc/sysctl.d/90-kingline-vpn.conf')
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', 'kingline-firewall', 'wg-quick@wg-control', 'fail2ban')
    run('systemctl', 'reload', 'kingline-firewall')
    run('docker', 'compose', 'config', '--quiet', cwd=node)
    run('docker', 'compose', 'pull', cwd=node, timeout=600)
    run('docker', 'compose', 'up', '-d', cwd=node, timeout=120)
    # The manager key was installed and tested before this phase. Do not leave the
    # initial provider password usable for SSH after automatic provisioning.
    harden_ssh(p)
    return {'configured': True, 'reboot_required': Path('/var/run/reboot-required').exists()}


if __name__ == '__main__':
    try:
        payload = json.load(sys.stdin)
        Path('/opt/kingline-node').mkdir(mode=0o700, exist_ok=True)
        lock = Path('/opt/kingline-node/install.lock').open('a')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = {'base': base, 'configure': configure, 'ssh': harden_ssh}[sys.argv[1]](payload)
        print(json.dumps(result))
    except Exception:
        # Deliberately avoid echoing subprocess arguments, payloads, and secrets.
        raise SystemExit(1)
