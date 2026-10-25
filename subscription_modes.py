"""Per-account mode selection at the private subscription boundary.

Preferences never authorise access: the native panel still checks expiry, block
and HWID before any conversion. RAW payloads are untouched.
"""
import base64
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit


def policy(root):
    file=Path(root)/'transport-policy.json'
    return json.loads(file.read_text()) if file.exists() else {}


def selected(root, short):
    cfg=policy(root)
    if not cfg.get('enabled') or not short:return 'raw',cfg
    with closing(sqlite3.connect('file:'+cfg['business_db']+'?mode=ro',uri=True,timeout=2)) as db:
        tables={r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        if 'vpn_preferences' not in tables:return 'raw',cfg
        actor=cfg.get('aliases',{}).get(short)
        if actor is None:
            row=db.execute('SELECT u.tg_id FROM remna_links l JOIN users u ON u.sub_id=l.sub_id WHERE l.short_uuid=?',(short,)).fetchone()
            actor=row[0] if row else None
        row=db.execute('SELECT transport FROM vpn_preferences WHERE tg_id=?',(actor,)).fetchone()
    return ('xhttp' if row and row[0]=='xhttp' else 'raw'),cfg


def announcement(root):
    """None preserves upstream defaults; '' removes the announcement headers."""
    cfg=policy(root)
    if not cfg.get('business_db'):return None
    with closing(sqlite3.connect('file:'+cfg['business_db']+'?mode=ro',uri=True,timeout=2)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscription_announcement'").fetchone():return None
        row=db.execute('SELECT text FROM subscription_announcement WHERE singleton=1').fetchone()
    if not row:return None
    text=row[0]
    if not isinstance(text,str) or len(text)>500:raise ValueError('invalid_announcement')
    if not text.strip():return ''
    return 'base64:'+base64.b64encode(text.encode('utf-8')).decode('ascii')


def profile_title(root, short):
    """Only the owner of this exact mapped subscription contributes its name."""
    cfg=policy(root)
    if not short or not cfg.get('business_db'):return None
    with closing(sqlite3.connect('file:'+cfg['business_db']+'?mode=ro',uri=True,timeout=2)) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'users' not in tables:return None
        actor=cfg.get('aliases',{}).get(short)
        if actor is None:
            if 'remna_links' not in tables:return None
            user=db.execute('SELECT u.tg_id,u.tg_name FROM remna_links l JOIN users u ON u.sub_id=l.sub_id WHERE l.short_uuid=?',(short,)).fetchone()
        else:
            user=db.execute('SELECT tg_id,tg_name FROM users WHERE tg_id=?',(actor,)).fetchone()
        if not user:return None
        names=[]
        if 'web_accounts' in tables:
            # Match the effective identity used by ensure_web_user. An obsolete
            # negative web ID of a linked account must not expose the new owner.
            names=[r[0] for r in db.execute('''SELECT name FROM web_accounts
                WHERE COALESCE(linked_tg_id,web_tg_id,-id)=? ORDER BY id''',(user[0],))]
        names.append(user[1])
    def clean(value):
        value=' '.join(''.join(' ' if c.isspace() else c for c in str(value or '')
            if not unicodedata.category(c).startswith('C') or c.isspace() or c=='\u200d').split())[:80]
        if re.fullmatch(r'\S+@\S+\.\S+',value) or re.fullmatch(r'(?:Telegram\s+|user)-?\d+',value,re.I):return ''
        return value
    name=next((value for raw in names if (value:=clean(raw))),'')
    title='Kingline VPN'+(' · '+name if name else '')
    return 'base64:'+base64.b64encode(title.encode('utf-8')).decode('ascii')


def convert(data, cfg):
    path=cfg['path'];sni=cfg['sni']
    if not re.fullmatch(r'/[A-Za-z0-9/_-]{8,160}/?',path):raise ValueError('invalid_xhttp_path')
    encoded=False
    text=data.decode()
    if not text.lstrip().startswith('vless://'):
        text=base64.b64decode(b''.join(data.split()),validate=True).decode();encoded=True
    result=[]
    for line in text.splitlines():
        if not line.strip():continue
        uri=urlsplit(line)
        if uri.scheme!='vless' or not uri.hostname or not uri.username:raise ValueError('unsupported_subscription_format')
        params={k:v[-1] for k,v in parse_qs(uri.query).items()}
        # An admin's custom foreign SNI cannot be served by our own HTTPS certificate.
        if params.get('sni')!=sni:continue
        for key in ('flow','pbk','sid','spx','extra','encryption','mode','serviceName','headerType'):
            params.pop(key,None)
        params.update(encryption='none',security='tls',type='xhttp',path=path,mode='auto',alpn='h2',sni=sni,fp='firefox')
        result.append(urlunsplit((uri.scheme,uri.netloc,uri.path,urlencode(params),uri.fragment)))
    if not result:raise ValueError('xhttp_hosts_unavailable')
    value=('\n'.join(result)+'\n').encode()
    return base64.b64encode(value) if encoded else value
