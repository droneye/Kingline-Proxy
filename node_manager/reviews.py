"""Owner decisions, bounded probe history and Telegram outbox. No automatic quarantine."""
import base64,hashlib,html,ipaddress,json,math,re,secrets,subprocess,threading,time,uuid
from pathlib import Path
from . import diagnostics as diag
from .node_network import route_paths
from .ru_detection import classify,TITLES
from .ssh import SafeError
from .store import atomic_json
from .vpn_config import inbound

ACTIVE=('open','kept','hidden','routing')
ERRORS={'tcp_unreachable','reality_rejected','reset','timeout','tls','short_transfer','connect','tunnel_failed','probe_engine_failed','unexpected_exit'}
def clean_observation(row):
    if not isinstance(row,dict) or not isinstance(row.get('attempts'),list) or not 1<=len(row['attempts'])<=3:return None
    attempts=[]
    for source in row['attempts']:
        if not isinstance(source,dict):return None
        if not isinstance(source.get('tcp'),dict):return None
        result={'ok':source.get('ok') is True,'error':source.get('error') if source.get('error') in ERRORS else None,'tcp':{'ok':source['tcp'].get('ok') is True}}
        for name in ('small','large'):
            value=source.get(name)
            if not isinstance(value,dict):result[name]=None;continue
            metrics={'ok':value.get('ok') is True,'error':value.get('error') if value.get('error') in ERRORS else None}
            for key in ('bytes','duration_ms','first_byte_ms','last_progress_ms','curl_code'):
                number=value.get(key);metrics[key]=number if isinstance(number,(int,float)) and not isinstance(number,bool) and math.isfinite(number) and 0<=number<=2**40 else None
            metrics['bytes']=metrics.get('bytes') or 0;result[name]=metrics
        attempts.append(result)
    return {'node':row.get('node'),'revision':row.get('revision'),'ok':row.get('ok') is True,'attempts':attempts}
class Reviews:
    def __init__(self,manager,control_probe=None):
        self.m=manager;self.store=manager.store;self.path=manager.control/'ru-monitor.json';self.thread=None;self.wake=threading.Event();self.sync_lock=threading.Lock()
        self.control_probe=control_probe or self.control_attempt
        with self.store.db() as db:db.executescript('''
          CREATE TABLE IF NOT EXISTS monitor_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS monitor_samples(id TEXT PRIMARY KEY,node TEXT NOT NULL,at REAL NOT NULL,body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS monitor_latest(node TEXT PRIMARY KEY,revision TEXT NOT NULL,at REAL NOT NULL,body TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS node_reviews(id INTEGER PRIMARY KEY AUTOINCREMENT,node TEXT NOT NULL,revision TEXT NOT NULL,ip TEXT NOT NULL,label TEXT NOT NULL,code TEXT NOT NULL,state TEXT NOT NULL,body TEXT NOT NULL,created REAL NOT NULL,updated REAL NOT NULL,next_alert REAL NOT NULL);
          CREATE UNIQUE INDEX IF NOT EXISTS review_active_node ON node_reviews(node,revision) WHERE state IN ('open','kept','hidden','routing');
          CREATE TABLE IF NOT EXISTS review_actions(token TEXT PRIMARY KEY,review INTEGER,operation TEXT NOT NULL,target TEXT,request_id TEXT NOT NULL,result TEXT,created REAL NOT NULL,expires REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS review_outbox(id INTEGER PRIMARY KEY AUTOINCREMENT,dedup TEXT UNIQUE NOT NULL,body TEXT NOT NULL,state TEXT NOT NULL,lease REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,message_id INTEGER);
          CREATE TABLE IF NOT EXISTS review_requests(id TEXT PRIMARY KEY,node TEXT,reason TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS review_routes(id TEXT PRIMARY KEY,review INTEGER NOT NULL,node TEXT NOT NULL,change_id TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL);
        ''')
    def config(self):return json.loads(self.path.read_text()) if self.path.exists() else {'enabled':False}
    def meta(self,key,default=None):
        with self.store.db() as db:r=db.execute('SELECT value FROM monitor_meta WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def save_meta(self,key,value):
        with self.store.db() as db:db.execute('INSERT OR REPLACE INTO monitor_meta VALUES(?,?)',(key,json.dumps(value)))
    def nodes(self):return self.m.network.enrich(self.m.network.nodes())
    def latest(self):
        with self.store.db() as db:return {r['node']:json.loads(r['body']) for r in db.execute('SELECT * FROM monitor_latest')}
    def start(self):
        self.save_meta('analyzing',False)
        self.thread=threading.Thread(target=self.worker,daemon=True,name='ru-monitor-sync');self.thread.start()
    def worker(self):
        while not self.m.stop.is_set():
            try:
                if self.config().get('enabled'):self.tick()
            except Exception as error:
                if isinstance(error,SafeError) and str(error) in {'vpn_operation_busy','ru_installation_busy','vpn_installation_busy'}:
                    self.m.stop.wait(5);continue
                diag.event('ru_monitor_sync_failed','ERROR',error=type(error).__name__)
                self.save_meta('sync_error',{'at':time.time(),'code':str(error) if isinstance(error,SafeError) else type(error).__name__})
                self.source_alert()
            self.wake.wait(60);self.wake.clear()
    def source_alert(self):
        last=self.meta('last_sync_at',self.meta('started_at',time.time()));now=time.time()
        started=self.meta('started_at',now);probe=self.meta('last_probe_at')
        stale=(now-probe>4500) if probe else now-started>600
        if (now-last>600 or stale) and now-self.meta('source_alert_at',0)>21600:
            self.notice('source:'+str(int(now//21600)),'❗ <b>ВАЖНО · Проверка из РФ недоступна</b>\nRaspberry не передаёт результаты. Это не означает, что все VPN-серверы заблокированы. Проверьте домашний интернет и питание Raspberry.')
            self.save_meta('source_alert_at',now)
    def manifest(self):
        with self.m.lock:
            snap=self.m.network.snapshot();nodes=self.nodes();by_id={n['uuid']:n for n in nodes};network=snap['network'];paths=route_paths({n['uuid']:network.get('via',{}).get(n['uuid']) for n in nodes},by_id)
            probe=json.loads((self.m.control/'probe-user.json').read_text());targets=[]
            for node in nodes:
                panel=next(n for n in snap['nodes'] if n['uuid']==node['uuid']);profile=next(p for p in snap['profiles'] if p['uuid']==panel['binding']['activeConfigProfileUuid']);raw=inbound(profile['config'])
                if not raw:continue
                reality=raw['streamSettings']['realitySettings'];host=next((h for h in snap['hosts'] if h['uuid']==node.get('host_uuid')),{});path=paths[node['uuid']]
                from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
                from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
                public=X25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(reality['privateKey']+'='*(-len(reality['privateKey'])%4))).public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)
                target={'id':node['uuid'],'label':node.get('label',node['name']),'ip':node['public_ip'],'address':by_id[path[0]]['public_ip'],'port':network['ports'][node['uuid']] if len(path)>1 else 443,'sni':host.get('sni') or reality['serverNames'][0],'fingerprint':host.get('fingerprint') or snap['owner']['fingerprint'],'public_key':base64.urlsafe_b64encode(public).decode().rstrip('='),'short_id':reality['shortIds'][0],'probe_uuid':probe['vlessUuid']}
                target['revision']=hashlib.sha256(json.dumps({k:v for k,v in target.items() if k!='label'},sort_keys=True).encode()).hexdigest();targets.append(target)
            sample=hashlib.shake_256(b'Kingline connectivity probe v1').digest(1024*1024)
            body={'targets':targets,'entry_ids':[n['uuid'] for n in nodes if not network.get('via',{}).get(n['uuid'])], 'sample_url':'https://test.edge.kingline.online/api/connectivity/probe.bin','sample_bytes':len(sample),'sample_sha256':hashlib.sha256(sample).hexdigest(),'sample_resolve':'test.edge.kingline.online:443:198.51.100.10'}
            body['expected_source_ip']=self.source_ip()
            body['revision']=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest();return body
    def control_attempt(self,target,manifest):
        from .ru_monitor_worker import attempt
        return attempt(target,manifest,binary=self.m.control/'client-26.6.27/xray')
    def source_ip(self):
        fallback=self.config().get('source_public_ip')
        try:
            allowed=subprocess.check_output(['wg','show','wg-ru-control','allowed-ips'],text=True,stderr=subprocess.DEVNULL)
            key=next(line.split()[0] for line in allowed.splitlines() if self.config()['probe']['ip']+'/32' in line.split()[1:])
            endpoints=subprocess.check_output(['wg','show','wg-ru-control','endpoints'],text=True,stderr=subprocess.DEVNULL)
            value=next(line.split()[1].rsplit(':',1)[0] for line in endpoints.splitlines() if line.split()[0]==key)
            return str(ipaddress.IPv4Address(value))
        except (OSError,StopIteration,ValueError,KeyError,subprocess.SubprocessError):return fallback
    def remote(self,payload):
        config=self.config();ssh=self.m.ssh_factory(self.m.root,config['probe'])
        try:result=json.loads(ssh.python(Path(__file__).with_name('ru_monitor_remote.py').read_text(),payload,timeout=90))
        finally:ssh.close()
        if result.get('ok') is False:raise SafeError(result.get('error','probe_remote_failed'))
        return result
    def tick(self):
        if not self.sync_lock.acquire(blocking=False):return
        try:
            if not self.meta('started_at'):self.save_meta('started_at',time.time())
            self.m.visibility.sync()
            manifest=self.manifest();old_manifest=self.meta('manifest_public',{})
            if old_manifest and old_manifest.get('revision')!=manifest['revision']:self.request(None,'inventory',str(uuid.uuid5(uuid.NAMESPACE_URL,'monitor-manifest:'+manifest['revision'])))
            self.save_meta('manifest_public',{'revision':manifest['revision'],'targets':[{k:t[k] for k in ('id','label','ip','address','port','revision')} for t in manifest['targets']],'entry_ids':manifest['entry_ids']})
            target_ids=[t['id'] for t in manifest['targets']]
            if target_ids:
                with self.store.db() as db:
                    db.execute("UPDATE review_requests SET state='cancelled' WHERE state='queued' AND node IS NOT NULL AND node NOT IN ("+','.join('?' for _ in target_ids)+')',target_ids)
                    db.execute('DELETE FROM monitor_latest WHERE node NOT IN ('+','.join('?' for _ in target_ids)+')',target_ids)
                    db.execute("UPDATE node_reviews SET state='superseded',updated=? WHERE node NOT IN ("+','.join('?' for _ in target_ids)+") AND state IN ('open','kept','hidden','routing')",[time.time(),*target_ids])
            self.reconcile_routes()
            with self.store.db() as db:requests=[dict(r) for r in db.execute("SELECT id,node,reason FROM review_requests WHERE state='queued' ORDER BY created LIMIT 50")]
            response=self.remote({'mode':'sync','manifest':manifest,'requests':requests})
            source=Path(__file__).with_name('ru_monitor_worker.py').read_text();sha=hashlib.sha256(source.encode()).hexdigest()
            if not response.get('installed') or response.get('worker_sha')!=sha:
                self.remote({'mode':'install','worker':source,'worker_sha':sha,'container':self.config().get('binary_container','kingline-ru-remnanode')})
                response=self.remote({'mode':'sync','manifest':manifest,'requests':requests})
            self.save_meta('last_sync_at',time.time());self.save_meta('sync_error',None);self.save_meta('running',bool(response.get('running')))
            if response.get('report'):self.ingest(response['report'],manifest)
            self.source_alert()
        finally:self.sync_lock.release()
    def request(self,node=None,reason='manual',identifier=None):
        if node and node not in {n['uuid'] for n in self.nodes()}:raise SafeError('node_not_found')
        identifier=identifier or str(uuid.uuid4())
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            pending=db.execute("SELECT id FROM review_requests WHERE node IS ? AND reason=? AND state='queued'",(node,reason)).fetchone()
            if pending:identifier=pending['id']
            elif db.execute("SELECT count(*) FROM review_requests WHERE state='queued'").fetchone()[0]>=30:raise SafeError('review_queue_busy')
            db.execute('INSERT OR IGNORE INTO review_requests VALUES(?,?,?,\'queued\',?)',(identifier,node,reason,time.time()))
        self.wake.set();return {'ok':True,'message':'Проверка поставлена в очередь. Результат придёт в Telegram.','request_id':identifier}
    def ingest(self,report,manifest):
        if not isinstance(report,dict) or not isinstance(report.get('id'),str) or not re.fullmatch(r'[a-f0-9-]{36}',report['id']) or report.get('id')==self.meta('last_report_id'):return
        now=time.time()
        if not isinstance(report.get('at'),(int,float)) or not 0<=now-report['at']<1800:return
        if report.get('manifest_revision')!=manifest['revision']:return
        targets={t['id']:t for t in manifest['targets']};previous=self.latest()
        core_path=self.m.control/'tunnel-health.json';core=json.loads(core_path.read_text()) if core_path.exists() else {};core_fresh=0<=now-core.get('checked_at',0)<240
        core_rows={r['uuid']:r for r in core.get('nodes',[]) if 'uuid' in r}
        raw=report.get('nodes',[])
        if not isinstance(raw,list) or len(raw)>200:return
        valid=[clean_observation(r) for r in raw if isinstance(r,dict) and r.get('node') in targets and r.get('revision')==targets[r['node']]['revision']]
        valid=[r for r in valid if r]
        if not valid:raise SafeError('probe_report_empty')
        origin_ok=not manifest.get('expected_source_ip') or report.get('control',{}).get('source_ip')==manifest['expected_source_ip']
        source_ok=bool(origin_ok and report.get('control',{}).get('clock_synced') and (report.get('control',{}).get('reference_https') or any(r['ok'] for r in valid)) and not any(a.get('error')=='probe_engine_failed' for r in valid for a in r['attempts']))
        self.save_meta('origin_verified',origin_ok)
        if not source_ok:
            self.notice('source-network:'+str(int(now//21600)),'❗ <b>ВАЖНО · Контрольная сеть Raspberry не проходит проверку</b>\nНельзя надёжно оценить отдельные ноды: возможны неполадки домашнего интернета, часов или широкая фильтрация. Ноды автоматически не скрывались.')
        for row in valid:
            node=row['node'];target=targets[node];core_row=core_rows.get(node,{});core_ok=bool(core_row['ok']) if core_fresh and 'ok' in core_row else None
            matched_control=False
            if source_ok and not row['ok'] and sum(not a['ok'] for a in row['attempts'])>=2:
                self.save_meta('analyzing',True)
                try:core_ok=bool(self.control_probe(target,manifest).get('ok'));matched_control=True
                except Exception:core_ok=None
                finally:self.save_meta('analyzing',False)
            old=previous.get(node);assessment=classify(row,core_ok=core_ok,source_ok=source_ok,other_ru_ok=any(r.get('ok') and r['node']!=node for r in valid),previous=old if old and now-old.get('at',0)<7500 else None)
            # Only bounded numeric observations and fixed codes are persisted.
            result={**row,**assessment,'title':TITLES[assessment['code']],'core_ok':core_ok,'matched_control':matched_control,'at':report['at']}
            with self.store.db() as db:
                db.execute('INSERT OR IGNORE INTO monitor_samples VALUES(?,?,?,?)',(report['id']+':'+node,node,report['at'],json.dumps(result)))
                db.execute('INSERT OR REPLACE INTO monitor_latest VALUES(?,?,?,?)',(node,target['revision'],report['at'],json.dumps(result)))
                existing=db.execute("SELECT * FROM node_reviews WHERE node=? AND revision=? AND state IN ('open','kept','hidden','routing')",(node,target['revision'])).fetchone()
                db.execute("UPDATE node_reviews SET state='superseded',updated=? WHERE node=? AND revision!=? AND state IN ('open','kept')",(now,node,target['revision']))
                if assessment['confirmed']:
                    if existing:db.execute('UPDATE node_reviews SET code=?,body=?,updated=? WHERE id=?',(assessment['code'],json.dumps(result),now,existing['id']));rid=existing['id']
                    else:
                        cursor=db.execute("INSERT INTO node_reviews(node,revision,ip,label,code,state,body,created,updated,next_alert) VALUES(?,?,?,?,?,'open',?,?,?,?)",(node,target['revision'],target['ip'],target['label'],assessment['code'],json.dumps(result),now,now,now));rid=cursor.lastrowid
                else:rid=existing['id'] if existing else None
            if assessment['confirmed']:
                if not existing or (existing['state'] not in ('hidden','routing') and now>=existing['next_alert']):self.alert(rid)
            elif row.get('ok') and rid and assessment['code']=='healthy' and existing['state'] not in ('routing',):
                hidden=next((n.get('hidden',False) for n in self.nodes() if n['uuid']==node),False)
                with self.store.db() as db:db.execute('UPDATE node_reviews SET state=?,updated=? WHERE id=?',('hidden' if hidden else 'recovered',now,rid))
                self.notice('recovery:'+str(rid),'✅ <b>'+html.escape(target['label'])+'</b> снова проходит проверку из РФ.'+ (' Профиль остаётся скрытым до вашего решения.' if hidden else ''),self.buttons(rid,recovered=hidden))
        with self.store.db() as db:
            requested=[dict(r) for r in db.execute("SELECT * FROM review_requests WHERE state='queued' AND id IN ("+','.join('?' for _ in report.get('requests',[]))+')',report.get('requests',[]))] if report.get('requests') else []
            for r in requested:db.execute("UPDATE review_requests SET state='done' WHERE id=?",(r['id'],))
            db.execute('DELETE FROM monitor_samples WHERE at<?',(now-30*86400,));db.execute('DELETE FROM review_actions WHERE expires<?',(now-86400,));db.execute("DELETE FROM review_outbox WHERE state='sent' AND created<?",(now-7*86400,));db.execute("DELETE FROM review_requests WHERE state='done' AND created<?",(now-7*86400,));db.execute("DELETE FROM node_reviews WHERE state IN ('recovered','restored','superseded') AND updated<?",(now-90*86400,))
        for request in requested:
            matched=[r for r in valid if not request['node'] or r['node']==request['node']]
            if matched:
                text='🔎 <b>Повторная проверка из РФ</b>\n'+'\n'.join(('✅ ' if r.get('ok') else '❗ ')+html.escape(targets[r['node']]['label']) for r in matched)
                with self.store.db() as db:related=db.execute('SELECT id FROM node_reviews WHERE node=? ORDER BY updated DESC LIMIT 1',(request['node'],)).fetchone() if request['node'] else None
                hidden=next((n.get('hidden',False) for n in self.nodes() if n['uuid']==request['node']),False)
                if request['reason']!='inventory':self.notice('request:'+request['id'],text,self.buttons(related['id'],recovered=hidden) if related else None)
                if request['reason'].startswith('route:'):
                    review_id=int(request['reason'].split(':')[1]);self.finish_route(review_id,all(r.get('ok') for r in matched))
        if report.get('full') and valid and source_ok and all(r['ok'] for r in valid) and not self.meta('announced'):
            self.notice('monitor-enabled','✅ <b>Мониторинг из РФ включён</b>\nПроверены входные ноды: '+str(len(valid))+'. Каждый час проверяем TCP, REALITY и передачу данных. При подтверждённых сбоях придёт сообщение «ВАЖНО» с кнопками решения. Ноды автоматически не скрываются.',[[{'text':'🔄 Проверить входы','callback_data':self.token(None,'all')}],[{'text':'Открыть ноды','url':'https://test.edge.kingline.online/admin?section=nodes'}]])
            self.save_meta('announced',True)
        self.save_meta('last_report_id',report['id']);self.save_meta('last_probe_at',report['at']);self.save_meta('last_full_at',report.get('last_full_at',0))
    def review(self,identifier):
        with self.store.db() as db:r=db.execute('SELECT * FROM node_reviews WHERE id=?',(identifier,)).fetchone()
        if not r:raise SafeError('review_not_found')
        return dict(r)
    def token(self,review,operation,target=None):
        value=secrets.token_urlsafe(18);now=time.time()
        with self.store.db() as db:db.execute('INSERT INTO review_actions VALUES(?,?,?,?,?,NULL,?,?)',(value,review,operation,target,str(uuid.uuid4()),now,now+7*86400))
        return 'kr:'+value
    def buttons(self,identifier,recovered=False):
        pairs=[('🔄 Проверить','check'),('Вернуть в подписки','restore')] if recovered else [('🔄 Проверить','check'),('Оставить','keep'),('Скрыть из подписок','hide'),('↪️ Через другую ноду','choose')]
        return [[{'text':label,'callback_data':self.token(identifier,action)} for label,action in pairs[i:i+2]] for i in range(0,len(pairs),2)]
    def notice(self,dedup,text,keyboard=None):
        body={'text':text,'keyboard':keyboard or [],'chat_id':self.config().get('owner_id')}
        if not body['chat_id']:return
        with self.store.db() as db:db.execute("INSERT OR IGNORE INTO review_outbox(dedup,body,state,created) VALUES(?,?,'pending',?)",(dedup,json.dumps(body,ensure_ascii=False),time.time()))
    def alert(self,identifier):
        row=self.review(identifier);failure=json.loads(row['body']);node=next((n for n in self.nodes() if n['uuid']==row['node']),None)
        if not node or node.get('maintenance') or node.get('hidden'):return
        affected=[n.get('label',n['name']) for n in self.nodes() if row['node'] in n.get('manual_connection',{}).get('path',[]) and n['uuid']!=row['node']]
        text='❗ <b>ВАЖНО · '+html.escape(row['label'])+'</b>\n'+html.escape(TITLES[row['code']])+'. Неудачные попытки: '+str(sum(not a.get('ok') for a in failure.get('attempts',[])))+'/'+str(len(failure.get('attempts',[])))+'.\n'
        text+=('Из Литвы проверка проходит. Возможна фильтрация или проблема пути в сети РФ.' if failure.get('core_ok') else 'Причина пока не установлена: возможен сбой ноды или сети.')
        if failure.get('plateau_bytes'):text+=' Передача замирает примерно на '+str(round(failure['plateau_bytes']/1024))+' КБ.'
        if affected:text+='\nЗависят от этого входа: '+html.escape(', '.join(affected))+'.'
        text+='\nАвтоматически ничего не скрыто. Выберите действие.'
        self.notice('incident:'+str(identifier)+':'+str(int(time.time()//86400)),text,self.buttons(identifier))
        with self.store.db() as db:db.execute('UPDATE node_reviews SET next_alert=? WHERE id=?',(time.time()+86400,identifier))
        diag.event('ru_review_opened','WARNING',review_id=identifier,node_uuid=row['node'],classification=row['code'])
    def candidates(self,node):
        nodes=self.nodes();via=self.m.network.state().get('via',{});latest=self.latest();now=time.time();result=[]
        for n in nodes:
            if n['uuid']==node or n.get('effective_hidden') or n.get('maintenance') or via.get(n['uuid']):continue
            current=n['uuid'];seen=set()
            while current and current not in seen:seen.add(current);current=via.get(current)
            if node in seen:continue
            check=latest.get(n['uuid'],{})
            if check.get('ok') and check.get('code')=='healthy' and 0<=now-check.get('at',0)<7200:result.append({'uuid':n['uuid'],'label':n.get('label',n['name'])})
        return result
    def route_pending(self,node):
        with self.store.db() as db:return bool(db.execute("SELECT 1 FROM review_routes WHERE node=? AND state IN ('routing','checking_ru')",(node,)).fetchone())
    def visibility(self,node,hidden,request_id):
        if not hidden and self.route_pending(node):raise SafeError('review_route_check_pending')
        if hidden:
            with self.store.db() as db:
                db.execute("UPDATE review_routes SET state='cancelled' WHERE node=? AND state IN ('routing','checking_ru')",(node,))
                db.execute("UPDATE node_reviews SET state='hidden',updated=? WHERE node=? AND state='routing'",(time.time(),node))
            if next((n.get('hidden') for n in self.nodes() if n['uuid']==node),False):return {'ok':True,'hidden':True,'message':'Профиль остаётся скрытым. Автоматическое возвращение отменено.'}
        return self.m.visibility.set(node,hidden,request_id)
    def action(self,identifier,operation,target=None,request_id=None):
        row=self.review(identifier);node=next((n for n in self.nodes() if n['uuid']==row['node']),None)
        if not node or node['public_ip']!=row['ip']:raise SafeError('review_node_changed')
        current=next((r for r in self.meta('manifest_public',{}).get('targets',[]) if r['id']==node['uuid']),None)
        if not current or current['revision']!=row['revision']:
            if operation not in ('check','restore'):raise SafeError('review_configuration_changed')
        request_id=request_id or str(uuid.uuid4());message=''
        if operation=='check':return self.request(node['uuid'],identifier=request_id)
        if operation=='keep':
            if self.route_pending(node['uuid']):return {'ok':True,'message':'Сейчас проверяем новый вход. Профиль остаётся скрытым до результата.'}
            with self.store.db() as db:db.execute("UPDATE node_reviews SET state='kept',next_alert=?,updated=? WHERE id=?",(time.time()+86400,time.time(),identifier))
            message='Нода оставлена без изменений.'+(' Профиль сейчас скрыт; вернуть его можно в «Нодах».' if node.get('hidden') else ' При продолжающемся сбое повторно сообщу через сутки.')
        elif operation in ('hide','restore'):
            self.visibility(node['uuid'],operation=='hide',request_id)
            with self.store.db() as db:db.execute('UPDATE node_reviews SET state=?,updated=? WHERE id=?',('hidden' if operation=='hide' else 'restored',time.time(),identifier))
            message='Нода и зависимые профили скрыты. Сервер не удалён. Изменение появится после обновления подписки.' if operation=='hide' else 'Профиль возвращён. Обновите подписку в приложении.'
        elif operation=='via':
            if self.route_pending(node['uuid']):raise SafeError('review_route_check_pending')
            if target not in {n['uuid'] for n in self.candidates(node['uuid'])}:raise SafeError('review_parent_not_healthy')
            self.m.visibility.set(node['uuid'],True,request_id)
            catalog=self.m.network.catalog();via={n['uuid']:n.get('via') for n in catalog['nodes']};via[node['uuid']]=target
            job=self.m.network.submit('routes',{'revision':catalog['revision'],'request_id':str(uuid.uuid5(uuid.NAMESPACE_URL,'review-route:'+request_id)),'via':via})
            with self.store.db() as db:
                db.execute("INSERT OR IGNORE INTO review_routes VALUES(?,?,?,?,'routing',?)",(request_id,identifier,node['uuid'],job['id'],time.time()));db.execute("UPDATE node_reviews SET state='routing',updated=? WHERE id=?",(time.time(),identifier))
            message='Настраиваем новый вход. Профиль пока скрыт; вернётся только после проверки с Raspberry.'
        else:raise SafeError('invalid_review_action')
        self.wake.set();diag.event('ru_review_decision',review_id=identifier,operation=operation)
        return {'ok':True,'message':message}
    def reconcile_routes(self):
        with self.store.db() as db:rows=db.execute("SELECT * FROM review_routes WHERE state IN ('routing','checking_ru')").fetchall()
        for row in rows:
            if row['node'] not in {n['uuid'] for n in self.nodes()}:
                with self.store.db() as db:db.execute("UPDATE review_routes SET state='cancelled' WHERE id=?",(row['id'],));db.execute("UPDATE node_reviews SET state='superseded' WHERE id=?",(row['review'],))
                continue
            if row['state']=='checking_ru':continue
            job=self.m.vpn_config.public(row['change_id'])
            if job['state'] in ('ready','noop'):
                self.request(row['node'],'route:'+str(row['review']),str(uuid.uuid5(uuid.NAMESPACE_URL,'route-check:'+row['id'])))
                with self.store.db() as db:db.execute("UPDATE review_routes SET state='checking_ru' WHERE id=?",(row['id'],))
            elif job['state'] in ('failed','rolled_back','rollback_failed'):
                with self.store.db() as db:db.execute("UPDATE review_routes SET state='failed' WHERE id=?",(row['id'],));db.execute("UPDATE node_reviews SET state='hidden' WHERE id=?",(row['review'],))
                self.notice('route-failed:'+row['id'],'❗ <b>Новый маршрут не прошёл проверку</b>\nПрофиль остаётся скрытым. Подробности — в нодах и технических логах.')
    def finish_route(self,identifier,ok):
        row=self.review(identifier)
        with self.store.db() as db:pending=db.execute("SELECT 1 FROM review_routes WHERE review=? AND state='checking_ru'",(identifier,)).fetchone()
        if not pending:return
        if ok:
            self.m.visibility.set(row['node'],False,str(uuid.uuid5(uuid.NAMESPACE_URL,'route-publish:'+str(identifier))))
        with self.store.db() as db:db.execute('UPDATE node_reviews SET state=?,updated=? WHERE id=?',('restored' if ok else 'hidden',time.time(),identifier));db.execute('UPDATE review_routes SET state=? WHERE review=? AND state=\'checking_ru\'',('ready' if ok else 'failed',identifier))
        self.notice('route-published:'+str(identifier),'✅ <b>Новый вход работает из РФ</b>\nПрофиль возвращён в подписки. Обновите подписку в приложении.' if ok else '❗ <b>Новый вход не работает из РФ</b>\nПрофиль остаётся скрытым. Выберите другой вход в админке.')
    def snapshot(self):
        with self.store.db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM node_reviews ORDER BY updated DESC LIMIT 50')]
        return {'ok':True,'source':{'enabled':bool(self.config().get('enabled')),'name':'Домашний интернет РФ · Raspberry Pi','last_at':self.meta('last_probe_at'),'last_full_at':self.meta('last_full_at'),'running':self.meta('running',False) or self.meta('analyzing',False),'sync_error':self.meta('sync_error'),'interval_seconds':3600},'reviews':[{k:r[k] for k in ('id','node','revision','ip','label','code','state','created','updated')}|{'title':TITLES.get(r['code'],r['code']),'check':json.loads(r['body']),'candidates':self.candidates(r['node'])} for r in rows],'entry_ids':self.meta('manifest_public',{}).get('entry_ids',[]),'latest':self.latest(),'hidden_nodes':[{'uuid':n['uuid'],'label':n.get('label',n['name']),'hidden':n.get('hidden'), 'hidden_by':n.get('hidden_by',[])} for n in self.nodes() if n.get('effective_hidden')]}
    def claim_outbox(self,actor):
        self.owner(actor);now=time.time()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');rows=db.execute("SELECT * FROM review_outbox WHERE state='pending' AND lease<? ORDER BY created LIMIT 4",(now,)).fetchall()
            for row in rows:db.execute('UPDATE review_outbox SET lease=?,attempts=attempts+1 WHERE id=?',(now+120,row['id']))
        return [{'id':r['id'],**json.loads(r['body'])} for r in rows]
    def owner(self,actor):
        if not self.config().get('owner_id') or actor!=self.config()['owner_id']:raise SafeError('review_owner_only')
    def ack(self,actor,identifier,message_id):
        self.owner(actor)
        with self.store.db() as db:db.execute("UPDATE review_outbox SET state='sent',message_id=? WHERE id=?",(message_id,identifier))
        return {'ok':True}
    def callback(self,actor,data):
        self.owner(actor)
        if not data.startswith('kr:'):raise SafeError('review_button_invalid')
        with self.store.db() as db:row=db.execute('SELECT * FROM review_actions WHERE token=?',(data[3:],)).fetchone()
        if not row or row['expires']<time.time():raise SafeError('review_button_expired')
        if row['result']:
            saved=json.loads(row['result'])
            if row['operation'] not in ('check','all'):return saved
            with self.store.db() as db:previous=db.execute('SELECT state FROM review_requests WHERE id=?',(saved.get('request_id'),)).fetchone()
            if previous and previous['state']=='queued':return saved
            row=dict(row);row['request_id']=str(uuid.uuid4())
        if row['operation']=='all':result=self.request(identifier=row['request_id'])
        elif row['operation']=='choose':
            review=self.review(row['review']);candidates=self.candidates(review['node'])
            result={'ok':True,'message':'Выберите новый вход. Профиль сначала будет скрыт, затем проверен из РФ.','keyboard':[[{'text':c['label'],'callback_data':self.token(row['review'],'via',c['uuid'])}] for c in candidates]}
            if not candidates:result['message']='Сейчас нет другого входа со свежей успешной проверкой из РФ. Запустите повторную проверку.'
        else:result=self.action(row['review'],row['operation'],row['target'],row['request_id'])
        with self.store.db() as db:db.execute('UPDATE review_actions SET result=? WHERE token=?',(json.dumps(result,ensure_ascii=False),data[3:]))
        return result
