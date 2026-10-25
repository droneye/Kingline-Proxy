"""Maintain direct private RU peers for current and newly enrolled exit nodes."""
import fcntl,json,time
from pathlib import Path
from .ssh import SSH
from .store import Store,atomic_json
from . import diagnostics as diag
from .ru_mesh_remote import options

class RUEgress:
    def __init__(self,data_root='/opt/kingline-manager/data',control='/opt/kingline-control',ssh_factory=SSH):
        self.root=Path(data_root);self.control=Path(control);self.path=self.control/'ru-egress.json';self.ssh_factory=ssh_factory
    def enabled(self):return self.path.exists() and json.loads(self.path.read_text()).get('enabled',False)
    def public(self):
        if not self.path.exists():return {'configured':False}
        state=json.loads(self.path.read_text())
        try:report=json.loads((self.control/'ru-resources.json').read_text())
        except (OSError,ValueError):report={}
        return {'configured':bool(state.get('enabled')),'name':state.get('label','Российский выход · Raspberry Pi'),
                'ip':state.get('public_ip'),'generation':state.get('generation',0),
                'online':bool(report.get('online') and time.time()-report.get('at',0)<180)}
    @staticmethod
    def selected(node,state):return node.get('ru_enabled',node['uuid'] not in state.get('direct_fallback_nodes',[]))
    def invoke(self,request,payload,timeout=90):
        s=self.ssh_factory(self.root,request)
        try:return json.loads(s.python(Path(__file__).with_name('ru_mesh_remote.py').read_text(),payload,timeout=timeout))
        finally:s.close()
    def provision(self,node):
        if not self.enabled():return
        with (self.control/'ru-egress.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);state=json.loads(self.path.read_text())
            generation=state.get('generation',0);o=options({'generation':generation})
            if not self.selected(node,state):
                if state.get('direct_targets',{}).get(node['uuid'])==o['ru_ip'] and not any(p['uuid']==node['uuid'] for p in state['mesh_peers']):return
                request={'ip':node['public_ip'],'ssh_port':node.get('ssh_port',22),'host_key_sha256':node.get('host_key_sha256')}
                self.invoke(request,{'mode':'direct','generation':generation})
                state['mesh_peers']=[p for p in state['mesh_peers'] if p['uuid']!=node['uuid']]
                state.setdefault('direct_targets',{})[node['uuid']]=o['ru_ip']
                state['direct_fallback_nodes']=list(set(state.get('direct_fallback_nodes',[]))|{node['uuid']})
                atomic_json(self.path,state)
                try:self.invoke(state['pi'],{'mode':'mesh','role':'pi','generation':generation,'address':o['ru_ip'],'peers':state['mesh_peers']})
                except Exception as e:diag.event('ru_peer_revocation_pending','WARNING',error=type(e).__name__)
                return
            if any(p['uuid']==node['uuid'] for p in state['mesh_peers']):return
            request={'ip':node['public_ip'],'ssh_port':node.get('ssh_port',22),'host_key_sha256':node.get('host_key_sha256')}
            key=self.invoke(request,{'mode':'key','generation':generation})['public_key']
            pi_key=self.invoke(state['pi'],{'mode':'key','generation':generation})['public_key']
            peer={'uuid':node['uuid'],'public_ip':node['public_ip'],'public_key':key,'address':'10.78.'+str(generation)+'.'+node['management_ip'].split('.')[-1],'request':request,'port':51821+generation}
            if any(p['address']==peer['address'] for p in state['mesh_peers']):raise ValueError('ru_peer_address_conflict')
            peers=state['mesh_peers']+[peer]
            self.invoke(state['pi'],{'mode':'mesh','role':'pi','generation':generation,'address':o['ru_ip'],'peers':peers})
            self.invoke(request,{'mode':'mesh','role':'head','generation':generation,'address':peer['address'],'release_direct':True,'peers':[{'address':o['ru_ip'],'public_key':pi_key}]})
            state['direct_fallback_nodes']=[v for v in state.get('direct_fallback_nodes',[]) if v!=node['uuid']]
            state.setdefault('direct_targets',{}).pop(node['uuid'],None)
            state['mesh_peers']=peers;atomic_json(self.path,state)
    def remove(self,node):
        if not self.enabled():return
        with (self.control/'ru-egress.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);state=json.loads(self.path.read_text())
            peers=[p for p in state['mesh_peers'] if p['uuid']!=node['uuid']]
            if len(peers)==len(state['mesh_peers']) and node['uuid'] not in state.get('direct_fallback_nodes',[]):return
            state['direct_fallback_nodes']=[v for v in state.get('direct_fallback_nodes',[]) if v!=node['uuid']]
            state['mesh_peers']=peers;atomic_json(self.path,state)
            generation=state.get('generation',0);o=options({'generation':generation})
            try:self.invoke(state['pi'],{'mode':'mesh','role':'pi','generation':generation,'address':o['ru_ip'],'peers':peers})
            except Exception as e:diag.event('ru_peer_revocation_pending','WARNING',error=type(e).__name__)
            self.invoke({'ip':node['public_ip'],'ssh_port':node.get('ssh_port',22),'host_key_sha256':node.get('host_key_sha256')},{'mode':'remove','generation':generation,'remove_direct':True})
    def tick(self):
        if not self.enabled():return
        for node in Store(self.root).nodes():
            if node.get('provisioned',True):
                try:self.provision(node)
                except Exception as e:diag.event('ru_peer_setup_failed','WARNING',node_uuid=node['uuid'],error=type(e).__name__)
        with (self.control/'ru-egress.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX);state=json.loads(self.path.read_text())
            o=options(state)
            self.invoke(state['pi'],{'mode':'mesh','role':'pi','generation':state.get('generation',0),'address':o['ru_ip'],'peers':state['mesh_peers']})
            sampler=Path('/opt/kingline-staging/ops/resource_metrics.py').read_text()
            result=self.invoke_resources(state,sampler)
            from remna_api import RemnaAPI
            node=RemnaAPI(self.control/'nodes-read.token').call('nodes/'+state['node_uuid'])
            result['online']=bool(result['online'] and node['isConnected'] and node.get('xrayUptime',0)>0)
            atomic_json(self.control/'ru-resources.json',result)
    def invoke_resources(self,state,sampler):
        s=self.ssh_factory(self.root,state['pi'])
        source=sampler+'''
import json,sys,subprocess
p=json.load(sys.stdin)
resources,cpu=sample_resources(p.get('cpu'))
def output(command):
    return subprocess.run(command,capture_output=True,text=True).stdout.strip()
legacy=output(['systemctl','is-active','x-ui'])=='active'
container=output(['docker','inspect',p['container'],'--format','{{.State.Running}}'])=='true'
print(json.dumps({'resources':resources,'cpu':cpu,'legacy_active':legacy,'container_running':container}))
'''
        try:
            result=json.loads(s.python(source,{'cpu':state.get('cpu'),'container':options(state)['container']},timeout=20))
        finally:s.close()
        state['cpu']=result.pop('cpu');atomic_json(self.path,state)
        return {'at':time.time(),'public_ip':state.get('public_ip'),'online':result['container_running'],'legacy_active':result['legacy_active'],'resources':result['resources']}

if __name__=='__main__':
    diag.configure('ru-egress')
    try:RUEgress().tick()
    except Exception as error:
        diag.event('ru_egress_tick_failed','ERROR',error=type(error).__name__)
        raise SystemExit(1) from None
