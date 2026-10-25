"""CLI for the private API. SSH credentials are read interactively or from a local key file."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
import uuid


def main():
    parser = argparse.ArgumentParser(prog='kingline-node')
    parser.add_argument('--url', default='http://127.0.0.1:8090')
    parser.add_argument('--token-file', default=os.environ.get('KINGLINE_ADMIN_TOKEN_FILE', '/opt/kingline-manager/data/admin.token'))
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ['add', 'preflight']:
        sub = commands.add_parser(name)
        sub.add_argument('--ip', required=True)
        sub.add_argument('--name')
        sub.add_argument('--label', help='Legacy full subscription label, including emoji')
        sub.add_argument('--display-name', help='Visible country/location name')
        sub.add_argument('--flag-code', help='Two-letter flag code, defaults to country')
        sub.add_argument('--country', required=True)
        sub.add_argument('--quota-tb', type=float, required=True)
        sub.add_argument('--speed-mbps', type=float, required=True)
        sub.add_argument('--reset-day', type=int, default=19)
        sub.add_argument('--ssh-port', type=int, default=22)
        sub.add_argument('--host-key-sha256')
        auth = sub.add_mutually_exclusive_group()
        auth.add_argument('--password', action='store_true', help='Prompt securely; never pass the password in argv')
        auth.add_argument('--ssh-key', type=Path)
        sub.add_argument('--key-passphrase', action='store_true')
        sub.add_argument('--idempotency-key', default=None)
    commands.add_parser('list')
    commands.add_parser('standard')
    commands.add_parser('jobs')
    sub = commands.add_parser('job'); sub.add_argument('id')
    sub = commands.add_parser('retry'); sub.add_argument('id'); sub.add_argument('--password', action='store_true')
    sub.add_argument('--ssh-key', type=Path); sub.add_argument('--key-passphrase', action='store_true')
    sub = commands.add_parser('capacity'); sub.add_argument('id')
    sub.add_argument('--quota-tb', type=float); sub.add_argument('--speed-mbps', type=float)
    sub.add_argument('--reset-day', type=int); sub.add_argument('--maintenance', choices=['on', 'off'])
    sub = commands.add_parser('remove'); sub.add_argument('id')
    sub.add_argument('--name', required=True); sub.add_argument('--ip', required=True)
    sub = commands.add_parser('removal'); sub.add_argument('id')
    args = parser.parse_args()
    body, method, headers = None, 'GET', {}
    command = args.command
    if command in ['add', 'preflight', 'retry']:
        credentials = {}
        if args.password:
            credentials['password'] = getpass.getpass('SSH root password: ')
        if args.ssh_key:
            credentials['private_key'] = args.ssh_key.read_text()
        if args.key_passphrase:
            credentials['passphrase'] = getpass.getpass('SSH key passphrase: ')
        if command == 'retry':
            path, body = '/v1/jobs/' + args.id + '/retry', credentials
        else:
            body = {k: getattr(args, k) for k in ['ip', 'name', 'label', 'display_name', 'flag_code', 'country', 'quota_tb',
                    'speed_mbps', 'reset_day', 'ssh_port', 'host_key_sha256']}
            body['credentials'] = credentials
            path = '/v1/nodes' + ('/preflight' if command == 'preflight' else '')
            headers['Idempotency-Key'] = args.idempotency_key or str(uuid.uuid4())
            print('Idempotency-Key: ' + headers['Idempotency-Key'], file=sys.stderr)
        method = 'POST'
    elif command == 'capacity':
        path, method = '/v1/nodes/' + args.id, 'PATCH'
        body = {k: getattr(args, k) for k in ['quota_tb', 'speed_mbps', 'reset_day'] if getattr(args, k) is not None}
        if args.maintenance:
            body['maintenance'] = args.maintenance == 'on'
    elif command == 'remove':
        path, method = '/v1/nodes/' + args.id, 'DELETE'
        body = {'name': args.name, 'ip': args.ip}
    else:
        path = {'list': '/v1/nodes', 'standard': '/v1/standard', 'jobs': '/v1/jobs',
                'removal': '/v1/removals/' + getattr(args, 'id', ''),
                'job': '/v1/jobs/' + getattr(args, 'id', '')}[command]
    headers.update({'Authorization': 'Bearer ' + Path(args.token_file).read_text().strip(),
                    'Content-Type': 'application/json'})
    request = urllib.request.Request(args.url.rstrip('/') + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
    except urllib.error.HTTPError as error:
        print(error.read().decode(), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
