"""Local-only Remnawave API helper. Never log returned credentials."""
import json
from pathlib import Path
import urllib.error
import urllib.request

class RemnaAPI:
    def __init__(self, token_file=None):
        self.headers = {'Content-Type': 'application/json', 'X-Forwarded-Proto': 'https',
                        'X-Forwarded-For': '127.0.0.1'}
        if token_file:
            self.headers['Authorization'] = 'Bearer ' + Path(token_file).read_text().strip()
        else:
            credentials = json.loads(Path('/opt/remnawave/admin-credentials.json').read_text())
            self.headers['X-Remnawave-Client-Type'] = 'browser'
            login = self.call('auth/login', 'POST', {k: credentials[k] for k in ['username', 'password']})
            self.headers['Authorization'] = 'Bearer ' + login['accessToken']

    def call(self, path, method='GET', body=None):
        request = urllib.request.Request('http://127.0.0.1:3000/api/' + path,
            headers=self.headers, method=method,
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw).get('response') if raw else None
        except urllib.error.HTTPError as error:
            # Validation messages can contain submitted secrets: keep response out of stdout.
            raise RuntimeError('Remnawave API ' + method + ' failed with HTTP ' + str(error.code)) from None

    def items(self, path, key):
        result = self.call(path)
        return result.get(key, result.get('items', [])) if isinstance(result, dict) else result
