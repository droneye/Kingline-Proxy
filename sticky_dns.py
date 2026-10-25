"""Private, durable per-subscription DNS leases. Does not proxy VPN traffic."""
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import time

LEASE_SECONDS=86400
SUFFIX='auto.edge.kingline.online'

class StickyDNS:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        key=self.root/'sticky-dns.key'
        try:
            fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        except FileExistsError:pass
        else:
            with os.fdopen(fd,'wb') as f:f.write(secrets.token_bytes(32))
        self.key=key.read_bytes()
        self.db=self.root/'sticky-dns.sqlite3'
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE IF NOT EXISTS leases(identity TEXT PRIMARY KEY,ip TEXT NOT NULL,changed REAL NOT NULL,review REAL NOT NULL,reason TEXT NOT NULL)')
        self.db.chmod(0o600)

    def digest(self,purpose,value):
        return hmac.new(self.key,(purpose+'|'+str(value)).encode(),hashlib.sha256).hexdigest()

    def ranked(self,identity,candidates):
        def score(pair):
            ip,weight=pair
            uniform=(int(self.digest('rank',str(identity)+'|'+ip)[:16],16)+1)/(2**64+1)
            return -math.log(uniform)/float(weight)
        return [ip for ip,w in sorted([(ip,w) for ip,w in candidates if w>0],key=score)]

    def reconcile(self,users,candidates,now=None):
        now=time.time() if now is None else now
        positive={ip:w for ip,w in candidates if w>0}
        result={'at':now,'subscriptions':{},'leases':[],'legacy':None}
        with sqlite3.connect(self.db,timeout=10) as c:
            c.execute('BEGIN IMMEDIATE')
            for user in [{'id':'legacy','shortUuid':None}]+list(users):
                identity=str(user['id']);ranked=self.ranked(identity,candidates)
                old=c.execute('SELECT ip,changed,review,reason FROM leases WHERE identity=?',(identity,)).fetchone()
                if not ranked:
                    # Keep a known lease through shared observation outages;
                    # its DNS record will fail closed on a genuinely dead port.
                    if not old:continue
                    ip,changed,review,reason=old
                elif old and old[0] in positive and now<old[2]:
                    ip,changed,review,reason=old
                else:
                    ip=ranked[0]
                    changed=old[1] if old and ip==old[0] else now
                    reason='initial' if not old else 'unavailable_or_quota' if old[0] not in positive else 'weights_review'
                    review=now+LEASE_SECONDS
                    c.execute('INSERT INTO leases VALUES(?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET ip=excluded.ip,changed=excluded.changed,review=excluded.review,reason=excluded.reason',(identity,ip,changed,review,reason))
                name=SUFFIX if identity=='legacy' else 'u-'+self.digest('dns',identity)[:24]+'.'+SUFFIX
                item={'name':name,'ip':ip,'eligible':ip in positive,'changed_at':changed,'review_at':review,'reason':reason,'backups':[v for v in ranked if v!=ip]}
                if identity=='legacy':result['legacy']=item
                else:
                    result['leases'].append(item)
                    result['subscriptions'][user['shortUuid']]=name
            active={str(u['id']) for u in users}|{'legacy'}
            for identity, in c.execute('SELECT identity FROM leases').fetchall():
                if identity not in active:c.execute('DELETE FROM leases WHERE identity=?',(identity,))
        return result

    def publish_index(self,data):
        path=self.root/'sticky-subscriptions.json';temp=path.with_suffix('.tmp')
        with open(temp,'w') as f:
            os.chmod(temp,0o600);json.dump(data,f,separators=(',',':'));f.flush();os.fsync(f.fileno())
        os.replace(temp,path)

def preferred_record(primary,backups):
    """A stable preferred address with native TCP failover, never random selection."""
    import ipaddress
    if not primary:return 'A "; return {}"'
    addresses=[str(ipaddress.IPv4Address(ip)) for ip in dict.fromkeys([primary]+list(backups))]
    tiers=','.join("{'"+ip+"'}" for ip in addresses+['127.0.0.1'])
    code="; local live=ifportup(443,{"+tiers+"},{selector='all',backupSelector='all',timeout=2,interval=10,minimumFailures=2}); for _,ip in ipairs(live) do if ip=='127.0.0.1' then return {}; end; end; if #live==0 then return {}; end; return live[1]"
    return 'A '+json.dumps(code)
