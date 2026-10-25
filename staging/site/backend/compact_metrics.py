"""Bounded operational analytics, separate from users, payments and credentials."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time

EVENT_DAYS = 7
SUMMARY_DAYS = 90
MAX_PAGES = 32768  # 128 MiB at SQLite's default 4096-byte page size.
TABLE_RE = re.compile(r'\bweb_xui_[a-z_]+\b')


def iso(at):
    return datetime.fromtimestamp(at, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def open_db(path):
    c=sqlite3.connect(path,timeout=15)
    c.execute(f'PRAGMA max_page_count={MAX_PAGES}')
    c.execute('PRAGMA journal_size_limit=8388608')
    return c


def prepare(path, business):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(open_db(path)) as c, c:
        c.execute('PRAGMA auto_vacuum=INCREMENTAL')
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA journal_size_limit=8388608')
        c.execute(f'PRAGMA max_page_count={MAX_PAGES}')
        tables = business.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name LIKE 'web_xui_%'").fetchall()
        for name, sql in tables:
            if name == 'web_xui_access_events': continue
            c.execute(sql.replace(name, 'metrics_' + name, 1).replace('CREATE TABLE ', 'CREATE TABLE IF NOT EXISTS ', 1))
        c.executescript('''
        CREATE TABLE IF NOT EXISTS dimensions(id INTEGER PRIMARY KEY,value TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS access_buckets(
            id INTEGER PRIMARY KEY,bucket INTEGER NOT NULL,last_at INTEGER NOT NULL,
            user_id INTEGER NOT NULL,ip_id INTEGER NOT NULL,node_id INTEGER NOT NULL,domain_id INTEGER NOT NULL,
            port INTEGER NOT NULL,count INTEGER NOT NULL,
            UNIQUE(bucket,user_id,ip_id,node_id,domain_id,port));
        CREATE INDEX IF NOT EXISTS access_user_time ON access_buckets(user_id,last_at);
        CREATE INDEX IF NOT EXISTS access_time ON access_buckets(last_at);
        CREATE TABLE IF NOT EXISTS domain_daily(
            day TEXT NOT NULL,user_id INTEGER NOT NULL,domain_id INTEGER NOT NULL,
            count INTEGER NOT NULL,last_at INTEGER NOT NULL,
            PRIMARY KEY(day,user_id,domain_id)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS ingest_batches(id TEXT PRIMARY KEY,at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS client_error_counts(day TEXT,kind TEXT,count INTEGER,PRIMARY KEY(day,kind));
        CREATE INDEX IF NOT EXISTS samples_user_time ON metrics_web_xui_user_samples(username,sampled_at);
        CREATE INDEX IF NOT EXISTS samples_time ON metrics_web_xui_user_samples(sampled_at);
        ''')
        # The old entry point created an empty physical event table. Preserve any
        # rows before replacing it with the dictionary-backed compatibility view.
        kind = c.execute("SELECT type FROM sqlite_master WHERE name='metrics_web_xui_access_events'").fetchone()
        if kind and kind[0] == 'table':
            rows = c.execute('SELECT seen_at,username,ip,server,domain,port,count FROM metrics_web_xui_access_events').fetchall()
            for t,u,ip,node,d,p,n in rows:
                store(c, {'t':int(datetime.fromisoformat(t.replace('Z','+00:00')).timestamp()),'u':u,'ip':ip or '', 's':node or '', 'd':d,'p':p,'c':n})
            c.execute('DROP TABLE metrics_web_xui_access_events')
        c.execute('DROP VIEW IF EXISTS metrics_web_xui_access_events')
        c.executescript('''
        CREATE VIEW IF NOT EXISTS metrics_web_xui_access_events AS
        SELECT a.id,strftime('%Y-%m-%dT%H:%M:%SZ',a.last_at,'unixepoch') seen_at,
            lower(u.value) username,i.value ip,n.value server,d.value domain,a.port,a.count
        FROM access_buckets a JOIN dimensions u ON u.id=a.user_id
        JOIN dimensions i ON i.id=a.ip_id JOIN dimensions n ON n.id=a.node_id
        JOIN dimensions d ON d.id=a.domain_id;
        CREATE TRIGGER IF NOT EXISTS delete_access_event INSTEAD OF DELETE ON metrics_web_xui_access_events
        BEGIN DELETE FROM access_buckets WHERE id=OLD.id; END;
        ''')
    return [r[0] for r in tables]


class RoutedConnection(sqlite3.Connection):
    """Legacy queries keep their schema, including writes and ON CONFLICT."""
    def execute(self, sql, parameters=()):
        return super().execute(TABLE_RE.sub(lambda m: 'telemetry.metrics_' + m[0], sql), parameters)
    def executemany(self, sql, parameters):
        return super().executemany(TABLE_RE.sub(lambda m: 'telemetry.metrics_' + m[0], sql), parameters)
    def __exit__(self, *args):
        try: return super().__exit__(*args)
        finally: self.close()


def connect(db, telemetry):
    c=sqlite3.connect(db, timeout=15, check_same_thread=False, factory=RoutedConnection)
    c.row_factory=sqlite3.Row
    sqlite3.Connection.execute(c,'PRAGMA busy_timeout=15000')
    sqlite3.Connection.execute(c,'ATTACH DATABASE ? AS telemetry',(telemetry,))
    sqlite3.Connection.execute(c,f'PRAGMA telemetry.max_page_count={MAX_PAGES}')
    return c


def dimension(c, value):
    c.execute('INSERT OR IGNORE INTO dimensions(value) VALUES(?)',(value,))
    return c.execute('SELECT id FROM dimensions WHERE value=?',(value,)).fetchone()[0]


def store(c, r):
    t=int(r['t']); u=str(r['u']).strip().lstrip('@').lower()[:128]; d=str(r['d']).lower().strip('.[]')[:253]
    if not u or not d or t < time.time()-EVENT_DAYS*86400 or t > time.time()+300: return
    # No URL paths, queries, headers or packet payloads enter the database.
    if any(v in d for v in ('/','?','#','@',' ')): return
    ids=[dimension(c,str(v)) for v in (u,str(r.get('ip',''))[:64],str(r.get('s',''))[:80],d)]
    n=max(1,min(int(r.get('c',1)),1_000_000)); p=max(0,min(int(r.get('p',0)),65535))
    c.execute('''INSERT INTO access_buckets(bucket,last_at,user_id,ip_id,node_id,domain_id,port,count)
        VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(bucket,user_id,ip_id,node_id,domain_id,port)
        DO UPDATE SET count=count+excluded.count,last_at=MAX(last_at,excluded.last_at)''',
        (t//300*300,t,*ids,p,n))
    day=iso(t)[:10]
    c.execute('''INSERT INTO domain_daily VALUES(?,?,?,?,?) ON CONFLICT(day,user_id,domain_id)
        DO UPDATE SET count=count+excluded.count,last_at=MAX(last_at,excluded.last_at)''',(day,ids[0],ids[3],n,t))
    c.execute('''INSERT INTO metrics_web_xui_access_daily_counts VALUES(?,?,?) ON CONFLICT(username,day)
        DO UPDATE SET request_count=request_count+excluded.request_count''',(u,day,n))
    if r.get('ip'):
        c.execute('''INSERT INTO metrics_web_xui_ip_history(username,ip,first_seen_at,last_seen_at,seen_count,panels_json)
            VALUES(?,?,?,?,?,?) ON CONFLICT(username,ip) DO UPDATE SET last_seen_at=excluded.last_seen_at,
            seen_count=seen_count+excluded.seen_count,panels_json=excluded.panels_json''',
            (u,str(r['ip'])[:64],iso(t),iso(t),n,json.dumps([r.get('s','REMNA')])) )
    c.execute('DELETE FROM metrics_web_xui_user_insights_cache WHERE username=?',(u,))


def ingest(path, directory, aliases):
    files=sorted(Path(directory).glob('*.json'))[:16]
    accepted=0
    for file in files:
        if file.stat().st_size > 4*1024*1024: continue
        batch=json.loads(file.read_text()); bid=batch['id']
        if not re.fullmatch('[a-f0-9]{64}',bid): continue
        with closing(open_db(path)) as c, c:
            c.execute(f'PRAGMA max_page_count={MAX_PAGES}')
            if not c.execute('SELECT 1 FROM ingest_batches WHERE id=?',(bid,)).fetchone():
                for r in batch['records']:
                    # Node email format is not an account identifier. Resolve only
                    # through a known panel mapping; never trust arbitrary log text.
                    user=aliases.get(r['u'])
                    if user:
                        r['u']=user; store(c,r); accepted+=1
                c.execute('INSERT INTO ingest_batches VALUES(?,?)',(bid,int(time.time())))
        file.unlink()  # Commit first: replay after a crash is safe.
    return accepted


def maintain(path):
    now=int(time.time())
    with closing(open_db(path)) as c, c:
        c.execute('DELETE FROM access_buckets WHERE last_at<?',(now-EVENT_DAYS*86400,))
        c.execute('DELETE FROM domain_daily WHERE day<?',(iso(now-SUMMARY_DAYS*86400)[:10],))
        c.execute('DELETE FROM metrics_web_xui_access_daily_counts WHERE day<?',(iso(now-SUMMARY_DAYS*86400)[:10],))
        c.execute('DELETE FROM metrics_web_xui_user_samples WHERE sampled_at<?',(iso(now-30*86400),))
        c.execute("""DELETE FROM metrics_web_xui_user_samples WHERE id IN (
            SELECT id FROM (SELECT id,ROW_NUMBER() OVER(PARTITION BY username,
            CAST(strftime('%s',sampled_at) AS INTEGER)/900 ORDER BY sampled_at DESC) n
            FROM metrics_web_xui_user_samples WHERE sampled_at<?) WHERE n>1)""",(iso(now-2*86400),))
        c.execute('DELETE FROM metrics_web_xui_ip_history WHERE last_seen_at<?',(iso(now-30*86400),))
        c.execute('DELETE FROM ingest_batches WHERE at<?',(now-7*86400,))
        c.execute('DELETE FROM client_error_counts WHERE day<?',(iso(now-14*86400)[:10],))
        # Keep ample free pages before the hard limit: prune oldest technical
        # buckets only. Users, balances and payment records live in another file.
        while c.execute('PRAGMA page_count').fetchone()[0]-c.execute('PRAGMA freelist_count').fetchone()[0] > MAX_PAGES*.75:
            old=c.total_changes
            c.execute('DELETE FROM access_buckets WHERE id IN (SELECT id FROM access_buckets ORDER BY last_at LIMIT 5000)')
            if c.total_changes == old:
                # Under exceptional volume, reduce oldest technical summaries,
                # then oldest traffic samples before reaching the hard limit.
                c.execute('DELETE FROM domain_daily WHERE (day,user_id,domain_id) IN (SELECT day,user_id,domain_id FROM domain_daily ORDER BY day LIMIT 5000)')
                c.execute('DELETE FROM metrics_web_xui_user_samples WHERE id IN (SELECT id FROM metrics_web_xui_user_samples ORDER BY sampled_at LIMIT 5000)')
                if c.total_changes == old: break
        c.execute('''DELETE FROM dimensions WHERE id NOT IN (SELECT user_id FROM access_buckets
            UNION SELECT ip_id FROM access_buckets UNION SELECT node_id FROM access_buckets
            UNION SELECT domain_id FROM access_buckets UNION SELECT user_id FROM domain_daily
            UNION SELECT domain_id FROM domain_daily)''')
    with closing(open_db(path)) as c:
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.execute('PRAGMA incremental_vacuum(2048)')


def traffic_delta(c,native_id,lifetime):
    """New identities establish a baseline; resets never create negative traffic."""
    c.execute('CREATE TABLE IF NOT EXISTS native_counters(id INTEGER PRIMARY KEY,last_value INTEGER NOT NULL,tracked INTEGER NOT NULL)')
    row=c.execute('SELECT last_value,tracked FROM native_counters WHERE id=?',(native_id,)).fetchone()
    tracked=(row[1]+max(0,lifetime-row[0])) if row else 0
    c.execute('INSERT INTO native_counters VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET last_value=excluded.last_value,tracked=excluded.tracked',(native_id,lifetime,tracked))
    return tracked
