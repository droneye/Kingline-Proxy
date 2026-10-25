"""Read-only streaming archive of legacy technical tables, separate from accounts."""
import gzip
import json
import sqlite3
import sys
import time

c = sqlite3.connect('file:/root/kingline/vpn_bot.db?mode=ro', uri=True, timeout=15)
c.execute('PRAGMA query_only=ON')
counts = {}
with gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', compresslevel=1) as archive:
    def write(item):
        archive.write((json.dumps(item, ensure_ascii=False, separators=(',',':')) + '\n').encode())
    write({'format': 'kingline-technical-history-v1', 'started_at': time.time(), 'source': 'legacy-read-only'})
    for name, sql in c.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name LIKE 'web_xui_%'").fetchall():
        assert name.replace('_','').isalnum()
        c.execute('BEGIN')
        cursor = c.execute('SELECT * FROM "' + name + '"')
        write({'table': name, 'schema': sql, 'columns': [d[0] for d in cursor.description]})
        count = 0
        while rows := cursor.fetchmany(2000):
            write({'rows': rows})
            count += len(rows)
            time.sleep(.008)
        write({'table_complete': name, 'rows': count})
        c.rollback()
        counts[name] = count
        print(json.dumps({'archived_table': name, 'rows': count}), file=sys.stderr, flush=True)
    write({'complete': True, 'tables': counts, 'finished_at': time.time()})
c.close()
