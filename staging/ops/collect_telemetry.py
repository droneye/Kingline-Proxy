#!/usr/bin/env python3
"""Privileged host bridge. The public website never receives SSH credentials."""
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from resource_metrics import sample_resources

sys.path.insert(0,'/opt/kingline-manager/app')
from node_manager.ssh import SSH
from node_manager.store import Store
from remna_api import RemnaAPI
ROOT=Path('/opt/kingline-staging')
import sys
sys.path.insert(0, str(ROOT/'bot'))
import diagnostics as diag
os.environ['KINGLINE_DIAGNOSTICS_PATH']=str(ROOT/'logs/operations.jsonl')
diag.configure('collector')

STATE=ROOT/'run/telemetry-cursors.json'
SPOOL=ROOT/'logs/ingest'
SPOOL.mkdir(exist_ok=True);os.chown(SPOOL,10001,10001);os.chmod(SPOOL,0o700)

# Xray stdout is handled by the image's s6-log, capped at 10 MiB. Read only
# complete lines, never emit raw connections or credentials to journald.
REMOTE=Path(__file__).with_name('resource_metrics.py').read_text()+r'''
import base64,datetime,gzip,json,os,re,subprocess,sys
q=json.load(sys.stdin)
resources,cpu=sample_resources(q.get('cpu'))
pid=subprocess.check_output(['docker','inspect','remnanode','--format','{{.State.Pid}}'],text=True,timeout=5).strip()
p='/proc/'+pid+'/root/var/log/xray/current'
try:
 f=open(p,'rb')
except FileNotFoundError:
 print(json.dumps({'missing':True,'resources':resources,'cpu':cpu}));sys.exit()
with f:
 st=os.fstat(f.fileno());identity=str(st.st_dev)+':'+str(st.st_ino)
 start=q.get('offset',0) if q.get('identity')==identity and q.get('offset',0)<=st.st_size else 0
 f.seek(start);data=f.read(1048576)
 stop=data.rfind(b'\n')+1;data=data[:stop]
rows={}
rx=re.compile(r'(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})(?:\.\d+)?\s+from\s+(?:tcp:|udp:)?(\[[^\]]+\]|[^:\s]+)(?::\d+)?\s+accepted\s+(?:tcp|udp):(\[[^\]]+\]|[^:\s]+)(?::(\d+))?\s+\[[^\]]*\]\s+email:\s*(.+)$')
for raw in data.decode(errors='replace').splitlines():
 m=rx.search(raw)
 if not m:continue
 t=int(datetime.datetime.strptime(m[1],'%Y/%m/%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp())
 u=m[5].strip().split('#')[0].strip().lower()
 key=(t//60*60,u,m[2].strip('[]'),m[3].strip('[]').lower(),int(m[4] or 0))
 if key not in rows:rows[key]={'t':t,'u':u,'ip':key[2],'d':key[3],'p':key[4],'c':0}
 rows[key]['c']+=1;rows[key]['t']=max(t,rows[key]['t'])
result={'identity':identity,'start':start,'offset':start+len(data),'size':st.st_size,'records':list(rows.values()),'resources':resources,'cpu':cpu}
print(json.dumps({'gzip':base64.b64encode(gzip.compress(json.dumps(result,separators=(',',':')).encode(),compresslevel=3)).decode()}))
'''

def atomic(path,data,readable=False):
    temp=path.with_suffix(path.suffix+'.tmp')
    with open(temp,'w') as f:
        os.chmod(temp,0o600);json.dump(data,f,separators=(',',':'));f.flush();os.fsync(f.fileno())
    if readable:os.chown(temp,10001,10001)
    os.replace(temp,path)

def collect(node,cursor):
    ssh=SSH('/opt/kingline-manager/data',{'ip':node['public_ip'],'ssh_port':node.get('ssh_port',22),'host_key_sha256':node.get('host_key_sha256')})
    try: answer=json.loads(ssh.python(REMOTE,cursor,timeout=25))
    finally:ssh.close()
    if answer.get('missing'):return answer
    batch=json.loads(gzip.decompress(base64.b64decode(answer['gzip'])))
    if batch['offset']>batch['start']:
        bid=hashlib.sha256(f"{node['uuid']}:{batch['identity']}:{batch['start']}:{batch['offset']}".encode()).hexdigest()
        for r in batch['records']:r['s']=node['name']
        atomic(SPOOL/(bid+'.json'),{'id':bid,'records':batch.pop('records')},True)
    return batch

def snapshot():
    a=RemnaAPI(); users=[];start=0
    while True:
        response=a.call(f'users?start={start}&size=100');part=response.get('users',[]);users.extend(part);start+=len(part)
        if not part or start>=response.get('total',start):break
    with closing(sqlite3.connect('file:'+str(ROOT/'data/vpn_bot.db')+'?mode=ro',uri=True,timeout=10)) as c:
        mappings={r[0]:r[1] for r in c.execute('SELECT remna_id,u.username FROM remna_links l JOIN users u ON l.sub_id=u.sub_id')}
        owner=c.execute('SELECT username FROM users WHERE tg_id=100000001').fetchone()
    original=json.loads(Path('/opt/kingline-control/owner-profile.json').read_text())['user']
    if owner:mappings[original['id']]=owner[0]
    devices={}; offset=0
    while True:
        data=a.call(f'hwid/devices?start={offset}&size=1000');part=data.get('devices',[])
        for d in part:devices[d['userId']]=devices.get(d['userId'],0)+1
        offset+=len(part)
        if not part or offset>=data.get('total',offset):break
    nodes={n['uuid']:{'code':n.get('country') or n.get('flag_code') or n['name'].split('-')[0], 'name':n.get('label') or n['name'],'ip':n['public_ip']} for n in Store('/opt/kingline-manager/data').nodes()}
    safe=[]
    for u in users:
        if u['id'] in mappings:
            safe.append({'id':u['id'],'nativeUsername':u['username'],'username':mappings[u['id']],
                'status':u['status'],'traffic':u.get('userTraffic') or {},'deviceCount':devices.get(u['id'],0),'deviceLimit':u.get('hwidDeviceLimit')})
    atomic(ROOT/'logs/native-snapshot.json',{'at':int(time.time()),'users':safe,'nodes':nodes},True)

def main():
    cursors=json.loads(STATE.read_text()) if STATE.exists() else {}
    nodes=[n for n in Store('/opt/kingline-manager/data').nodes() if n.get('provisioned',True)]
    status={'at':int(time.time()),'nodes':{},'snapshot':False}
    resources={'at':status['at'],'nodes':{}}
    resources['core'],cursors['__core__']=sample_resources(cursors.get('__core__'))
    try:snapshot();status['snapshot']=True
    except Exception as e:
        status['snapshotError']=type(e).__name__;diag.event('collector_snapshot_failed','ERROR',error=type(e).__name__)
    backlog=sum(p.stat().st_size for p in SPOOL.glob('*.json'))
    if backlog>64*1024*1024:
        status['backpressure']=True;diag.event('collector_backpressure','WARNING')
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            jobs={pool.submit(collect,n,cursors.get(n['uuid'],{})):n for n in nodes}
            for job in as_completed(jobs):
                node=jobs[job]
                try:
                    value=job.result();status['nodes'][node['uuid']]={'ok':not value.get('missing'),'lagBytes':value.get('size',0)-value.get('offset',0)}
                    resources['nodes'][node['uuid']]=value['resources']
                    cursors[node['uuid']]={k:value[k] for k in ('identity','offset','cpu') if k in value}
                except Exception as e:
                    status['nodes'][node['uuid']]={'ok':False,'error':type(e).__name__};diag.event('collector_node_failed','ERROR',node_uuid=node['uuid'],error=type(e).__name__)
    # A failed node remains explicitly unavailable, never carries old percentages as fresh.
    atomic(ROOT/'logs/resources.json',resources,True)
    atomic(STATE,cursors);atomic(ROOT/'logs/collector-status.json',status,True)
    print(json.dumps({'nodes':len(nodes),'healthy':sum(v['ok'] for v in status['nodes'].values()),'snapshot':status['snapshot'],'backlogBytes':backlog}))

if __name__=='__main__':main()
