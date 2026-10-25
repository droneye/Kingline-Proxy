"""Lossless stronger compression; source preserved until byte-for-byte verified."""
from datetime import datetime,timezone
import gzip
import hashlib
import json
import lzma
import os
from pathlib import Path
os.umask(0o077)
root=Path('/opt/kingline-staging/archive');source=root/'legacy-technical-20261022.jsonl.gz'
target=root/'legacy-technical-20261022.jsonl.xz';temporary=target.with_suffix('.xz.tmp')
hash1=hashlib.sha256();size=0
with gzip.open(source,'rb') as src,lzma.open(temporary,'wb',preset=6) as dst:
    while block:=src.read(1024*1024):hash1.update(block);size+=len(block);dst.write(block)
hash2=hashlib.sha256()
with lzma.open(temporary,'rb') as src:
    while block:=src.read(1024*1024):hash2.update(block)
assert hash1.digest()==hash2.digest()
os.chmod(temporary,0o600);temporary.replace(target)
report={'lossless':True,'rawBytes':size,'oldBytes':source.stat().st_size,'newBytes':target.stat().st_size,'rawSha256':hash2.hexdigest(),'verifiedAt':datetime.now(timezone.utc).isoformat()}
(root/'legacy-technical-20261022.xz.verified.json').write_text(json.dumps(report,indent=2))
# Retain the source as a rollback copy until the current inspection is complete.
print(json.dumps(report))
