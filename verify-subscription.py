#!/usr/bin/env python3
"""Run on the control server. Creates and removes a temporary account with no squads."""
import datetime
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import urllib.error
import urllib.request

os.umask(0o077)
root = Path('/opt/remnawave')
headers = {'Content-Type': 'application/json', 'X-Forwarded-Proto': 'https',
           'X-Forwarded-For': '127.0.0.1', 'X-Remnawave-Client-Type': 'browser'}

def api(path, method='GET', body=None):
    req = urllib.request.Request('http://127.0.0.1:3000/api/' + path, method=method,
        headers=headers, data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = response.read()
            return json.loads(payload).get('response') if payload else None
    except urllib.error.HTTPError as error:
        raise RuntimeError('Panel API returned HTTP ' + str(error.code)) from None

credentials = json.loads((root / 'admin-credentials.json').read_text())
headers['Authorization'] = 'Bearer ' + api('auth/login', 'POST',
    {k: credentials[k] for k in ['username', 'password']})['accessToken']
user = api('users', 'POST', {
    'username': 'infra_check_' + secrets.token_hex(5),
    'expireAt': (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat(),
    'activeInternalSquads': [],
    'description': 'Temporary infrastructure verification; automatically deleted',
})
report = {}
try:
    assert user.get('id') is not None, 'API user ID missing'
    url = 'https://connect.kingline.online/' + user['shortUuid']
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
    })
    with browser.open(request, timeout=30) as response:
        html = response.read().decode()
        assert response.status == 200 and '<html' in html.lower(), 'Subscription HTML check failed'
        report['personal_subscription_page_https'] = response.status
        report['html_bytes'] = len(html)
    request = urllib.request.Request(url, headers={'User-Agent': 'v2rayN/7.0', 'Accept': '*/*'})
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()
        assert response.status == 200, 'Subscription client endpoint failed'
        report['subscription_client_endpoint_https'] = response.status
    report['verified_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
finally:
    api('users/' + str(user['id']), 'DELETE')
    report['temporary_user_deleted'] = True

(root / 'verification-subscription.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
