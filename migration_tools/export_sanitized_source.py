"""Read-only source export: no environment files, databases, logs or live credentials."""
import ast
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile

ROOTS = [Path('/root/kingline'), Path('/root/kingline_site')]
SENSITIVE = re.compile(r'token|password|passwd|secret|api.?key|private.?key|webbasepath', re.I)
TEXT = {'.py', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.json', '.md', '.txt', '.svg', '.yml', '.yaml'}
BINARY = {'.png', '.jpg', '.jpeg', '.webp', '.ico', '.woff', '.woff2', '.ttf'}
SKIP = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', 'dist', '.cache', 'backups', 'backup', 'uploads', 'support_uploads'}
secrets = set()
for root in ROOTS:
    for env in [root / '.env', root / 'backend/.env']:
        if env.exists():
            for line in env.read_text().splitlines():
                if '=' in line and not line.lstrip().startswith('#'):
                    key, value = line.split('=', 1)
                    value = value.strip().strip('"\'')
                    if SENSITIVE.search(key) and len(value) >= 6:
                        secrets.add(value)
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('.')]
        for name in files:
            if not name.endswith('.py'):
                continue
            try:
                tree = ast.parse((Path(base) / name).read_text())
                for n in ast.walk(tree):
                    if isinstance(n, ast.Assign) and any(SENSITIVE.search(ast.unparse(t)) for t in n.targets):
                        for c in ast.walk(n.value):
                            if isinstance(c, ast.Constant) and isinstance(c.value, str) and len(c.value) >= 12:
                                if not c.value.isupper() and ' ' not in c.value:
                                    secrets.add(c.value)
            except (OSError, SyntaxError, UnicodeError):
                pass
count = redactions = 0
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as archive:
    for root in ROOTS:
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith('.')]
            for name in files:
                path = Path(base) / name
                if (name.startswith('.') or '.bak' in name or path.suffix not in TEXT | BINARY
                        or path.is_symlink() or path.stat().st_size > 10_000_000):
                    continue
                value = path.read_bytes()
                if path.suffix in TEXT:
                    try:
                        text = value.decode()
                    except UnicodeError:
                        continue
                    original = text
                    for secret in sorted(secrets, key=len, reverse=True):
                        text = text.replace(secret, '__REDACTED_FOR_STAGING__')
                    text = re.sub(r'\b[0-9]{7,12}:[A-Za-z0-9_-]{30,50}\b', '__REDACTED_BOT_TOKEN__', text)
                    text = re.sub(r'(?<=://)[^/\s:@]+:[^/\s@]+@', '__REDACTED_CREDENTIALS__@', text)
                    redactions += text != original
                    value = text.encode()
                info = tarfile.TarInfo(root.name + '/' + str(path.relative_to(root)))
                info.size = len(value)
                info.mode = 0o600
                archive.addfile(info, io.BytesIO(value))
                count += 1
print(json.dumps({'exported_files': count, 'redacted_files': redactions,
                  'environment_database_logs_excluded': True}), file=sys.stderr)
