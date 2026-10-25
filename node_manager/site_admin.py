"""Owner UI bridge. Unix socket only; manager/SSH secrets never enter the site container."""
import contextlib
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, HttpUrl, ValidationError, field_validator
from .models import AddNode, Capacity, Credentials, RemoveNode, StrictModel, VPNRollback, NodeConnection, NodeRoutes, NodeOrder, RUReceiver, NodeVisibility, ReviewAction, ReviewCheck, InstallationAction
from . import diagnostics as diag

UUID = re.compile(r'^[a-f0-9-]{36}$')

class Metadata(StrictModel):
    description: str = Field(default='', max_length=2000)
    provider_url: str = Field(default='', max_length=2048)
    monthly_cost: float | None = Field(default=None, ge=0, le=10000000)
    currency: str = Field(default='EUR', pattern=r'^[A-Z]{3}$')
    payment_reminders_enabled: bool = True

    @field_validator('provider_url')
    @classmethod
    def valid_provider_url(cls, value):
        value=value.strip()
        if not value:return ''
        if any(c.isspace() or ord(c)<32 or ord(c)==127 for c in value) or '\\' in value:
            raise ValueError('invalid_provider_url')
        if '://' not in value:
            if ':' in value or value.startswith('/'):
                raise ValueError('invalid_provider_url')
            value='https://'+value
        url=HttpUrl(value)
        if url.username or url.password:
            raise ValueError('invalid_provider_url')
        return str(url)

class Enrollment(StrictModel):
    node: AddNode
    metadata: Metadata = Field(default_factory=Metadata)
    request_id: str = Field(pattern=r'^[a-f0-9-]{36}$')

class UpdateNode(StrictModel):
    uuid: str = Field(pattern=r'^[a-f0-9-]{36}$')
    capacity: Capacity | None = None
    metadata: Metadata
    credentials: Credentials | None = None

class DeleteNode(StrictModel):
    uuid: str = Field(pattern=r'^[a-f0-9-]{36}$')
    name: str
    ip: str

class Vault:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root/'site-admin.sqlite3'
        self.cipher = Fernet((self.root/'credentials.key').read_bytes())
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;PRAGMA secure_delete=ON;
                CREATE TABLE IF NOT EXISTS inventory(ip TEXT PRIMARY KEY, metadata TEXT NOT NULL, secret BLOB, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, node_uuid TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL, error TEXT, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS active_removal ON operations(node_uuid) WHERE state IN ('queued','running');''')
        os.chmod(self.path, 0o600)

    @contextlib.contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c: yield c
        finally: c.close()

    def save(self, ip, metadata, credentials=None):
        secret = self.cipher.encrypt(json.dumps(credentials.reveal()).encode()) if credentials and credentials.reveal() else None
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            value=metadata.model_dump()
            old=db.execute('SELECT metadata FROM inventory WHERE ip=?',(ip,)).fetchone()
            previous=json.loads(old[0]) if old else {}
            for field in ('payment_reminders_enabled','provider_url'):
                if field not in metadata.model_fields_set and field in previous:
                    value[field]=previous[field]
            encoded=json.dumps(value,ensure_ascii=False)
            db.execute('INSERT INTO inventory VALUES(?,?,?,?) ON CONFLICT(ip) DO UPDATE SET metadata=excluded.metadata,secret=COALESCE(excluded.secret,inventory.secret),updated=excluded.updated', (ip, encoded, secret, time.time()))

    def metadata(self, ip):
        with self.db() as db: row=db.execute('SELECT metadata,secret FROM inventory WHERE ip=?',(ip,)).fetchone()
        return {**Metadata().model_dump(), **(json.loads(row['metadata']) if row else {}), 'stored_credentials':bool(row and row['secret'])}

    def reveal(self, ip):
        with self.db() as db: row=db.execute('SELECT secret FROM inventory WHERE ip=?',(ip,)).fetchone()
        return json.loads(self.cipher.decrypt(row[0])) if row and row[0] else {}

    def queue_removal(self, body):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute("SELECT * FROM operations WHERE node_uuid=? AND state IN ('queued','running')", (body.uuid,)).fetchone()
            if previous: return self.public_operation(previous)
            op=str(uuid.uuid4()); now=time.time()
            db.execute("INSERT INTO operations VALUES(?,?,?,'queued',NULL,?,?)",(op,body.uuid,body.model_dump_json(),now,now))
            return self.public_operation(db.execute('SELECT * FROM operations WHERE id=?',(op,)).fetchone())

    @staticmethod
    def public_operation(row):
        return {**{k:row[k] for k in ('id','node_uuid','state','error','created','updated')}, **{k:json.loads(row['body']).get(k) for k in ('name','ip')}}

    def operations(self):
        with self.db() as db: return [self.public_operation(r) for r in db.execute('SELECT * FROM operations ORDER BY created DESC LIMIT 30')]


class ManagerClient:
    def __init__(self, root): self.root=Path(root)
    def call(self, method, path, payload=None, idem=None):
        headers={'Authorization':'Bearer '+(self.root/'admin.token').read_text().strip(), 'X-Request-ID':diag.REQUEST_ID.get() or ''}
        if idem: headers['Idempotency-Key']=idem
        if payload is not None: headers['Content-Type']='application/json'
        req=urllib.request.Request('http://127.0.0.1:8090'+path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=240 if method == 'DELETE' else 60) as response: return json.load(response)
        except urllib.error.HTTPError as exc:
            try: code=json.load(exc).get('detail','manager_failed')
            except (ValueError, OSError): code='manager_failed'
            raise HTTPException(exc.code, code if isinstance(code,str) and re.fullmatch(r'[a-z0-9_:-]{1,120}',code) else 'invalid_request') from None
        except (OSError, ValueError): raise HTTPException(503,'manager_unavailable') from None


class LogReader:
    def __init__(self, root, site_root):
        self.root=Path(root);self.site=Path(site_root);self.cached=None;self.at=0;self.lock=threading.Lock()
        self.control=Path(os.environ.get('KINGLINE_CONTROL_ROOT','/opt/kingline-control'))

    def snapshot(self):
        with self.lock:
            if self.cached and time.monotonic()-self.at < 5: return self.cached
            records=[]
            # Bounded scan: at most 6 MiB per source, never the legacy traffic archive.
            for base in (self.site/'logs/diagnostics.jsonl',self.root/'diagnostics/node-manager.jsonl',self.root/'diagnostics/site-admin.jsonl',self.root/'diagnostics/ru-egress.jsonl',self.site/'logs/operations.jsonl'):
                budget=6*1024*1024
                for path in [base]+[base.with_name(base.name+'.'+str(i)) for i in range(1,4)]:
                    try:
                        with path.open('rb') as f:
                            size=f.seek(0,2);take=min(size,budget);f.seek(size-take);data=f.read(take)
                        budget-=take
                        lines=data.splitlines()
                        if size > take: lines=lines[1:]
                        for line in lines:
                            try:
                                row=json.loads(line)
                                if not isinstance(row,dict) or 'ts' not in row: continue
                                # Whitelist technical fields; exclude arbitrary payloads, user IDs and paths.
                                allowed=('ts','service','level','event','request_id','operation','target','status','error','message','stack','duration_ms','count','errors','slow','avg_ms','max_ms','p95_ms_upper','job_id','phase','node_uuid','op_id','attempt','file','line','logger','dropped_events','write_errors','kind','route','asset')
                                safe={k:clean_value(row[k]) for k in allowed if k in row}
                                records.append(safe)
                                if len(records)>=10000:
                                    records.sort(key=lambda r:r.get('ts',''),reverse=True);records=records[:5000]
                            except (ValueError,UnicodeError): continue
                    except OSError: pass
                    if budget<=0: break
            records.sort(key=lambda r:r.get('ts',''),reverse=True)
            records=records[:5000]
            cutoff=time.time()-86400
            recent=[];buckets=Counter();groups=Counter()
            for row in records:
                try: epoch=datetime.fromisoformat(str(row['ts']).replace('Z','+00:00')).timestamp()
                except ValueError:continue
                if epoch<cutoff:continue
                recent.append(row)
                if row.get('level') in ('ERROR','CRITICAL','WARNING'):
                    buckets[int(epoch)//3600*3600]+=1
                    groups[(str(row.get('service','')),str(row.get('event','')),str(row.get('error') or ''))]+=1
            now=int(time.time())//3600*3600
            timeline=[{'at':i,'count':buckets[i]} for i in range(now-23*3600,now+1,3600)]
            counts=Counter(r.get('level','INFO') for r in recent)
            self.cached={'ok':True,'at':datetime.now(timezone.utc).isoformat(),'events':records, 'counts':dict(counts),'timeline':timeline,'issues':[{'service':s,'event':e,'error':er,'count':n} for (s,e,er),n in groups.most_common(8)],'health':self.health(),'retention':{'max_mib':33,'recent_event_limit':5000,'display_limit':600}}
            self.at=time.monotonic();return self.cached

    def health(self):
        result=[]
        try:
            with urllib.request.urlopen('http://127.0.0.1:8090/healthz',timeout=3) as response: health=json.load(response)
            result.append({'name':'Установка нод','ok':bool(health.get('worker_alive')),'detail':'Очередь работает' if health.get('worker_alive') else 'Очередь остановлена'})
            result.append({'name':'Очередь РФ-выхода','ok':bool(health.get('ru_worker_alive',True)),'detail':'Работает' if health.get('ru_worker_alive',True) else 'Остановлена'})
            result.append({'name':'Проверки из РФ','ok':bool(health.get('review_worker_alive',True)),'detail':'Обслуживание работает' if health.get('review_worker_alive',True) else 'Остановлено'})
        except (OSError,ValueError): result.append({'name':'Установка нод','ok':False,'detail':'Служба недоступна'})
        try:
            raw=subprocess.check_output(['docker','inspect','--format','{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}} {{.RestartCount}}','kingline-staging-api-1','kingline-staging-bot-1','remnawave','remnawave-db','remnawave-redis'],timeout=5,text=True,stderr=subprocess.DEVNULL)
            for line in raw.splitlines():
                parts=line.split();result.append({'name':parts[0].lstrip('/'),'ok':parts[1]=='running' and 'unhealthy' not in parts,'detail':' · '.join(parts[1:])})
        except (OSError,subprocess.SubprocessError): result.append({'name':'Контейнеры','ok':False,'detail':'Проверка недоступна'})
        for name,path,max_age in [('Сбор статистики',self.site/'logs/collector-status.json',300),('Бот: heartbeat',self.site/'run/bot-heartbeat',180)]:
            try:
                age=round(time.time()-path.stat().st_mtime);ok=age<max_age;detail=f'{age} с назад'
                if name=='Сбор статистики':
                    status=json.loads(path.read_text());failed=sum(not value.get('ok') for value in status.get('nodes',{}).values())
                    ok=ok and bool(status.get('snapshot')) and not failed and not status.get('backpressure')
                    if failed:detail+=f' · проблемных нод: {failed}'
                    if not status.get('snapshot'):detail+=' · снимок недоступен'
                    if status.get('backpressure'):detail+=' · очередь переполнена'
                result.append({'name':name,'ok':bool(ok),'detail':detail})
            except (OSError,ValueError,TypeError,AttributeError): result.append({'name':name,'ok':False,'detail':'Нет корректных данных'})
        usage=os.statvfs(self.root); free=usage.f_bavail*usage.f_frsize
        result.append({'name':'Свободное место','ok':free>2*1024**3,'detail':f'{free/1024**3:.1f} ГБ'})
        for label,file in [('Российский выход','ru-resources.json'),('Закрепление IP','smart-dns-status.json')]:
            path=self.control/file
            if not path.exists():continue
            try:
                value=json.loads(path.read_text());age=time.time()-path.stat().st_mtime
                ok=age<180 and (bool(value.get('online')) if file=='ru-resources.json' else bool(value.get('sticky',{}).get('enabled')) and any(r.get('ok') for r in value.get('replicas',[])))
                result.append({'name':label,'ok':ok,'detail':'Работает' if ok else 'Нет свежей успешной проверки'})
            except (OSError,ValueError,TypeError):result.append({'name':label,'ok':False,'detail':'Нет корректных данных'})
        return result


def clean_value(value):
    if isinstance(value,str): return diag.sanitize(value)
    if isinstance(value,list): return [clean_value(v) for v in value[:16]]
    if isinstance(value,dict): return {str(k)[:50]:clean_value(v) for k,v in list(value.items())[:20] if not re.search(r'password|private_key|passphrase|token|cookie|secret|authorization',str(k),re.I)}
    return value if isinstance(value,(int,float,bool,type(None))) else None


def create_app(root=None, client=None, run_worker=True):
    root=Path(root or os.environ.get('KINGLINE_MANAGER_ROOT','/opt/kingline-manager/data'))
    control=Path(os.environ.get('KINGLINE_CONTROL_ROOT','/opt/kingline-control'))
    client=client or ManagerClient(root);vault=Vault(root)
    logs=LogReader(root,os.environ.get('KINGLINE_SITE_ROOT','/opt/kingline-staging'))
    stop=threading.Event()
    def worker():
        with vault.db() as db: db.execute("UPDATE operations SET state='queued' WHERE state='running'")
        while not stop.is_set():
            with vault.db() as db:
                row=db.execute("SELECT * FROM operations WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
                if row:db.execute("UPDATE operations SET state='running',updated=? WHERE id=?",(time.time(),row['id']))
            if not row:stop.wait(1);continue
            try:
                body=json.loads(row['body'])
                diag.event('node_removal_started',node_uuid=body['uuid'],op_id=row['id'])
                result=client.call('DELETE','/v1/nodes/'+body['uuid'],{'name':body['name'],'ip':body['ip']})
                if not (result.get('state')=='removed' or result.get('removed')): raise RuntimeError('removal_not_confirmed')
                state='done'
                with vault.db() as db:
                    db.execute('UPDATE operations SET state=?,updated=? WHERE id=?',(state,time.time(),row['id']))
                    db.execute('UPDATE inventory SET secret=NULL WHERE ip=?',(body['ip'],))
                diag.event('node_removal_finished',node_uuid=body['uuid'],op_id=row['id'])
            except Exception as exc:
                code=exc.detail if isinstance(exc,HTTPException) and isinstance(exc.detail,str) else 'removal_failed'
                with vault.db() as db:db.execute("UPDATE operations SET state='failed',error=?,updated=? WHERE id=?",(code,time.time(),row['id']))
                diag.event('node_removal_failed','ERROR',op_id=row['id'],error=code)
    def supervise_worker():
        while not stop.is_set():
            try:worker()
            except Exception as exc:
                diag.event('removal_queue_recovering','ERROR',error=type(exc).__name__)
                stop.wait(5)
    thread=threading.Thread(target=supervise_worker,daemon=True,name='site-removals')
    @contextlib.asynccontextmanager
    async def lifespan(app):
        if run_worker:thread.start()
        yield
        stop.set()
        if run_worker:thread.join(timeout=5)
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.state.vault=vault;app.state.worker=thread
    @app.middleware('http')
    async def guard(request:Request, call_next):
        incoming=request.headers.get('x-request-id','');rid=incoming if re.fullmatch(r'[a-f0-9]{32}',incoming) else uuid.uuid4().hex;token=diag.REQUEST_ID.set(rid);started=time.monotonic()
        try:
            if request.method=='POST':
                size=request.headers.get('content-length','')
                if not size.isdigit() or int(size)>65536:return JSONResponse({'ok':False,'error':'body_too_large'},413)
            response=await call_next(request)
            response.headers['Cache-Control']='no-store';response.headers['X-Request-ID']=rid
            return response
        finally:
            diag.observe('admin_bridge', (time.monotonic()-started)*1000,target=diag.safe_route(request.url.path),status=locals().get('response').status_code if 'response' in locals() else 500)
            diag.REQUEST_ID.reset(token)
    @app.exception_handler(RequestValidationError)
    async def invalid(request,error): return JSONResponse({'ok':False,'error':'invalid_request','fields':[{'field':list(e['loc']),'type':e['type']} for e in error.errors()]},422)
    @app.exception_handler(HTTPException)
    async def known(request,error):return JSONResponse({'ok':False,'error':error.detail},error.status_code)
    @app.exception_handler(Exception)
    async def unknown(request,error):
        # Exception values may contain request credentials: keep class and safe stack frames only.
        fields=diag.exception_fields(error);fields.pop('message',None)
        diag.event('admin_bridge_failed','ERROR',**fields)
        return JSONResponse({'ok':False,'error':'operation_failed'},500)

    def inventory():
        raw=client.call('GET','/v1/nodes')
        allowed=('uuid','name','label','display_name','flag_code','public_ip','quota_bytes','reset_day','speed_mbps','maintenance','smart_enabled','provisioned','country','ssh_port','managed_ssh','host_key_sha256','removable','removal_block_reason','via_uuid','ru_enabled','hidden','effective_hidden','hidden_by','network_auto_eligible','relay_accounted')
        route_fields=('name','ip','weight','quota_bytes','used_bytes','remaining_before_reserve_bytes','speed_mbps','reset_day','maintenance','smart_enabled','vpn_probe_state','vpn_probe_excluded','network_auto_eligible','relay_accounting_ready')
        data={'nodes':[{k:n[k] for k in allowed if k in n} for n in raw.get('nodes',[])], 'routing':{'updated_at':raw.get('routing',{}).get('updated_at'),'nodes':[{k:n[k] for k in route_fields if k in n} for n in raw.get('routing',{}).get('nodes',[])]}}
        sticky=raw.get('routing',{}).get('sticky',{})
        data['routing']['sticky']={k:sticky.get(k) for k in ('enabled','minimum_hold_seconds')}
        data['ru_gateway']={k:raw.get('ru_gateway',{}).get(k) for k in ('configured','name','ip','generation','online')}
        routes={n['ip']:n for n in data.get('routing',{}).get('nodes',[])}
        for node in data.get('nodes',[]):
            route=routes.get(node['public_ip'],{})
            node['smart_enabled']=node.get('smart_enabled',True)
            node['speed_mbps']=node.get('speed_mbps') or route.get('speed_mbps') or 1000
            node['admin']=vault.metadata(node['public_ip'])
        try:
            resources=json.loads((logs.site/'logs/resources.json').read_text())
        except (OSError,ValueError):resources={}
        def measurement(value):
            if not isinstance(value,dict) or time.time()-value.get('at',0)>120:return None
            return {k:value.get(k) for k in ('at','cpu_percent','ram_percent','ram_used','ram_total','disk_percent','disk_used','disk_total')}
        for node in data['nodes']:node['resources']=measurement(resources.get('nodes',{}).get(node['uuid']))
        data['core']={'name':'Главный сервис','ip':'198.51.100.10','resources':measurement(resources.get('core'))}
        try:ru=json.loads((control/'ru-resources.json').read_text())
        except (OSError,ValueError):ru={}
        data['service_nodes']=[]
        if ru:
            fresh=time.time()-ru.get('at',0)<=120
            data['service_nodes'].append({'name':'Российский выход','ip':ru.get('public_ip','Raspberry Pi'),'online':bool(fresh and ru.get('online')),'resources':measurement(ru.get('resources'))})
        return data
    def find_node(node_uuid):
        if not UUID.fullmatch(node_uuid):raise HTTPException(422,'invalid_node')
        node=next((n for n in inventory().get('nodes',[]) if n['uuid']==node_uuid),None)
        if not node:raise HTTPException(404,'node_not_found')
        return node
    @app.get('/nodes')
    def nodes():
        data=inventory();data['jobs']=client.call('GET','/v1/jobs').get('jobs',[]);data['operations']=vault.operations();data['ok']=True
        return data
    @app.get('/job')
    def job_status(id: str):
        if not UUID.fullmatch(id):raise HTTPException(422,'invalid_job')
        row=client.call('GET','/v1/jobs/'+id)
        # Detailed installation history contains phases only, never SSH or tunnel credentials.
        public={k:row.get(k) for k in ('id','ip','state','phase','error','created','updated')}
        public['events']=[{k:e.get(k) for k in ('at','phase')} for e in row.get('events',[])[-200:]]
        # A closed schema from the manager, with no private candidate payload or keys.
        public['review']=row.get('review')
        return {'ok':True,'job':public}
    @app.post('/installation-action')
    def installation_action(body:InstallationAction):
        return client.call('POST','/v1/installation-action',body.model_dump(mode='json'))
    @app.get('/node-reviews')
    def node_reviews():return client.call('GET','/v1/node-reviews')
    @app.post('/review-action')
    def review_action(body:ReviewAction):return client.call('POST','/v1/node-reviews/action',body.model_dump(mode='json'))
    @app.post('/review-check')
    def review_check(body:ReviewCheck):return client.call('POST','/v1/node-reviews/check',body.model_dump(mode='json'))
    @app.post('/node-visibility')
    def node_visibility(body:NodeVisibility):return client.call('POST','/v1/node-reviews/visibility',body.model_dump(mode='json'))
    @app.get('/ru-egress')
    def ru_egress():return client.call('GET','/v1/ru-egress')
    @app.get('/ru-job')
    def ru_job(id:str):
        if not UUID.fullmatch(id):raise HTTPException(422,'invalid_job')
        return client.call('GET','/v1/ru-egress/jobs/'+id)
    @app.post('/ru-replace',status_code=202)
    def ru_replace(body:RUReceiver):
        payload=body.model_dump(mode='json',exclude={'credentials'})|{'credentials':body.credentials.reveal()}
        return client.call('POST','/v1/ru-egress',payload)
    @app.post('/ru-retry',status_code=202)
    def ru_retry(body:dict):
        identifier=str(body.get('job_id',''))
        if not UUID.fullmatch(identifier):raise HTTPException(422,'invalid_job')
        credentials=Credentials.model_validate(body.get('credentials') or {})
        return client.call('POST','/v1/ru-egress/jobs/'+identifier+'/retry',credentials.reveal())
    @app.post('/preflight')
    def preflight(body:Enrollment):
        check=client.call('POST','/v1/nodes/preflight',body.node.model_dump(mode='json',exclude={'credentials'})|{'credentials':body.node.credentials.reveal()})
        diag.event('node_preflight_finished', 'INFO' if check.get('ok') else 'WARNING', error=check.get('error'))
        return {'ok':True,'check':check}
    @app.post('/add',status_code=202)
    def add(body:Enrollment):
        # Write encrypted credentials before submission: retries never lose a provided password/key.
        vault.save(body.node.ip,body.metadata,body.node.credentials)
        result=client.call('POST','/v1/nodes',body.node.model_dump(mode='json',exclude={'credentials'})|{'credentials':body.node.credentials.reveal()},body.request_id)
        diag.event('node_install_submitted',job_id=result.get('id'))
        return {'ok':True,'job':result}
    @app.post('/update')
    def update(body:UpdateNode):
        node=find_node(body.uuid)
        if body.capacity:
            current={'quota_tb':float(node.get('quota_bytes') or 0)/1e12,'speed_mbps':float(node.get('speed_mbps') or 1000),'reset_day':node.get('reset_day'),'maintenance':bool(node.get('maintenance')),'smart_enabled':node.get('smart_enabled',True),'ru_enabled':node.get('ru_enabled',True)}
            changed={k:v for k,v in body.capacity.model_dump(exclude_none=True).items() if v!=current[k]}
            if changed:client.call('PATCH','/v1/nodes/'+body.uuid,changed)
        vault.save(node['public_ip'],body.metadata,body.credentials)
        diag.event('node_settings_updated',node_uuid=body.uuid)
        return {'ok':True}
    @app.post('/remove',status_code=202)
    def remove(body:DeleteNode):
        node=find_node(body.uuid)
        if node['name']!=body.name or node['public_ip']!=body.ip:raise HTTPException(409,'node_identity_changed')
        if not node.get('removable'):raise HTTPException(409,node.get('removal_block_reason') or 'removal_blocked')
        return {'ok':True,'operation':vault.queue_removal(body)}
    @app.post('/credentials')
    def reveal(body:dict):
        node=find_node(str(body.get('uuid','')))
        diag.event('node_credentials_revealed',node_uuid=node['uuid'])
        return {'ok':True,'credentials':vault.reveal(node['public_ip']), 'managed_ssh':bool(node.get('managed_ssh'))}
    @app.post('/retry')
    def retry(body:dict):
        job=str(body.get('job_id',''))
        if not UUID.fullmatch(job):raise HTTPException(422,'invalid_job')
        row=client.call('GET','/v1/jobs/'+job)
        supplied=body.get('credentials') if 'credentials' in body else vault.reveal(row['ip'])
        creds=Credentials.model_validate(supplied or {})
        if not creds.reveal() and 'credentials' not in body:raise HTTPException(409,'credentials_required')
        vault.save(row['ip'],Metadata.model_validate({k:v for k,v in vault.metadata(row['ip']).items() if k!='stored_credentials'}),creds)
        return {'ok':True,'job':client.call('POST','/v1/jobs/'+job+'/retry',creds.reveal())}
    @app.get('/logs')
    def log_data(level:str='', service:str='', search:str='', export:bool=False):
        data=logs.snapshot();events=data['events']
        if level:events=[r for r in events if r.get('level')==level]
        if service:events=[r for r in events if r.get('service')==service]
        if search:events=[r for r in events if search[:100].lower() in json.dumps(r,ensure_ascii=False).lower()]
        return {**data,'health':data['health']+[{'name':'Очередь удаления','ok':not run_worker or thread.is_alive(),'detail':'Работает' if thread.is_alive() else 'Остановлена'}],'events':events[:600 if export else 200], 'services':sorted({r.get('service','unknown') for r in data['events']})}
    @app.get('/health')
    def health():return {'ok':not run_worker or thread.is_alive()}
    @app.get('/node-network')
    def node_network(): return client.call('GET','/v1/node-network')
    @app.get('/node-order')
    def node_order():
        data=client.call('GET','/v1/node-network')
        return {k:data[k] for k in ('ok','revision','keys')}
    @app.get('/node-job')
    def node_job(id:str):
        if not UUID.fullmatch(id):raise HTTPException(422,'invalid_change')
        data=client.call('GET','/v1/vpn/change?id='+id)
        if data['change'].get('kind')!='node_network':raise HTTPException(404,'not_found')
        return data
    @app.post('/node-connection',status_code=202)
    def node_connection(body:NodeConnection):return client.call('POST','/v1/node-network/connection',body.model_dump(mode='json'))
    @app.post('/node-routes',status_code=202)
    def node_routes(body:NodeRoutes):return client.call('POST','/v1/node-network/routes',body.model_dump(mode='json'))
    @app.post('/node-order',status_code=202)
    def node_reorder(body:NodeOrder):return client.call('POST','/v1/node-network/order',body.model_dump(mode='json'))
    @app.post('/node-rollback',status_code=202)
    def node_rollback(body:VPNRollback):return client.call('POST','/v1/node-network/rollback',body.model_dump(mode='json'))
    return app

if __name__=='__main__':
    import uvicorn
    diag.configure('site-admin')
    uvicorn.run(create_app(),uds='/run/kingline-site-admin/bridge.sock',access_log=False,log_level='warning')
