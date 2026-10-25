"""Lifecycle cleanup for an explicitly enabled alternative mobile listener."""
REMOVE_MOBILE_FIREWALL = '''python3 - <<'PY'
from pathlib import Path
import subprocess
p=Path('/etc/kingline-firewall.nft')
old=p.read_text()
new=''.join(line for line in old.splitlines(keepends=True) if 'comment "kingline mobile 36847"' not in line)
if new!=old:
 p.write_text(new)
 try: subprocess.run(['/usr/local/sbin/kingline-firewall'],check=True)
 except Exception:
  p.write_text(old);raise
PY'''
