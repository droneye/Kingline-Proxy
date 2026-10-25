#!/usr/bin/env python3
"""Install/reload a local HTTPS target. Certificate material is accepted only on stdin."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import ssl
import subprocess
import sys
import time

os.umask(0o077)


def run(args, **kw):
    return subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)


def write(path, value, mode=0o600):
    path.write_text(value)
    path.chmod(mode)


def scanner_passed(output, address, domain):
    # Upstream's asynchronous CSV writer can lose its final row on process exit.
    # Its synchronous completed-handshake log is also checked, independently of
    # our verified-certificate TLS/ALPN handshake above.
    for line in output.splitlines():
        values = dict(token.split('=', 1) for token in shlex.split(line) if '=' in token)
        if (values.get('msg') == 'Connected to target' and values.get('feasible') == 'true'
                and values.get('ip') == address and values.get('tls') == 'TLS 1.3'
                and values.get('alpn') == 'h2' and values.get('cert-domain') in (domain, '*.' + domain)):
            return True
    return False


def install(p):
    domain = p['domain']
    assert domain == 'edge.kingline.online'
    assert re.fullmatch(r'nginx@sha256:[0-9a-f]{64}', p['image'])
    path = p.get('xhttp_path')
    if path is not None:
        assert re.fullmatch(r'/[A-Za-z0-9/_-]{8,160}/', path)
    xhttp = '' if not path else f'''
  location ^~ {path} {{
   grpc_pass grpc://127.0.0.1:10080;
   grpc_set_header Host $host;
   grpc_set_header X-Forwarded-For $remote_addr;
   grpc_read_timeout 3600s;
   grpc_send_timeout 3600s;
   client_body_timeout 3600s;
   client_max_body_size 8m;
  }}
'''
    root = Path('/opt/kingline-cover')
    if not (root / 'docker-compose.yml').exists():
        for host, port in [('0.0.0.0', 80), ('127.0.0.1', 8443)]:
            with socket.socket() as sock:
                sock.bind((host, port))
    root.mkdir(exist_ok=True, mode=0o755)
    root.chmod(0o755)
    previous_files = {name: (root / name).read_text() if (root / name).exists() else None
                      for name in ['nginx.conf', 'docker-compose.yml']}
    revision = hashlib.sha256((p['certificate'] + p['private_key'] + (path or '')).encode()).hexdigest()[:24]
    releases = root / 'releases'
    releases.mkdir(exist_ok=True, mode=0o755)
    release = releases / revision
    release.mkdir(exist_ok=True, mode=0o755)
    write(release / 'fullchain.pem', p['certificate'])
    write(release / 'privkey.pem', p['private_key'])
    run(['openssl', 'x509', '-in', str(release / 'fullchain.pem'), '-noout', '-checkend', '604800'])
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(release / 'fullchain.pem', release / 'privkey.pem')
    web = root / 'www'
    web.mkdir(exist_ok=True, mode=0o755)
    web.chmod(0o755)
    write(web / 'index.html', '''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kingline</title><style>body{font:18px system-ui;background:#f6f7fa;color:#202632;max-width:48rem;margin:15vh auto;padding:2rem}h1{font-size:3rem;letter-spacing:-.04em}p{line-height:1.6;color:#536075}small{color:#697589}</style>
<main><small>KINGLINE</small><h1>A place for what comes next.</h1><p>Our new home is taking shape.</p><p>More information will be available here soon.</p></main></html>
''', 0o644)
    write(root / 'nginx.conf', f'''user nginx;
worker_processes 1;
pid /var/run/nginx.pid;
error_log /dev/stderr warn;
events {{ worker_connections 1024; }}
http {{
 include /etc/nginx/mime.types;
 default_type application/octet-stream;
 access_log off;
 server_tokens off;
 sendfile on;
 keepalive_timeout 65;
 server {{
  listen 80 default_server;
  server_name {domain};
  return 308 https://{domain}$request_uri;
 }}
 server {{
  listen 127.0.0.1:8443 ssl default_server;
  http2 on;
  server_name {domain};
  ssl_certificate /cover/current/fullchain.pem;
  ssl_certificate_key /cover/current/privkey.pem;
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_session_cache shared:TLS:4m;
  ssl_session_timeout 1h;
  ssl_session_tickets off;
  root /cover/www;
  index index.html;
  {xhttp}
  location / {{ try_files $uri $uri/ =404; }}
 }}
}}
''', 0o644)
    write(root / 'docker-compose.yml', f'''services:
  https:
    image: {p['image']}
    container_name: kingline-cover
    restart: always
    network_mode: host
    read_only: true
    cap_drop: [ALL]
    cap_add: [CHOWN, SETUID, SETGID, NET_BIND_SERVICE]
    security_opt: [no-new-privileges:true]
    entrypoint: [nginx]
    command: [-g, "daemon off;", -c, /cover/nginx.conf]
    volumes: ["/opt/kingline-cover:/cover:ro"]
    tmpfs: [/var/cache/nginx, /var/run]
    mem_limit: 96m
    pids_limit: 64
    logging:
      driver: local
      options: {{max-size: "5m", max-file: "2"}}
''')
    current = root / 'current'
    previous = os.readlink(current) if current.is_symlink() else None
    link = root / 'next'
    link.unlink(missing_ok=True)
    link.symlink_to('releases/' + revision)
    link.replace(current)
    try:
        run(['docker', 'compose', 'pull'], cwd=root, timeout=300)
        run(['docker', 'compose', 'run', '--rm', '--no-deps', 'https', '-t', '-c', '/cover/nginx.conf'],
            cwd=root, timeout=30)
        run(['docker', 'compose', 'up', '-d'], cwd=root, timeout=60)
        run(['docker', 'exec', 'kingline-cover', 'nginx', '-s', 'reload', '-c', '/cover/nginx.conf'])
        verify = ssl.create_default_context()
        verify.minimum_version = ssl.TLSVersion.TLSv1_3
        verify.set_alpn_protocols(['h2'])
        last = None
        for _ in range(10):
            try:
                with socket.create_connection(('127.0.0.1', 8443), timeout=5) as tcp:
                    with verify.wrap_socket(tcp, server_hostname=domain) as tls:
                        assert tls.version() == 'TLSv1.3' and tls.selected_alpn_protocol() == 'h2'
                        peer = tls.getpeercert()
                last = None
                break
            except Exception as error:
                last = error
                time.sleep(.3)
        if last:
            raise last
        scanner = Path('/opt/kingline-node/RealiTLScanner')
        assert hashlib.sha256(scanner.read_bytes()).hexdigest() == p['scanner_sha256']
        targets = root / 'scanner-targets.txt'
        write(targets, '127.0.0.1\n')
        output = root / 'scanner-result.csv'
        scan = subprocess.run([str(scanner), '-in', str(targets), '-port', '8443', '-thread', '1',
             '-timeout', '5', '-out', str(output)], timeout=15, check=True, capture_output=True, text=True)
        assert scanner_passed(scan.stdout, '127.0.0.1', domain)
    except Exception:
        for name, value in previous_files.items():
            if value is not None:
                write(root / name, value, 0o644 if name == 'nginx.conf' else 0o600)
        if previous:
            link.unlink(missing_ok=True)
            link.symlink_to(previous)
            link.replace(current)
            run(['docker', 'exec', 'kingline-cover', 'nginx', '-s', 'reload', '-c', '/cover/nginx.conf'])
        raise
    firewall = Path('/etc/kingline-firewall.nft')
    old = firewall.read_text()
    marker = '  tcp dport 80 accept comment "kingline HTTPS redirect"\n'
    if marker not in old:
        assert 'ct state invalid drop' in old
        updated = re.sub(r'(?m)^(\s*)ct state invalid drop$',
                         lambda m: m.group(0) + '\n' + marker.rstrip('\n'), old, count=1)
        assert updated != old
        firewall.write_text(updated)
        try:
            run(['/usr/local/sbin/kingline-firewall'])
        except Exception:
            firewall.write_text(old)
            raise
    return {'domain': domain, 'target': '127.0.0.1:8443', 'tls': 'TLSv1.3', 'alpn': 'h2',
            'certificate_expires': peer['notAfter'], 'scanner_verified': True, 'revision': revision}


if __name__ == '__main__':
    try:
        print(json.dumps(install(json.load(sys.stdin))))
    except Exception:
        # Never expose submitted certificate/private key data in logs or API errors.
        print('HTTPS target installation failed', file=sys.stderr)
        raise SystemExit(1)
