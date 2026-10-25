"""Install the explicitly requested staging business data in a private directory."""
import base64
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

os.umask(0o077)
root = Path('/opt/kingline-staging')
data = root / 'data'
data.mkdir(parents=True, mode=0o700, exist_ok=True)
manifest = json.loads(gzip.decompress(sys.stdin.buffer.read()))
for name, item in manifest['files'].items():
    assert name in ['vpn_bot.db', 'ref.db', 'promos.db', 'site-ref.db']
    path = data / name
    assert not path.exists(), 'Never overwrite a running staging database'
    content = base64.b64decode(item.pop('data'))
    assert hashlib.sha256(content).hexdigest() == item['sha256']
    path.write_bytes(content)
    path.chmod(0o600)
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
manifest['copied_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
manifest['legacy_source_unchanged'] = True
manifest['technical_logs_separate'] = True
(root / 'import-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps({'database_bytes': {k: v['bytes'] for k, v in manifest['files'].items()},
                  'counts': manifest['counts']}))
