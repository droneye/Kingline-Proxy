"""Durable replacement of the private RU exit, prepared beside the live receiver."""
import copy,fcntl,ipaddress,json,os,re,subprocess,threading,time,uuid
from pathlib import Path
from . import diagnostics as diag
from .cover import certificate_lock
from .models import RUReceiver
from .probe import verify_tunnel
from .ru_egress import RUEgress
from .ru_mesh_remote import options
from .ru_policy import build
from .ssh import SafeError
from .store import atomic_json

class RUInstaller:
    def __init__(self,manager):
        self.m=manager;self.store=manager.store;self.thread=None
        self.gateway=RUEgress(manager.root,manager.control,manager.ssh_factory)
        with self.store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS ru_jobs(
                id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,generation INTEGER UNIQUE NOT NULL,
                ip TEXT NOT NULL,request TEXT NOT NULL,secret BLOB,context BLOB NOT NULL,
                state TEXT NOT NULL,phase TEXT NOT NULL,error TEXT,history TEXT NOT NULL,
                created REAL NOT NULL,updated REAL NOT NULL);''')
    def active(self):
        with self.store.db() as db:return bool(db.execute("SELECT 1 FROM ru_jobs WHERE state IN ('queued','running')").fetchone())
    def public(self,row):
        context=json.loads(self.store.cipher.decrypt(row['context']))
        progress={k:context.get(k) for k in ('current_node','node_total','verified_nodes')}
        return {k:row[k] for k in ('id','ip','state','phase','error','created','updated')}|{'events':json.loads(row['history']),'progress':progress}
    def jobs(self):
        with self.store.db() as db:return [self.public(r) for r in db.execute('SELECT * FROM ru_jobs ORDER BY created DESC LIMIT 20')]
    def read(self,identifier):
        with self.store.db() as db:row=db.execute('SELECT * FROM ru_jobs WHERE id=?',(identifier,)).fetchone()
        if row is None:raise KeyError(identifier)
        result=dict(row);result['body']=json.loads(row['request']);result['ctx']=json.loads(self.store.cipher.decrypt(row['context']))
        result['credentials']=json.loads(self.store.cipher.decrypt(row['secret'])) if row['secret'] else {}
        return result
    def phase(self,identifier,phase,context=None,*,state='running',error=None,clear_secret=False):
        with self.store.db() as db:
            row=db.execute('SELECT * FROM ru_jobs WHERE id=?',(identifier,)).fetchone();history=json.loads(row['history'])
            history.append({'at':time.time(),'phase':phase})
            db.execute('UPDATE ru_jobs SET state=?,phase=?,error=?,context=?,secret=?,history=?,updated=? WHERE id=?',
                (state,phase,error,self.store.cipher.encrypt(json.dumps(context).encode()) if context is not None else row['context'],None if clear_secret else row['secret'],json.dumps(history[-200:]),time.time(),identifier))
        diag.event('ru_installation_phase','ERROR' if error else 'INFO',job_id=identifier,phase=phase,error=error)
    def submit(self,request):
        with self.m.lock:return self._submit(request)
    def _submit(self,request):
        public=request.model_dump(exclude={'credentials'});encoded=json.dumps(public,sort_keys=True)
        protected=set(self.m.settings.get('protected_ips',[]))|{self.m.settings['core_public_ip'],'198.51.100.20'}|{n['public_ip'] for n in self.store.nodes()}
        if request.ip in protected:raise SafeError('protected_server_address')
        if not self.gateway.enabled():raise SafeError('ru_gateway_not_configured')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT * FROM ru_jobs WHERE request_id=?',(request.request_id,)).fetchone()
            if old:
                if old['request']!=encoded:raise SafeError('idempotency_key_conflict')
                return self.public(old)
            if db.execute("SELECT 1 FROM ru_jobs WHERE state IN ('queued','running')").fetchone():raise SafeError('ru_installation_busy')
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():raise SafeError('vpn_installation_busy')
            if db.execute("SELECT 1 FROM vpn_changes WHERE state IN ('queued_check','checking','queued_apply','applying')").fetchone():raise SafeError('vpn_operation_busy')
            generation=(db.execute('SELECT max(generation) FROM ru_jobs').fetchone()[0] or 0)+1
            current=json.loads(self.gateway.path.read_text());generation=max(generation,int(current.get('generation',0))+1)
            if generation>254:raise SafeError('ru_address_pool_exhausted')
            identifier=str(uuid.uuid4());now=time.time()
            db.execute('INSERT INTO ru_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(identifier,request.request_id,generation,request.ip,encoded,self.store.cipher.encrypt(json.dumps(request.credentials.reveal()).encode()),self.store.cipher.encrypt(b'{}'),'queued','queued',None,json.dumps([{'at':now,'phase':'queued'}]),now,now))
            return self.public(db.execute('SELECT * FROM ru_jobs WHERE id=?',(identifier,)).fetchone())
    def retry(self,identifier,credentials):
        with self.m.lock:return self._retry(identifier,credentials)
    def _retry(self,identifier,credentials):
        row=self.read(identifier)
        if row['state']!='failed':raise SafeError('ru_job_is_not_failed')
        if row['error']=='ru_rollback_incomplete':raise SafeError('ru_manual_recovery_required')
        context=row['ctx'];context.pop('profiles_before',None)
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM ru_jobs WHERE state IN ('queued','running')").fetchone():raise SafeError('ru_installation_busy')
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():raise SafeError('vpn_installation_busy')
            if db.execute("SELECT 1 FROM vpn_changes WHERE state IN ('queued_check','checking','queued_apply','applying')").fetchone():raise SafeError('vpn_operation_busy')
            db.execute("UPDATE ru_jobs SET state='queued',phase='queued',error=NULL,secret=?,context=?,updated=? WHERE id=?",(self.store.cipher.encrypt(json.dumps(credentials.reveal()).encode()),self.store.cipher.encrypt(json.dumps(context).encode()),time.time(),identifier))
        return self.public(self.read(identifier))
    def start(self):
        with self.store.db() as db:db.execute("UPDATE ru_jobs SET state='queued' WHERE state='running'")
        self.thread=threading.Thread(target=self.worker,daemon=True,name='ru-installer');self.thread.start()
    def worker(self):
        while not self.m.stop.is_set():
            try:
                with self.store.db() as db:
                    db.execute('BEGIN IMMEDIATE');row=db.execute("SELECT id FROM ru_jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
                    if row:db.execute("UPDATE ru_jobs SET state='running' WHERE id=?",(row['id'],))
                if row:self.execute(row['id'])
                else:self.m.stop.wait(1)
            except Exception as error:
                diag.event('ru_worker_failed','ERROR',error=type(error).__name__);self.m.stop.wait(3)
    def remote(self,request,payload,*,credentials=None,bootstrap=False,timeout=120):
        ssh=self.m.ssh_factory(self.m.root,request,credentials) if credentials else self.m.ssh_factory(self.m.root,request)
        try:
            file='ru_receiver_remote.py' if bootstrap else 'ru_mesh_remote.py'
            result=json.loads(ssh.python(Path(__file__).with_name(file).read_text(),payload,timeout=timeout))
            if result.get('ok') is False:raise SafeError(result.get('error','ru_remote_failed'))
            return result
        finally:ssh.close()
    def core_peer(self,public,address,remove=False):
        assert re.fullmatch(r'[A-Za-z0-9+/]{43}=',public)
        ipaddress.IPv4Address(address)
        file=Path('/etc/wireguard/wg-ru-control.conf');old=file.read_text()
        blocks=old.split('[Peer]');kept=[b for b in blocks[1:] if not re.search(r'^PublicKey\s*=\s*'+re.escape(public)+r'\s*$',b,re.M)]
        new=blocks[0]+''.join('[Peer]'+b for b in kept)
        if not remove:new+='\n[Peer]\nPublicKey = '+public+'\nAllowedIPs = '+address+'/32\n'
        temp=file.with_suffix('.ru-new');temp.write_text(new);temp.chmod(0o600);temp.replace(file)
        try:
            subprocess.run(['wg','set','wg-ru-control','peer',public,*(['remove'] if remove else ['allowed-ips',address+'/32'])],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            subprocess.run(['ip','route','del' if remove else 'replace',address+'/32','dev','wg-ru-control'],check=not remove,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except Exception:
            file.write_text(old);raise
    def wait_node(self,identifier):
        for _ in range(90):
            node=self.m.api.call('nodes/'+identifier)
            if node.get('isConnected') and node.get('xrayUptime',0)>0:return
            if self.m.stop.wait(2):raise SafeError('worker_stopping')
        raise SafeError('ru_panel_connection_timeout')
    def execute(self,identifier):
        row=self.read(identifier);body=row['body'];context=row['ctx'];generation=row['generation'];o=options({'generation':generation})
        context['job_id']=identifier
        live=json.loads(self.gateway.path.read_text())
        context.setdefault('old',live);old=context['old'];committed=context.get('committed',False) or (context.get('node_uuid') and live.get('node_uuid')==context['node_uuid'])
        public_request={'ip':body['ip'],'ssh_port':body['ssh_port'],'host_key_sha256':context.get('fingerprint')}
        try:
            if committed:
                context['committed']=True
                self.phase(identifier,'retiring_old',context)
                self.retire(context)
                self.phase(identifier,'ready',context,state='ready',clear_secret=True);return
            self.phase(identifier,'ssh_preflight',context)
            credentials=None if context.get('key_ready') else row['credentials']
            ssh=self.m.ssh_factory(self.m.root,public_request,credentials) if credentials else self.m.ssh_factory(self.m.root,public_request)
            try:context['fingerprint']=ssh.fingerprint
            finally:ssh.close()
            public_request['host_key_sha256']=context['fingerprint']
            base={'generation':generation,'core_ip':self.m.settings['core_public_ip'],'job_id':identifier,'allow_legacy':body['ip']==old.get('origin_ip')}
            self.remote(public_request,{**base,'mode':'preflight'},credentials=credentials,bootstrap=True)
            self.phase(identifier,'managed_key',context)
            self.remote(public_request,{**base,'mode':'install_key','ssh_public_key':(self.m.root/'id_ed25519.pub').read_text()},credentials=credentials,bootstrap=True)
            check=self.m.ssh_factory(self.m.root,public_request)
            try:
                if check.run('id -u').strip()!='0':raise SafeError('node_ssh_not_root')
            finally:check.close()
            context['key_ready']=True;self.phase(identifier,'packages',context,clear_secret=True)
            self.remote(public_request,{**base,'mode':'packages'},bootstrap=True,timeout=1800)
            harden=Path(__file__).with_name('remote_install.py').read_text().partition("if __name__ == '__main__':")[0]
            ssh=self.m.ssh_factory(self.m.root,public_request)
            try:ssh.python(harden+"\np=json.load(sys.stdin)\nprint(json.dumps(harden_ssh(p)))",{'core_public_ip':self.m.settings['core_public_ip']},timeout=40)
            finally:ssh.close()
            self.phase(identifier,'management_network',context)
            management=self.remote(public_request,{**base,'mode':'control'},bootstrap=True)
            context['management_public_key']=management['public_key'];self.phase(identifier,'management_network',context)
            self.core_peer(management['public_key'],o['management_ip'])
            core_public=subprocess.check_output(['wg','pubkey'],input=Path('/etc/wireguard/ru-control.key').read_bytes()).decode().strip()
            self.remote(public_request,{**base,'mode':'control','core_public_key':core_public},bootstrap=True)
            receiver={'ip':o['management_ip'],'ssh_port':body['ssh_port'],'host_key_sha256':context['fingerprint']}
            self.phase(identifier,'private_peers',context)
            ru_key=self.remote(public_request,{'mode':'key','generation':generation})['public_key']
            selected=[n for n in self.store.nodes() if n.get('provisioned',True) and self.gateway.selected(n,old)]
            context['node_total']=len(selected);context['verified_nodes']=0
            peers=[]
            for node in selected:
                context['current_node']=node.get('label',node['public_ip'])
                request={'ip':node['public_ip'],'ssh_port':node.get('ssh_port',22),'host_key_sha256':node.get('host_key_sha256')}
                key=self.remote(request,{'mode':'key','generation':generation})['public_key']
                peer={'uuid':node['uuid'],'public_ip':node['public_ip'],'public_key':key,'address':f"10.78.{generation}."+node['management_ip'].split('.')[-1],'request':request,'port':51821+generation}
                if any(p['address']==peer['address'] for p in peers):raise SafeError('ru_peer_address_conflict')
                peers.append(peer);context['peers']=peers;self.phase(identifier,'private_peers',context)
                self.remote(request,{'mode':'mesh','generation':generation,'role':'head','address':peer['address'],'peers':[{'address':o['ru_ip'],'public_key':ru_key}]})
            self.remote(public_request,{'mode':'mesh','generation':generation,'role':'pi','address':o['ru_ip'],'peers':peers})
            self.phase(identifier,'container',context)
            self.remote(public_request,{'mode':'container','generation':generation,'image':self.m.settings['node_image'],'secret':self.m.api.call('keygen')['secretKey']},timeout=800)
            self.phase(identifier,'panel_registration',context)
            nodes=self.m.api.items('nodes','nodes');candidate=next((n for n in nodes if n['address']==o['management_ip']),None)
            if not candidate:
                candidate=self.m.api.call('nodes','POST',{'name':'RU-Egress-'+identifier[:8],'address':o['management_ip'],'port':2222,'countryCode':'RU','isTrafficTrackingActive':False,'configProfile':{'activeConfigProfileUuid':old['profile_uuid'],'activeInbounds':[old['inbound_uuid']]}})
            context['node_uuid']=candidate['uuid'];self.phase(identifier,'waiting_for_node',context)
            self.wait_node(candidate['uuid'])
            ssh=self.m.ssh_factory(self.m.root,public_request)
            try:wan=ssh.run('curl -4fsS --max-time 20 https://api.ipify.org',timeout=25).strip()
            finally:ssh.close()
            if not ipaddress.IPv4Address(wan).is_global:raise SafeError('ru_public_exit_invalid')
            context['wan']=wan;self.phase(identifier,'verifying_private',context)
            for peer in peers:
                context['current_node']=next((n.get('label',n['public_ip']) for n in selected if n['uuid']==peer['uuid']),peer['public_ip'])
                self.remote(peer['request'],{'mode':'probe','generation':generation,'vless_uuid':old['vless_uuid'],'expected_ip':wan},timeout=90)
                context['verified_nodes']+=1;self.phase(identifier,'verifying_private',context)
            self.phase(identifier,'switching',context)
            self.commit(context,generation,receiver,body)
            context['committed']=True;self.phase(identifier,'retiring_old',context)
            self.retire(context)
            self.phase(identifier,'ready',context,state='ready',clear_secret=True)
        except Exception as error:
            # The old receiver stays authoritative until commit has verified
            # real customer paths. Candidate artifacts are isolated for retry.
            code=str(error) if isinstance(error,SafeError) else 'ru_installation_'+type(error).__name__
            if not context.get('committed') and code!='ru_rollback_incomplete':
                for peer in context.get('peers',[]):
                    try:self.remote(peer['request'],{'mode':'remove','generation':generation})
                    except Exception:diag.event('ru_candidate_peer_cleanup_pending','WARNING',job_id=identifier)
                if context.get('key_ready'):
                    try:self.remote(public_request,{'mode':'retire','generation':generation},bootstrap=True,timeout=30)
                    except Exception:diag.event('ru_candidate_cleanup_pending','WARNING',job_id=identifier)
                if context.get('node_uuid'):
                    try:self.m.api.call('nodes/'+context['node_uuid'],'DELETE')
                    except Exception:diag.event('ru_candidate_node_cleanup_pending','WARNING',job_id=identifier)
                if context.get('management_public_key'):
                    try:self.core_peer(context['management_public_key'],o['management_ip'],remove=True)
                    except Exception:diag.event('ru_candidate_control_cleanup_pending','WARNING',job_id=identifier)
            self.phase(identifier,'failed',context,state='failed',error=code,clear_secret=True)
    def retire(self,context):
        old=context['old']
        for peer in old.get('mesh_peers',[]):
            try:self.remote(peer['request'],{'mode':'remove','generation':old.get('generation',0)})
            except Exception as error:diag.event('ru_old_peer_retirement_pending','WARNING',error=type(error).__name__)
        try:self.remote(old['pi'],{'mode':'retire','generation':old.get('generation',0)},bootstrap=True,timeout=30)
        except Exception as error:diag.event('ru_old_receiver_retirement_pending','WARNING',error=type(error).__name__)
        try:self.m.api.call('nodes','PATCH',{'uuid':old['node_uuid'],'isDisabled':True})
        except Exception as error:diag.event('ru_old_node_disable_pending','WARNING',error=type(error).__name__)
        try:self.gateway.tick()
        except Exception as error:diag.event('ru_metrics_refresh_pending','WARNING',error=type(error).__name__)
    def commit(self,context,generation,receiver,body):
        old=context['old'];o=options({'generation':generation});before={};changed=[]
        with self.m.lock,certificate_lock(self.m.root),(self.m.control/'ru-egress.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            current=json.loads(self.gateway.path.read_text())
            if current.get('node_uuid')!=old['node_uuid']:raise SafeError('ru_receiver_changed')
            heads=[n for n in self.store.nodes() if n.get('provisioned',True)]
            panel={n['uuid']:n for n in self.m.api.items('nodes','nodes')}
            profiles={panel[n['uuid']]['configProfile']['activeConfigProfileUuid'] for n in heads}
            before=context.get('profiles_before') or {uid:self.m.api.call('config-profiles/'+uid)['config'] for uid in profiles}
            if set(before)!=profiles:raise SafeError('ru_profiles_changed')
            context['profiles_before']=before
            self.phase(context['job_id'],'switching',context)
            try:
                for node in heads:
                    if not self.gateway.selected(node,old):
                        self.remote({'ip':node['public_ip'],'ssh_port':node.get('ssh_port',22),'host_key_sha256':node.get('host_key_sha256')},{'mode':'direct','generation':generation})
                for uid,config in before.items():
                    candidate=build(config,old['vless_uuid'],old['domains'],o['ru_ip'])
                    folder=self.m.control/'ru-job-backups'/context['job_id'];folder.mkdir(parents=True,exist_ok=True,mode=0o700)
                    file=folder/'candidate.json';atomic_json(file,candidate)
                    tested=subprocess.run([str(self.m.control/'client-26.6.27/xray'),'run','-test','-config',str(file)],capture_output=True,env={**os.environ,'XRAY_LOCATION_ASSET':str(self.m.control/'diagnostics/assets')})
                    if tested.returncode:raise SafeError('ru_xray_validation_failed')
                    self.m.api.call('config-profiles','PATCH',{'uuid':uid,'config':candidate});changed.append(uid)
                for node in heads:
                    expected=context['wan'] if self.gateway.selected(node,old) else node['public_ip']
                    for attempt in range(4):
                        try:
                            verify_tunnel(self.m.control,node['public_ip'],exit_urls=('https://ipv4-internet.yandex.net/api/v0/ip',),expected_exit_ip=expected);break
                        except Exception:
                            if attempt==3:raise SafeError('ru_public_route_check_failed')
                            time.sleep(10)
                state={**old,'generation':generation,'pi':receiver,'origin_ip':body['ip'],'label':body['display_name'],
                       'public_ip':context['wan'],'node_uuid':context['node_uuid'],'mesh_peers':context.get('peers',[]),'cpu':None,
                       'direct_targets':{n['uuid']:o['ru_ip'] for n in heads if not self.gateway.selected(n,old)}}
                atomic_json(self.gateway.path,state)
            except BaseException:
                failed=False
                for uid in changed:
                    try:self.m.api.call('config-profiles','PATCH',{'uuid':uid,'config':before[uid]})
                    except Exception:failed=True
                for node in heads:
                    if not self.gateway.selected(node,old):
                        try:self.remote({'ip':node['public_ip'],'ssh_port':node.get('ssh_port',22),'host_key_sha256':node.get('host_key_sha256')},{'mode':'direct','generation':old.get('generation',0)})
                        except Exception:failed=True
                if failed:raise SafeError('ru_rollback_incomplete') from None
                raise
