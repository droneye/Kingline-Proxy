"""One-off RF candidate check; leave the hourly manifest and old VPN untouched."""
import fcntl
import json
import os
from pathlib import Path
import pwd
import runpy
import subprocess
import sys

if __name__ == '__main__':
    payload = json.load(sys.stdin)
    try:
        account = pwd.getpwnam('kingline-probe')
        os.setgroups([]); os.setgid(account.pw_gid); os.setuid(account.pw_uid)
        worker = runpy.run_path('/opt/kingline-ru-monitor/worker.py')
        with Path('/opt/kingline-ru-monitor/state/worker.lock').open('a') as lock:
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({'source_ok': False, 'error': 'ru_probe_busy'})); sys.exit(0)
            manifest, target = payload['manifest'], payload['target']
            reference = worker['transfer'](manifest['sample_url'], None, 2048, small=True,
                                           resolve=manifest['sample_resolve'])
            clock = subprocess.check_output(['timedatectl', 'show', '-p', 'NTPSynchronized', '--value'],
                                            text=True, timeout=5).strip() == 'yes'
            source_ok = bool(reference['ok'] and clock and
                             reference.get('source_ip') == manifest['expected_source_ip'])
            results = {}
            if source_ok:
                results['raw'] = worker['check'](target, manifest)
                if payload.get('xhttp_path'):
                    results['xhttp'] = worker['check']({**target, 'transport': 'xhttp',
                                                        'xhttp_path': payload['xhttp_path']}, manifest)
            print(json.dumps({'source_ok': source_ok, 'transports': results,
                              'error': None if source_ok else 'ru_source_invalid'}))
    except Exception:
        print(json.dumps({'source_ok': False, 'error': 'ru_probe_unavailable'}))
