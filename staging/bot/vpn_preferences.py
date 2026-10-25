"""Shared website/bot preference. One subscription URL, no new identities or charge."""
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone

MOSCOW=timezone(timedelta(hours=3))
PLATFORMS={'ios':'iOS / iPadOS','android':'Android','windows':'Windows','macos':'macOS','linux':'Linux','unknown':'Не определено'}

MODES={'raw','xhttp'}


def available():return os.getenv('KINGLINE_XHTTP_READY','0')=='1'


def schema(c):
    # Take a write reservation before inspecting the schema: two starting processes
    # must not both hold read locks and then attempt to upgrade them for ALTER.
    c=getattr(c,'connection',c)
    own_transaction=not c.in_transaction
    if own_transaction:c.execute('BEGIN IMMEDIATE')
    c.execute('SAVEPOINT vpn_schema')
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS vpn_preferences (
            tg_id INTEGER PRIMARY KEY, transport TEXT NOT NULL CHECK(transport IN ('raw','xhttp')),
            updated_at REAL NOT NULL, selection_source TEXT NOT NULL DEFAULT 'default',
            manual_selected_at REAL, auto_due_at REAL, auto_completed_at REAL)""")
        columns={r[1] for r in c.execute('PRAGMA table_info(vpn_preferences)')}
        if 'selection_source' not in columns:
            # Every pre-migration row was written by the user's explicit selector.
            c.execute("ALTER TABLE vpn_preferences ADD COLUMN selection_source TEXT NOT NULL DEFAULT 'manual'")
        if 'manual_selected_at' not in columns:
            c.execute('ALTER TABLE vpn_preferences ADD COLUMN manual_selected_at REAL')
            c.execute("UPDATE vpn_preferences SET manual_selected_at=updated_at WHERE selection_source='manual'")
        if 'auto_due_at' not in columns:c.execute('ALTER TABLE vpn_preferences ADD COLUMN auto_due_at REAL')
        if 'auto_completed_at' not in columns:
            c.execute('ALTER TABLE vpn_preferences ADD COLUMN auto_completed_at REAL')
            c.execute("UPDATE vpn_preferences SET auto_completed_at=updated_at,auto_due_at=NULL WHERE selection_source='auto'")
        c.execute("""CREATE TABLE IF NOT EXISTS account_platforms (
            tg_id INTEGER NOT NULL, platform TEXT NOT NULL CHECK(platform IN ('ios','android','windows','macos','linux','unknown')),
            first_seen_at REAL NOT NULL, last_seen_at REAL NOT NULL,
            seen_on_site INTEGER NOT NULL DEFAULT 0, seen_in_miniapp INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(tg_id,platform))""")
        c.execute('CREATE INDEX IF NOT EXISTS vpn_auto_due ON vpn_preferences(auto_due_at) WHERE auto_due_at IS NOT NULL')
    except Exception:
        c.execute('ROLLBACK TO vpn_schema');c.execute('RELEASE vpn_schema')
        if own_transaction:c.rollback()
        raise
    c.execute('RELEASE vpn_schema')
    if own_transaction:c.commit()


def next_night(now):
    local=datetime.fromtimestamp(now,MOSCOW)
    due=local.replace(hour=3,minute=30,second=0,microsecond=0)
    if due<=local:due+=timedelta(days=1)
    return due.timestamp()


def platform(user_agent='',client_hint='',platform_hint=''):
    """Coarse client-reported OS, never an identity or a security proof."""
    ua=str(user_agent or '')[:1024].lower();hint=str(client_hint or '').strip('" ').lower()
    app=str(platform_hint or '').lower()
    if any(s in ua for s in ('iphone','ipad','ipod')) or ('macintosh' in ua and 'mobile/' in ua):return 'ios'
    if hint=='ios' or app=='ios':return 'ios'
    if 'android' in ua or hint=='android' or app=='android':return 'android'
    if 'windows' in ua or hint=='windows' or app=='windows':return 'windows'
    if 'macintosh' in ua or 'mac os x' in ua or hint=='macos' or app=='macos':return 'macos'
    if 'linux' in ua or hint=='linux' or app=='linux':return 'linux'
    return 'unknown'


def automatic_target(c,actor):
    known={r[0] for r in c.execute('SELECT platform FROM account_platforms WHERE tg_id=? AND (seen_on_site=1 OR seen_in_miniapp=1)',(actor,))}
    # Shared subscription: any observed iPhone/iPad takes priority over Android.
    return 'raw' if 'ios' in known else 'xhttp' if 'android' in known else None


def schedule(c,actor,now):
    row=c.execute('SELECT transport,manual_selected_at,selection_source,auto_completed_at FROM vpn_preferences WHERE tg_id=?',(actor,)).fetchone()
    if not row:return
    target=automatic_target(c,actor)
    if row[1] is not None or row[2]=='manual' or row[3] is not None or target is None:
        c.execute('UPDATE vpn_preferences SET auto_due_at=NULL WHERE tg_id=? AND auto_due_at IS NOT NULL',(actor,))
    else:
        c.execute('UPDATE vpn_preferences SET auto_due_at=COALESCE(auto_due_at,?) WHERE tg_id=?',(next_night(now),actor))


def observe(tg_id,user_agent='',client_hint='',platform_hint='',source='site',now=None):
    actor=int(tg_id);now=time.time() if now is None else now
    os_name=platform(user_agent,client_hint,platform_hint);mini=source=='miniapp'
    with closing(sqlite3.connect(os.environ['DB_PATH'],timeout=5)) as c:
        schema(c);c.execute('BEGIN IMMEDIATE')
        with c:
            user=c.execute('SELECT is_blocked FROM users WHERE tg_id=?',(actor,)).fetchone()
            if not user or user[0]:return
            c.execute("INSERT OR IGNORE INTO vpn_preferences(tg_id,transport,updated_at,selection_source) VALUES(?,'raw',?,'default')",(actor,now))
            c.execute("""INSERT INTO account_platforms VALUES(?,?,?,?,?,?)
                ON CONFLICT(tg_id,platform) DO UPDATE SET last_seen_at=excluded.last_seen_at,
                  seen_on_site=MAX(seen_on_site,excluded.seen_on_site),seen_in_miniapp=MAX(seen_in_miniapp,excluded.seen_in_miniapp)
                WHERE last_seen_at<? OR seen_on_site<excluded.seen_on_site OR seen_in_miniapp<excluded.seen_in_miniapp""",
                (actor,os_name,now,now,int(not mini),int(mini),now-300))
            schedule(c,actor,now)


def apply_due(now=None):
    now=time.time() if now is None else now;local=datetime.fromtimestamp(now,MOSCOW)
    result={'changed':0,'raw':0,'xhttp':0,'completed':0}
    # Recover missed work on the next night, never at daytime after a restart.
    if not available() or not (210<=local.hour*60+local.minute<300):return result
    with closing(sqlite3.connect(os.environ['DB_PATH'],timeout=5)) as c:
        schema(c);c.execute('BEGIN IMMEDIATE')
        with c:
            rows=c.execute("""SELECT p.tg_id,p.transport FROM vpn_preferences p JOIN users u ON u.tg_id=p.tg_id
                WHERE p.auto_due_at<=? AND p.manual_selected_at IS NULL AND p.selection_source!='manual' AND p.auto_completed_at IS NULL
                AND COALESCE(u.is_blocked,0)=0 ORDER BY p.auto_due_at LIMIT 100""",(now,)).fetchall()
            for actor,current in rows:
                target=automatic_target(c,actor)
                if target:
                    c.execute("UPDATE vpn_preferences SET transport=?,updated_at=?,selection_source='auto',auto_due_at=NULL,auto_completed_at=? WHERE tg_id=? AND manual_selected_at IS NULL AND selection_source!='manual' AND auto_completed_at IS NULL",(target,now,now,actor))
                    result['completed']+=1
                    if target!=current:result['changed']+=1;result[target]+=1
                else:c.execute('UPDATE vpn_preferences SET auto_due_at=NULL WHERE tg_id=?',(actor,))
    return result


def account_devices(tg_id):
    with closing(sqlite3.connect(os.environ['DB_PATH'],timeout=5)) as c,c:
        schema(c)
        rows=c.execute('SELECT platform,first_seen_at,last_seen_at,seen_on_site,seen_in_miniapp FROM account_platforms WHERE tg_id=? ORDER BY last_seen_at DESC',(int(tg_id),)).fetchall()
        pref=c.execute('SELECT transport,selection_source,manual_selected_at,auto_due_at,auto_completed_at FROM vpn_preferences WHERE tg_id=?',(int(tg_id),)).fetchone()
    def iso(value):return datetime.fromtimestamp(value,timezone.utc).isoformat() if value is not None else None
    return {'devices':[{'family':r[0],'label':PLATFORMS[r[0]],'firstSeenAt':iso(r[1]),'lastSeenAt':iso(r[2]),
                        'source':'site_and_miniapp' if r[3] and r[4] else 'miniapp' if r[4] else 'site'} for r in rows],
            'transport':pref[0] if pref else 'raw','selectionSource':pref[1] if pref else 'default',
            'manual':bool(pref and (pref[2] is not None or pref[1]=='manual')),'autoDueAt':iso(pref[3]) if pref else None,
            'autoCompletedAt':iso(pref[4]) if pref else None}


def admin_modes():
    with closing(sqlite3.connect(os.environ['DB_PATH'],timeout=5)) as c,c:
        schema(c)
        rows=c.execute('SELECT tg_id,transport,selection_source,manual_selected_at,auto_due_at,auto_completed_at FROM vpn_preferences').fetchall()
    return {r[0]:{'transport':r[1],'transportManual':r[3] is not None or r[2]=='manual','transportAutoCompleted':r[5] is not None} for r in rows}


def forget(c,tg_id):
    schema(c)
    c.execute('DELETE FROM vpn_preferences WHERE tg_id=?',(int(tg_id),))
    c.execute('DELETE FROM account_platforms WHERE tg_id=?',(int(tg_id),))


def read(tg_id):
    with closing(sqlite3.connect(os.environ['DB_PATH'],timeout=5)) as c,c:
        schema(c);row=c.execute('SELECT transport FROM vpn_preferences WHERE tg_id=?',(int(tg_id),)).fetchone()
    return {'transport':row[0] if row else 'raw','xhttpAvailable':available()}


def save(tg_id,transport,*,allow_blocked=False):
    if not isinstance(transport,str) or transport not in MODES:raise ValueError('invalid_transport')
    if transport=='xhttp' and not available():raise ValueError('xhttp_not_ready')
    with closing(sqlite3.connect(os.environ['DB_PATH'],timeout=5)) as c,c:
        schema(c)
        user=c.execute('SELECT is_blocked FROM users WHERE tg_id=?',(int(tg_id),)).fetchone()
        if not user or (user[0] and not allow_blocked):raise ValueError('permission_denied')
        now=time.time()
        c.execute("""INSERT INTO vpn_preferences(tg_id,transport,updated_at,selection_source,manual_selected_at)
            VALUES(?,?,?,'manual',?) ON CONFLICT(tg_id) DO UPDATE SET transport=excluded.transport,
            updated_at=excluded.updated_at,selection_source='manual',manual_selected_at=COALESCE(manual_selected_at,excluded.manual_selected_at),auto_due_at=NULL""",(int(tg_id),transport,now,now))
    return read(tg_id)


def merge(c,source,target):
    """A manual choice on either identity permanently wins over automatic/default choices."""
    if int(source)==int(target):return
    schema(c)
    for row in c.execute('SELECT platform,first_seen_at,last_seen_at,seen_on_site,seen_in_miniapp FROM account_platforms WHERE tg_id=?',(source,)).fetchall():
        c.execute("""INSERT INTO account_platforms VALUES(?,?,?,?,?,?) ON CONFLICT(tg_id,platform) DO UPDATE SET
            first_seen_at=MIN(first_seen_at,excluded.first_seen_at),last_seen_at=MAX(last_seen_at,excluded.last_seen_at),
            seen_on_site=MAX(seen_on_site,excluded.seen_on_site),seen_in_miniapp=MAX(seen_in_miniapp,excluded.seen_in_miniapp)""",(target,*row))
    completed=c.execute('SELECT MIN(auto_completed_at) FROM vpn_preferences WHERE tg_id IN (?,?)',(source,target)).fetchone()[0]
    row=c.execute("""SELECT transport,updated_at,selection_source,manual_selected_at,auto_due_at FROM vpn_preferences
        WHERE tg_id IN (?,?) ORDER BY (manual_selected_at IS NOT NULL OR selection_source='manual') DESC,(auto_completed_at IS NOT NULL) DESC,updated_at DESC LIMIT 1""",(source,target)).fetchone()
    if row:
        c.execute("""INSERT INTO vpn_preferences(tg_id,transport,updated_at,selection_source,manual_selected_at,auto_due_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(tg_id) DO UPDATE SET transport=excluded.transport,updated_at=excluded.updated_at,
            selection_source=excluded.selection_source,manual_selected_at=excluded.manual_selected_at,auto_due_at=excluded.auto_due_at""",(target,*row))
        c.execute('UPDATE vpn_preferences SET auto_completed_at=? WHERE tg_id=?',(completed,target))
        schedule(c,target,time.time())
    c.execute('DELETE FROM vpn_preferences WHERE tg_id=?',(source,))
    c.execute('DELETE FROM account_platforms WHERE tg_id=?',(source,))
