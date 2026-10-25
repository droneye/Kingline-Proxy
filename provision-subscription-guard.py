#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess

os.umask(0o077)
root = Path('/opt/remnawave')
folder = root / 'subscription-guard'
folder.mkdir(mode=0o700, exist_ok=True)
config = '''worker_processes 1;
pid /tmp/nginx.pid;
error_log /dev/stderr crit;
events { worker_connections 1024; }
http {
    access_log off;
    server_tokens off;
    client_body_temp_path /tmp/client_body;
    proxy_temp_path /tmp/proxy;
    fastcgi_temp_path /tmp/fastcgi;
    uwsgi_temp_path /tmp/uwsgi;
    scgi_temp_path /tmp/scgi;
    set_real_ip_from 172.16.0.0/12;
    real_ip_header X-Real-IP;
    limit_req_zone $binary_remote_addr zone=subscription_rate:10m rate=5r/s;
    limit_conn_zone $binary_remote_addr zone=subscription_connections:1m;
    resolver 127.0.0.11 valid=10s ipv6=off;
    server {
        listen 8080;
        client_max_body_size 32k;
        client_header_timeout 10s;
        client_body_timeout 10s;
        keepalive_timeout 15s;
        limit_req zone=subscription_rate burst=20 nodelay;
        limit_req_status 429;
        limit_conn subscription_connections 20;
        limit_conn_status 429;
        location / {
            set $subscription_backend remnawave-subscription-page:3010;
            proxy_pass http://$subscription_backend;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_set_header X-Forwarded-Proto https;
            proxy_connect_timeout 5s;
            proxy_read_timeout 30s;
            proxy_buffering off;
            add_header Cache-Control "no-store" always;
        }
    }
}
'''
(folder / 'nginx.conf').write_text(config)
(folder / 'nginx.conf').chmod(0o644)
compose = root / 'docker-compose.override.yml'
contents = compose.read_text()
service = '''  subscription-guard:
    image: nginx@sha256:970364228b1e4f932abc22d321932816782cca8a20b85a77552a7cfe9acfb3b5
    container_name: kingline-subscription-guard
    restart: always
    user: "101:101"
    entrypoint: ["nginx", "-g", "daemon off;"]
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    read_only: true
    mem_limit: 64m
    pids_limit: 64
    networks: [remnawave-network]
    volumes:
      - ./subscription-guard/nginx.conf:/etc/nginx/nginx.conf:ro
    tmpfs: [/tmp]
    logging:
      driver: local
      options:
        max-size: "5m"
        max-file: "3"
'''
if '  subscription-guard:' not in contents:
    compose.with_suffix('.yml.before-guard').write_text(contents)
    assert contents.count('\nvolumes:\n') == 1
    compose.write_text(contents.replace('\nvolumes:\n', '\n' + service + '\nvolumes:\n'))
subprocess.run(['docker', 'compose', 'config', '--quiet'], cwd=root, check=True)
subprocess.run(['docker', 'compose', 'up', '-d', '--force-recreate', 'subscription-guard'], cwd=root, check=True)
subprocess.run(['docker', 'exec', 'kingline-subscription-guard', 'nginx', '-t'], check=True)
caddy = root / 'caddy/Caddyfile'
content = caddy.read_text()
old = '        reverse_proxy remnawave-subscription-page:3010'
new = '''        reverse_proxy subscription-guard:8080 {
            header_up X-Real-IP {remote_host}
        }'''
if old in content:
    caddy.with_name('Caddyfile.before-guard').write_text(content)
    caddy.write_text(content.replace(old, new))
for action in ['validate', 'reload']:
    result = subprocess.run(['docker', 'exec', 'remnawave-caddy', 'caddy', action,
        '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'], capture_output=True)
    assert result.returncode == 0, 'Caddy configuration check failed'
print('Subscription rate limit enabled: 5 requests/sec per IP, burst 20; access logging disabled')
