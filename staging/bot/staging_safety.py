"""Staging delivery boundaries, independent of legacy notification handlers."""
import json
import os
from pathlib import Path
import secrets
import time

def capture_mail(email, subject, text, html=None):
    folder = Path(os.environ['MAIL_CAPTURE_DIR'])
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (str(time.time_ns()) + '-' + secrets.token_hex(4) + '.json')
    with path.open('x') as f:
        os.chmod(path, 0o600)
        json.dump({'to': email, 'subject': subject, 'text': text, 'html': html}, f, ensure_ascii=False)

