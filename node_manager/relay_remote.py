"""Runs over the already trusted SSH connection; fixed destinations, no shell input.

HAProxy forwards encrypted bytes, never terminates or generates VPN credentials.
Only our container/directory and our existing input firewall are touched.
"""
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

ROOT = Path('/opt/kingline-relay')
FIREWALL = Path('/etc/kingline-firewall.nft')
IMAGE = 'haproxy:3.2-alpine@sha256:dfba86f031bd4c9039f10f8cc5de75991d1b6766d5460f0335b980408503baad'
FILES = ('config/haproxy.cfg', 'compose.yml', 'identity.json')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs).stdout


def write(path, text, mode=0o600):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.new')
    temp.write_text(text); temp.chmod(mode); os.replace(temp, path)


def owned():
    result = subprocess.run(['docker', 'inspect', 'kingline-relay', '--format', '{{json .Config.Labels}}'], capture_output=True)
    if result.returncode == 0:
        assert json.loads(result.stdout).get('com.kingline.role') == 'relay', 'relay_container_not_owned'
    if ROOT.exists() and any((ROOT/f).exists() for f in FILES):
        assert (ROOT/'identity.json').exists(), 'relay_directory_not_owned'
        assert json.loads((ROOT/'identity.json').read_text()).get('kind') == 'kingline-tcp-relay'


def snapshot():
    owned()
    return {'files': {f: (ROOT/f).read_text() if (ROOT/f).exists() else None for f in FILES},
            'firewall': FIREWALL.read_text(), 'usage': usage()}


def usage():
    routes = json.loads(run('ip', '-j', '-4', 'route', 'show', 'default'))
    iface = next(r['dev'] for r in routes if r.get('dev'))
    assert re.fullmatch(r'[A-Za-z0-9_.:-]{1,32}', iface)
    folder = Path('/sys/class/net')/iface/'statistics'
    return {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'counter': sum(int((folder/k).read_text()) for k in ('rx_bytes', 'tx_bytes')), 'at': time.time()}


def firewall(ports):
    text = FIREWALL.read_text()
    assert 'table inet kingline_filter' in text and 'chain input' in text
    text = re.sub(r'^.*# kingline-relay-ports\n?', '', text, flags=re.M)
    if ports:
        rule = '        tcp dport { '+', '.join(str(p) for p in sorted(ports))+' } accept # kingline-relay-ports\n'
        # Keep invalid-state drop first; accepting here still needs a real listening socket.
        anchor = re.search(r'^.*iifname "lo" accept.*\n', text, re.M)
        assert anchor, 'firewall_layout_changed'
        text = text[:anchor.end()]+rule+text[anchor.end():]
    return text


def prepare(payload):
    owned()
    ports = payload['ports']
    assert all(isinstance(p, int) and 24000 <= p <= 24999 for p in ports)
    old = (ROOT/'config/haproxy.cfg').read_text() if (ROOT/'config/haproxy.cfg').exists() else ''
    old_ports = {int(v) for v in re.findall(r'^\s*bind 0\.0\.0\.0:(\d+)$', old, re.M)}
    for port in set(ports)-old_ports:
        with socket.socket() as s:
            s.bind(('0.0.0.0', port))
    text = firewall(ports)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    write(ROOT/'firewall.check', 'delete table inet kingline_filter\n'+text)
    run('nft', '--check', '-f', str(ROOT/'firewall.check'))
    if ports:
        if subprocess.run(['docker', 'image', 'inspect', IMAGE], capture_output=True).returncode:
            run('docker', 'pull', IMAGE, timeout=300)
        write(ROOT/'check.cfg', payload['config'], 0o644)
        run('docker', 'run', '--rm', '--network', 'none', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--pids-limit=32', '--memory=192m',
            '-v', str(ROOT/'check.cfg')+':/check.cfg:ro', IMAGE, 'haproxy', '-c', '-f', '/check.cfg', timeout=30)
    return {'ok': True}


def compose():
    return f'''services:
  relay:
    image: {IMAGE}
    container_name: kingline-relay
    labels:
      com.kingline.role: relay
    restart: unless-stopped
    network_mode: host
    user: "65532:65532"
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    pids_limit: 32
    mem_limit: 192m
    tmpfs:
      - /tmp:rw,nosuid,nodev,noexec,size=16m,uid=65532,gid=65532
    volumes:
      - ./config:/usr/local/etc/haproxy:ro
    command: ["haproxy", "-W", "-db", "-f", "/usr/local/etc/haproxy/haproxy.cfg", "-p", "/tmp/haproxy.pid"]
    logging:
      driver: local
      options:
        max-size: "2m"
        max-file: "2"
'''


def running():
    r = subprocess.run(['docker', 'inspect', 'kingline-relay', '--format', '{{.State.Running}}'], capture_output=True)
    return r.returncode == 0 and r.stdout.strip() == b'true'


def workers():
    result=subprocess.run(['docker','inspect','kingline-relay','--format','{{.State.Pid}}'],capture_output=True)
    if result.returncode:return set()
    pid=int(result.stdout)
    try:return set(Path(f'/proc/{pid}/task/{pid}/children').read_text().split())
    except OSError:return set()


def activate(ports):
    run('/usr/local/sbin/kingline-firewall')
    if ports:
        previous_workers=workers()
        if running():
            run('docker', 'kill', '--signal=USR2', 'kingline-relay')
        else:
            run('docker', 'compose', '-f', str(ROOT/'compose.yml'), 'up', '-d', timeout=90)
        for _ in range(60):
            ready = running() and bool(workers()-previous_workers)
            for port in ports:
                try:
                    with socket.create_connection(('127.0.0.1', port), timeout=.5): pass
                except OSError:
                    ready = False; break
            if ready:
                return
            time.sleep(.5)
        raise RuntimeError('relay_ports_not_ready')
    elif (ROOT/'compose.yml').exists():
        run('docker', 'compose', '-f', str(ROOT/'compose.yml'), 'down', timeout=60)


def apply(payload):
    prepare(payload)
    write(ROOT/'identity.json', json.dumps({'kind': 'kingline-tcp-relay', 'version': 1}))
    (ROOT/'config').mkdir(exist_ok=True, mode=0o755)
    (ROOT/'config').chmod(0o755)
    write(ROOT/'config/haproxy.cfg', payload['config'], 0o644)
    write(ROOT/'compose.yml', compose())
    write(FIREWALL, firewall(payload['ports']))
    activate(payload['ports'])
    return {'ok': True}


def restore(before):
    owned()
    old = before['files']['config/haproxy.cfg'] or ''
    ports = [int(v) for v in re.findall(r'^\s*bind 0\.0\.0\.0:(\d+)$', old, re.M)]
    write(FIREWALL, before['firewall'])
    if old:
        for name, content in before['files'].items():
            if content is not None: write(ROOT/name, content, 0o644 if name.endswith('.cfg') else 0o600)
        activate(ports)
    else:
        activate([])
        for name, content in before['files'].items():
            if content is None: (ROOT/name).unlink(missing_ok=True)
    return {'ok': True}


if __name__ == '__main__':
    os.umask(0o077)
    payload = json.load(sys.stdin)
    action = payload['action']
    result = {'snapshot': snapshot, 'stats': usage}.get(action)
    if result: output = result()
    elif action == 'check': output = prepare(payload)
    elif action == 'apply': output = apply(payload)
    elif action == 'restore': output = restore(payload['before'])
    else: raise ValueError('invalid_action')
    print(json.dumps(output))
