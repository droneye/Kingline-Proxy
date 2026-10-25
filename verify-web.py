#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile

os.umask(0o077)
root = Path(__file__).resolve().parent
credentials = json.loads((root / 'private/admin-credentials.json').read_text())
report = {}

def curl(url, *args):
    result = subprocess.run(['/usr/bin/curl', '--silent', '--show-error', '--connect-timeout', '10',
                             '--max-time', '30', *args, url], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('HTTPS request failed, curl exit code ' + str(result.returncode))
    return result.stdout

with tempfile.TemporaryDirectory(prefix='verify-', dir=root / 'private') as temporary:
    cookie_file = str(Path(temporary) / 'cookies.txt')
    code = curl(credentials['panel_url'], '-o', '/dev/null', '-w', '%{http_code}')
    assert code == '200', 'Normal panel login page is unavailable'
    report['panel_without_entry'] = int(code)
    code = curl(credentials['panel_url'] + '/entry/' + credentials['entry_token'],
                '-L', '-c', cookie_file, '-b', cookie_file, '-o', '/dev/null', '-w', '%{http_code}')
    assert code == '200', 'Panel entry failed'
    report['panel_with_entry'] = int(code)
    code = curl(credentials['panel_url'] + '/api/nodes', '-o', '/dev/null', '-w', '%{http_code}')
    assert code in ['401', '403'], 'Management API is accessible without authentication'
    report['unauthenticated_management_api'] = int(code)
    status = json.loads(curl(credentials['panel_url'] + '/api/auth/status', '-b', cookie_file))['response']
    assert status['isLoginAllowed'] and not status['isRegisterAllowed']
    body = Path(temporary) / 'login.json'
    body.write_text(json.dumps({key: credentials[key] for key in ('username', 'password')}))
    login = json.loads(curl(credentials['panel_url'] + '/api/auth/login', '-b', cookie_file,
                           '-H', 'Content-Type: application/json', '--data-binary', '@' + str(body)))
    assert login['response']['accessToken']
    report['admin_login'] = 'passed'
    report['registration'] = 'closed'
    code = curl(credentials['subscription_url'], '-o', '/dev/null', '-w', '%{http_code}')
    assert code == '404', 'Unexpected subscription root status'
    report['subscription_root_without_personal_link'] = int(code)
    report['tls_certificate_and_hostname_validation'] = 'passed for both domains'
(root / 'verification-web.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False))
