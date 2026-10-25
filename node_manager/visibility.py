"""Reversible publication control. Hiding never revokes users or deletes a node."""
import json,time,uuid
from .cover import certificate_lock
from .ssh import SafeError
from . import diagnostics as diag


def hidden_paths(nodes,via):
    by_id={n['uuid']:n for n in nodes};result={}
    for node in nodes:
        seen=set();current=node['uuid'];blocked=[]
        while current:
            if current in seen:raise SafeError('network_route_cycle')
            seen.add(current)
            if by_id.get(current,{}).get('hidden',False):blocked.append(current)
            current=via.get(current)
        result[node['uuid']]=blocked
    return result

class Visibility:
    def __init__(self,manager):
        self.m=manager;self.store=manager.store;self.api=manager.network.api
        with self.store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS visibility_hosts(host TEXT PRIMARY KEY,was_disabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS visibility_changes(id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,state TEXT NOT NULL,payload BLOB NOT NULL,error TEXT,created REAL NOT NULL);''')
    def guard(self):
        if self.m.vpn_config.busy():raise SafeError('vpn_operation_busy')
        if getattr(self.m,'ru_installer',None) and self.m.ru_installer.active():raise SafeError('ru_installation_busy')
    def recover(self):
        with self.m.lock,certificate_lock(self.m.root):
            with self.store.db() as db:pending=db.execute("SELECT * FROM visibility_changes WHERE state='applying'").fetchall()
            for row in pending:
                data=json.loads(self.store.cipher.decrypt(row['payload']));failed=False
                for host in data['patches']:
                    try:self.api.call('hosts','PATCH',{'uuid':host['uuid'],'isDisabled':host['before']})
                    except Exception:failed=True
                if failed:raise SafeError('visibility_recovery_incomplete')
                with self.store.db() as db:db.execute("UPDATE visibility_changes SET state='failed',error='interrupted_restored' WHERE id=?",(row['id'],))
    def set(self,node_uuid,hidden,request_id):
        with self.m.lock:
            self.guard();self.recover()
            with certificate_lock(self.m.root):return self._apply(node_uuid,hidden,request_id)
    def sync(self):
        with self.m.lock:
            self.guard();self.recover()
            with certificate_lock(self.m.root):return self._apply(None,None,str(uuid.uuid4()))
    def _apply(self,node_uuid,hidden,request_id):
        nodes=self.m.network.nodes();by_id={n['uuid']:n for n in nodes}
        if node_uuid and node_uuid not in by_id:raise SafeError('node_not_found')
        identity={'node':node_uuid,'hidden':hidden}
        with self.store.db() as db:
            old=db.execute('SELECT * FROM visibility_changes WHERE request_id=?',(request_id,)).fetchone()
            ledger={r['host']:bool(r['was_disabled']) for r in db.execute('SELECT * FROM visibility_hosts')}
        if old:
            data=json.loads(self.store.cipher.decrypt(old['payload']))
            if data['identity']!=identity:raise SafeError('idempotency_key_conflict')
            if old['state']!='ready':raise SafeError(old['error'] or 'visibility_failed')
            return data['result']
        before_node=dict(by_id[node_uuid]) if node_uuid else None
        old_blockers=hidden_paths(list(by_id.values()),self.m.network.state().get('via',{}))
        if node_uuid:by_id[node_uuid]['hidden']=bool(hidden)
        blockers=hidden_paths(list(by_id.values()),self.m.network.state().get('via',{}))
        hosts={h['uuid']:h for h in self.api.items('hosts','hosts')};next_ledger={k:v for k,v in ledger.items() if k in hosts};patches=[]
        for node in nodes:
            host=hosts.get(node.get('host_uuid'))
            if not host:continue
            key=host['uuid'];disabled=bool(host.get('isDisabled',False))
            if blockers[node['uuid']]:
                if key not in next_ledger:next_ledger[key]=disabled
                desired=True
            elif key in next_ledger:desired=next_ledger.pop(key)
            else:continue
            if desired!=disabled:patches.append({'uuid':key,'before':disabled,'after':desired})
        result={'ok':True,'node_uuid':node_uuid,'hidden':bool(hidden),'affected':[n['uuid'] for n in nodes if node_uuid in blockers[n['uuid']]+old_blockers[n['uuid']]],'subscription_refresh_required':True}
        if node_uuid is None and not patches and next_ledger==ledger:return result
        identifier=str(uuid.uuid4());data={'identity':identity,'patches':patches,'result':result}
        with self.store.db() as db:db.execute('INSERT INTO visibility_changes VALUES(?,?,?, ?,NULL,?)',(identifier,request_id,'applying',self.store.cipher.encrypt(json.dumps(data).encode()),time.time()))
        try:
            for host in patches:self.api.call('hosts','PATCH',{'uuid':host['uuid'],'isDisabled':host['after']})
            with self.store.db() as db:
                if node_uuid:db.execute('UPDATE nodes SET metadata=? WHERE uuid=?',(json.dumps(by_id[node_uuid],ensure_ascii=False),node_uuid))
                db.execute('DELETE FROM visibility_hosts');db.executemany('INSERT INTO visibility_hosts VALUES(?,?)',[(k,int(v)) for k,v in next_ledger.items()])
                db.execute("UPDATE visibility_changes SET state='ready' WHERE id=?",(identifier,))
                db.execute("DELETE FROM visibility_changes WHERE state!='applying' AND id NOT IN (SELECT id FROM visibility_changes ORDER BY created DESC LIMIT 100)")
            self.m.export_inventory()
        except Exception:
            failed=False
            for host in patches:
                try:self.api.call('hosts','PATCH',{'uuid':host['uuid'],'isDisabled':host['before']})
                except Exception:failed=True
            # A committed DB is authoritative if only the derived inventory export failed.
            with self.store.db() as db:committed=db.execute('SELECT state FROM visibility_changes WHERE id=?',(identifier,)).fetchone()[0]=='ready'
            if committed:
                if before_node:self.store.save_node(before_node)
                with self.store.db() as db:
                    db.execute('DELETE FROM visibility_hosts');db.executemany('INSERT INTO visibility_hosts VALUES(?,?)',[(k,int(v)) for k,v in ledger.items()])
            with self.store.db() as db:db.execute("UPDATE visibility_changes SET state=?,error=? WHERE id=?",('applying' if failed else 'failed','visibility_recovery_incomplete' if failed else 'visibility_update_failed',identifier))
            try:self.m.export_inventory()
            except Exception:pass
            raise SafeError('visibility_recovery_incomplete' if failed else 'visibility_update_failed') from None
        diag.event('node_publication_changed',node_uuid=node_uuid,hidden=bool(hidden),affected=len(result['affected']))
        return result
