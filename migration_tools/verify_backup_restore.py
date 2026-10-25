"""Restore the panel dump into a disposable database, never into the active one."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import subprocess
folder=Path('/opt/kingline-staging/backups')/('daily-'+datetime.now(timezone.utc).strftime('%Y%m%d'))
result={}
for name in ('vpn_bot.db','ref.db','promos.db'):
    with sqlite3.connect('file:'+str(folder/name)+'?mode=ro',uri=True) as c:
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    result[name]='verified'
name='kingline_restore_check'
try:
    subprocess.run(['docker','exec','remnawave-db','sh','-c','createdb -U "$POSTGRES_USER" '+name],check=True,capture_output=True,timeout=15)
    with (folder/'remnawave.dump').open('rb') as f:
        subprocess.run(['docker','exec','-i','remnawave-db','sh','-c','pg_restore --no-owner -U "$POSTGRES_USER" -d '+name],stdin=f,check=True,capture_output=True,timeout=30)
    r=subprocess.run(['docker','exec','remnawave-db','sh','-c',"psql -U \"$POSTGRES_USER\" -d "+name+" -tAc \"SELECT count(*) FROM information_schema.tables WHERE table_schema='public'\""],capture_output=True,check=True,text=True,timeout=10)
    assert int(r.stdout)>20
    result['remnawave_actual_restore']='verified'
finally:
    subprocess.run(['docker','exec','remnawave-db','sh','-c','dropdb --if-exists -U "$POSTGRES_USER" '+name],capture_output=True,timeout=15)
Path('/opt/kingline-staging/verification-backup-restore.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
