import base64
import contextlib
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import threading
import time

import paramiko

from remna_api import RemnaAPI
from .probe import verify_tunnel
from .ssh import SSH, SafeError, PREFLIGHT, INSTALL_KEY
from .store import Store, atomic_json
from .cover import Cover, REMOVE_COVER, certificate_lock, sync_dns as sync_cover_dns
from . import domains
from .mobile import REMOVE_MOBILE_FIREWALL
from .vpn_config import VPNConfig, FINGERPRINTS
from .node_network import NodeNetwork


class Manager:
    def __init__(self, root, api=None, ssh_factory=SSH, probe=verify_tunnel):
        self.root = Path(root)
        self.settings = json.loads((self.root / 'settings.json').read_text())
        self.control = Path(self.settings['control_directory'])
        self.store = Store(root)
        self.api = api or RemnaAPI(self.root / 'panel.token')
        self.ssh_factory, self.probe = ssh_factory, probe
        self.cover = Cover(self.settings['cover']) if self.settings.get('cover') else None
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.vpn_config = VPNConfig(self)
        self.network = NodeNetwork(self, self.vpn_config)
        from .ru_installer import RUInstaller
        self.ru_installer = RUInstaller(self)
        from .visibility import Visibility
        self.visibility = Visibility(self)
        from .reviews import Reviews
        self.reviews = Reviews(self)
        from .billing import BillingReminders
        self.billing = BillingReminders(self)
        from .onboarding import Onboarding
        self.onboarding = Onboarding(self, user_api=api)

    def standard(self):
        state = json.loads((self.control / 'owner-profile.json').read_text())
        profile = self.api.call('config-profiles/' + state['profile_uuid'])
        inbound = next(i for i in profile['config']['inbounds'] if i['tag'] == 'KINGLINE_XHTTP_REALITY')
        stream = inbound['streamSettings']
        if not (inbound['port'] == 443 and stream['network'] in ('tcp', 'raw')
                and stream['security'] == 'reality' and inbound['settings']['flow'] == 'xtls-rprx-vision'
                and state['fingerprint'] in FINGERPRINTS and state['sni'] in stream['realitySettings']['serverNames']):
            raise SafeError('standard_profile_drift')
        if self.cover and (state['sni'] != self.cover.settings['domain'] or
                          stream['realitySettings'].get('target', stream['realitySettings'].get('dest'))
                          != self.cover.settings['target']):
            raise SafeError('owned_target_profile_drift')
        target = stream['realitySettings'].get('target', stream['realitySettings'].get('dest'))
        if state.get('reality_target') and target != state['reality_target']:
            raise SafeError('standard_target_drift')
        if self.settings.get('enforce_uniform_profiles'):
            manual={n.get('host_uuid'):n for n in self.store.nodes()}
            for host in self.api.items('hosts', 'hosts'):
                bound = host.get('inbound', {})
                if bound.get('configProfileUuid') != state['profile_uuid']:
                    continue
                endpoint=self.network.expected_endpoint(manual[host['uuid']]['uuid']) if host.get('uuid') in manual else None
                expected_port=endpoint['port'] if endpoint else inbound['port']
                if (host.get('port') != expected_port or host.get('sni') != state['sni']
                        or (endpoint and host.get('address')!=endpoint['address'])
                        or host.get('fingerprint') != state['fingerprint']
                        or bound.get('configProfileInboundUuid') != state['inbound_uuid']):
                    raise SafeError('published_profile_drift')
        return {**state, 'reality_target': target}

    def standard_public(self):
        state = self.standard()
        return {'protocol': 'vless', 'transport': 'tcp/raw', 'security': 'reality',
                'flow': 'xtls-rprx-vision', 'port': 443, 'fingerprint': state['fingerprint'],
                'sni': state['sni'], 'mux': False, 'image': self.settings['node_image'],
                'profile_uuid': state['profile_uuid'], 'inbound_uuid': state['inbound_uuid'],
                'target': state.get('reality_target'),
                'owned_https': bool(self.cover),
                'uniform_profiles_enforced': bool(self.settings.get('enforce_uniform_profiles'))}

    def start(self):
        # Exactly one uvicorn worker. flock also prevents a second process doing installations.
        self.worker_lock = (self.root / 'worker.lock').open('a')
        fcntl.flock(self.worker_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.store.db() as db:
            db.execute("UPDATE jobs SET state='queued' WHERE state='running'")
        self.thread = threading.Thread(target=self.worker, daemon=True, name='node-installer')
        self.thread.start()
        self.vpn_config.start()
        self.network.start()
        self.ru_installer.start()
        self.reviews.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        # A running remote step is recovered idempotently after systemd terminates this process.

    def worker(self):
        while not self.stop.is_set():
            try:
                job = self.store.claim()
            except Exception as exc:
                logging.error('Node queue temporarily unavailable: %s', type(exc).__name__)
                self.stop.wait(5)
                continue
            if job:
                try:
                    self.execute(job)
                except Exception:
                    logging.error('Node job %s failed during recovery bookkeeping', job)
                    try:
                        self.store.finish(job, 'recovery_bookkeeping_failed')
                    except Exception:
                        self.stop.wait(5)
            else:
                self.stop.wait(1)

    def preflight(self, request, job='preflight'):
        data = request.model_dump(exclude={'credentials'})
        self.check_ip(data['ip'])
        ssh = self.ssh_factory(self.root, data, request.credentials.reveal())
        try:
            result = json.loads(ssh.python(PREFLIGHT, {'job_id': job}))
            return {**result, 'host_key_sha256': ssh.fingerprint}
        finally:
            ssh.close()

    def check_ip(self, address):
        if address in self.settings['protected_ips']:
            raise ValueError('protected_server_address')

    def submit(self, request, idem):
        with self.lock:
            if self.ru_installer.active():raise SafeError('ru_installation_busy')
            if self.vpn_config.busy():
                raise SafeError('vpn_operation_busy')
            return self._submit(request, idem)

    def _submit(self, request, idem):
        self.check_ip(request.ip)
        # An already completed idempotent request must still return its original job.
        with self.store.db() as db:
            old = db.execute('SELECT id FROM jobs WHERE idem=?', (idem,)).fetchone()
        if not old and any(n['public_ip'] == request.ip for n in self.store.nodes()):
            raise ValueError('server_is_already_managed')
        return self.store.create_job(request, idem)

    def install_key(self, ssh, request, job):
        ssh.python(INSTALL_KEY, {'core_public_ip': self.settings['core_public_ip'],
                               'public_key': (self.root / 'id_ed25519.pub').read_text()})
        verified = self.ssh_factory(self.root, request)
        try:
            if verified.run('id -u').strip() != '0':
                raise SafeError('managed_ssh_key_not_root')
        finally:
            verified.close()
        self.store.forget_credentials(job)

    def core_peer(self, request, context, public_key):
        if not re.fullmatch(r'[A-Za-z0-9+/]{43}=', public_key):
            raise SafeError('invalid_wireguard_public_key')
        # Changes to our own table only. Do not restart WireGuard or the existing nodes.
        with (self.root / 'network.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            conf = Path('/etc/wireguard/wg-control.conf')
            old = conf.read_text()
            allowed = context['management_ip'] + '/32'
            peers = subprocess.check_output(['wg', 'show', 'wg-control', 'allowed-ips'], text=True)
            for line in peers.splitlines():
                peer, *addresses = line.split()
                if peer == public_key and addresses != [allowed]:
                    raise SafeError('wireguard_peer_key_collision')
            block = f'\n[Peer]\nPublicKey = {public_key}\nAllowedIPs = {allowed}\nEndpoint = {request["ip"]}:51820\nPersistentKeepalive = 25\n'
            if public_key not in old:
                if allowed in old:
                    raise SafeError('wireguard_address_collision')
                conf.write_text(old.rstrip() + '\n' + block)
            subprocess.run(['wg', 'set', 'wg-control', 'peer', public_key, 'allowed-ips', allowed,
                            'endpoint', request['ip'] + ':51820', 'persistent-keepalive', '25'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(['ip', 'route', 'replace', allowed, 'dev', 'wg-control'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            firewall = Path('/etc/kingline-firewall.nft')
            text = firewall.read_text()
            pattern = r'ip saddr \{ ([0-9., ]+) \} udp dport 51820 accept'
            match = re.search(pattern, text)
            if not match:
                raise SafeError('core_firewall_layout_changed')
            sources = {ip.strip() for ip in match[1].split(',')}
            sources.add(request['ip'])
            replacement = 'ip saddr { ' + ', '.join(sorted(sources)) + ' } udp dport 51820 accept'
            updated = re.sub(pattern, replacement, text, count=1)
            if updated != text:
                backup = self.root / ('firewall-before-' + request['ip'] + '.nft')
                if not backup.exists():
                    backup.write_text(text)
                firewall.write_text(updated)
                try:
                    subprocess.run(['/usr/local/sbin/kingline-firewall'], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except BaseException:
                    firewall.write_text(text)
                    raise

    def remove_core_peer(self, node):
        job_id = node.get('job_id')
        if not job_id:
            return  # Legacy DNS replicas still require their management tunnel.
        job = self.store.get(job_id)
        public_key = job['context'].get('node_wg_public_key')
        if not public_key or not re.fullmatch(r'[A-Za-z0-9+/]{43}=', public_key):
            raise SafeError('node_wireguard_key_missing')
        allowed = node['management_ip'] + '/32'
        block = (f'\n[Peer]\nPublicKey = {public_key}\nAllowedIPs = {allowed}\n'
                 f'Endpoint = {node["public_ip"]}:51820\nPersistentKeepalive = 25\n')
        with (self.root / 'network.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            conf = Path('/etc/wireguard/wg-control.conf')
            old = conf.read_text()
            if block in old:
                conf.write_text(old.replace(block, '\n', 1))
            elif public_key in old or allowed in old:
                raise SafeError('wireguard_peer_layout_changed')
            subprocess.run(['wg', 'set', 'wg-control', 'peer', public_key, 'remove'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if subprocess.check_output(['ip', 'route', 'show', allowed], text=True).strip():
                subprocess.run(['ip', 'route', 'del', allowed, 'dev', 'wg-control'], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            firewall = Path('/etc/kingline-firewall.nft')
            text = firewall.read_text()
            pattern = r'ip saddr \{ ([0-9., ]+) \} udp dport 51820 accept'
            match = re.search(pattern, text)
            if not match:
                raise SafeError('core_firewall_layout_changed')
            sources = {ip.strip() for ip in match[1].split(',')}
            if node['public_ip'] in sources:
                sources.remove(node['public_ip'])
                if not sources:
                    raise SafeError('core_firewall_last_peer')
                updated = re.sub(pattern, 'ip saddr { ' + ', '.join(sorted(sources)) +
                                 ' } udp dport 51820 accept', text, count=1)
                firewall.write_text(updated)
                try:
                    subprocess.run(['/usr/local/sbin/kingline-firewall'], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except BaseException:
                    firewall.write_text(text)
                    raise

    def execute(self, job):
        ssh = None
        host_uuid = None
        try:
            row = self.store.get(job)
            request, context = row['request'], row['context']
            if context.get('candidate_prepared') or self.onboarding.data(job).get('action'):
                self.onboarding.execute(job)
                return
            state = self.standard()
            self.store.phase(job, 'ssh_preflight')
            credentials = json.loads(self.store.cipher.decrypt(row['secret'])) if row['secret'] else {}
            ssh = self.ssh_factory(self.root, request, credentials)
            credentials.clear()
            check = json.loads(ssh.python(PREFLIGHT, {'job_id': job}))
            if not check.get('ok'):
                raise SafeError(check.get('error', 'preflight_failed'))
            self.store.phase(job, 'managed_key', host_key_sha256=ssh.fingerprint, ubuntu=check['ubuntu'])
            fingerprint = ssh.fingerprint
            self.install_key(ssh, request, job)
            ssh.run('install -d -m 700 /opt/kingline-node')
            ssh.upload(Path(__file__).with_name('remote_install.py'), '/opt/kingline-node/installer.py')
            self.store.phase(job, 'packages')
            payload = {**request, 'job_id': job}
            base = json.loads(ssh.run('python3 /opt/kingline-node/installer.py base', json.dumps(payload), timeout=2400))
            self.store.phase(job, 'management_network', node_wg_public_key=base['public_key'])
            self.core_peer(request, context, base['public_key'])
            payload.update({'management_ip': context['management_ip'],
                            'core_public_ip': self.settings['core_public_ip'],
                            'core_wg_public_key': Path('/etc/wireguard/control.pub').read_text().strip(),
                            'image': self.settings['node_image'],
                            'node_secret': self.api.call('keygen')['secretKey']})
            self.store.phase(job, 'container')
            configured = json.loads(ssh.run('python3 /opt/kingline-node/installer.py configure',
                                           json.dumps(payload), timeout=900))
            payload.pop('node_secret', None)
            if configured['reboot_required']:
                self.store.phase(job, 'rebooting')
                # Only a new, not yet published node is rebooted.
                boot = ssh.run('cat /proc/sys/kernel/random/boot_id').strip()
                ssh.run('systemd-run --unit=kingline-onboarding-reboot --on-active=5s /sbin/reboot')
                ssh.close()
                ssh = None
                for _ in range(90):
                    if self.stop.wait(5):
                        raise SafeError('worker_stopping')
                    try:
                        check_ssh = self.ssh_factory(self.root, request)
                        changed = check_ssh.run('cat /proc/sys/kernel/random/boot_id').strip() != boot
                        check_ssh.close()
                        if changed:
                            break
                    except (SafeError, OSError, paramiko.SSHException):
                        # A reset SSH channel is expected while the new host reboots.
                        pass
                else:
                    raise SafeError('reboot_timeout')
            covered = None
            if self.cover:
                self.store.phase(job, 'https_target')
                if ssh is None:
                    ssh = self.ssh_factory(self.root, request)
                with certificate_lock(self.root):
                    covered = self.cover.deploy(ssh)
                self.store.phase(job, 'https_verified', cover=covered)
            self.store.phase(job, 'candidate_profile')
            binding = self.onboarding.profile(job, state)
            self.store.phase(job, 'panel_registration')
            nodes = self.api.items('nodes', 'nodes')
            found = next((n for n in nodes if n['address'] == context['management_ip']), None)
            if found and found['name'] != request['name']:
                raise SafeError('panel_node_collision')
            body = {'name': request['name'], 'address': context['management_ip'], 'port': 2222,
                    'countryCode': request['country'], 'isTrafficTrackingActive': True,
                    'trafficLimitBytes': round(request['quota_tb'] * 10**12), 'trafficResetDay': request['reset_day'],
                    'notifyPercent': 80, 'configProfile': binding}
            node = found or self.api.call('nodes', 'POST', body)
            self.store.phase(job, 'waiting_for_node', node_uuid=node['uuid'])
            if found:
                self.api.call('nodes', 'PATCH', {'uuid': node['uuid'], 'configProfile': binding})
            self.wait_node(node['uuid'])
            self.store.phase(job, 'verifying_tunnel')
            from .ru_egress import RUEgress
            try:
                RUEgress(self.root,self.control,self.ssh_factory).provision({'uuid':node['uuid'],'public_ip':request['ip'],'management_ip':context['management_ip'],'ssh_port':request['ssh_port'],'host_key_sha256':fingerprint,'ru_enabled':request.get('ru_enabled',True)})
            except Exception as error:
                # The native RU balancer retains direct Internet fallback;
                # the maintenance timer retries the private peer independently.
                from .diagnostics import event
                event('ru_peer_setup_pending','WARNING',node_uuid=node['uuid'],error=type(error).__name__)
            candidate = {'uuid': node['uuid'], 'name': request['name'], 'label': request['label'],
                'public_ip': request['ip'], 'management_ip': context['management_ip'],
                'ssh_port': request['ssh_port'], 'host_key_sha256': fingerprint,
                'ru_enabled': request.get('ru_enabled', True), 'job_id': job,
                **({'cover': covered} if covered else {})}
            candidate_data = self.onboarding.data(job)
            candidate_data.update(candidate=candidate, state=state)
            self.onboarding.save(job, candidate_data)
            check = self.verify_new_node(request['ip'])
            self.store.phase(job, 'verifying_tunnel', verification=check)
            self.onboarding.prepared(job, candidate, state)
        except Exception as error:
            # Never expose an unverified new node in smart DNS. Keep packages/WG for safe retry.
            saved = self.store.get(job)
            host_uuid = host_uuid or saved['context'].get('host_uuid')
            if host_uuid:
                try:
                    self.api.call('hosts', 'PATCH', {'uuid': host_uuid, 'isDisabled': True})
                except Exception:
                    pass
            with self.lock:
                node_uuid = saved['context'].get('node_uuid')
                if node_uuid:
                    for node in self.store.nodes():
                        if node['uuid'] == node_uuid:
                            node['provisioned'] = False
                            self.store.save_node(node)
                self.export_inventory()
            self.store.finish(job, str(error) if isinstance(error, SafeError) else 'operation_failed_' + type(error).__name__)
        finally:
            if ssh:
                ssh.close()

    def wait_node(self, node_uuid):
        for _ in range(90):
            current = self.api.call('nodes/' + node_uuid)
            if current['isConnected'] and (current.get('xrayUptime') or 0) > 0:
                return
            if self.stop.wait(2): raise SafeError('worker_stopping')
        raise SafeError('panel_connection_timeout')

    def verify_new_node(self, address):
        check = self.probe(self.control, address)
        if self.settings.get('xhttp'):
            check['xhttp'] = self.probe(self.control, address, transport='xhttp',
                xhttp_path=self.settings['xhttp']['path'], exit_urls=('https://api.ipify.org', 'https://icanhazip.com'))
        return check

    def publish(self, job, candidate, state, check):
        row = self.store.get(job)
        request, context, node = row['request'], row['context'], candidate
        self.store.phase(job, 'publishing', verification=check)
        with self.lock:
            hosts = self.api.items('hosts', 'hosts')
            address = domains.published_address(self, node, request['ip'])
            if domains.enabled(self):
                domains.sync(self.control, [*self.store.nodes(), {'uuid': node['uuid'],
                    'public_ip': request['ip']}], self.network.state())
            host = next((h for h in hosts if h['address'] in (address, request['ip']) and
                         h['inbound']['configProfileUuid'] == state['profile_uuid']), None)
            if host is not None:
                attached = [n if isinstance(n, str) else n['uuid'] for n in host['nodes']]
                if attached != [node['uuid']] or host['remark'] != request['label']:
                    raise SafeError('existing_host_collision')
                if host['address'] != address:
                    self.api.call('hosts', 'PATCH', {'uuid': host['uuid'], 'address': address})
            if host is None:
                host = self.api.call('hosts', 'POST', {
                    'inbound': {'configProfileUuid': state['profile_uuid'], 'configProfileInboundUuid': state['inbound_uuid']},
                    'remark': request['label'], 'address': address, 'port': 443,
                    'fingerprint': state['fingerprint'], 'sni': state['sni'],
                    'nodes': [node['uuid']], 'isDisabled': True})
            host_uuid = host['uuid']
            self.store.phase(job, 'publishing', host_uuid=host_uuid)
            smart = next(h for h in hosts if h['address'] == 'auto.edge.kingline.online')
            smart_nodes = [n if isinstance(n, str) else n['uuid'] for n in smart['nodes']]
            if node['uuid'] not in smart_nodes:
                self.api.call('hosts', 'PATCH', {'uuid': smart['uuid'], 'nodes': smart_nodes + [node['uuid']]})
            self.api.call('hosts', 'PATCH', {'uuid': host_uuid, 'isDisabled': False})
            self.store.save_node({'name': request['name'], 'label': request['label'], 'uuid': node['uuid'],
                'display_name': request.get('display_name'), 'flag_code': request.get('flag_code', request['country']),
                'public_ip': request['ip'], 'management_ip': context['management_ip'],
                'quota_bytes': round(request['quota_tb'] * 10**12), 'reset_day': request['reset_day'],
                'speed_mbps': request['speed_mbps'], 'maintenance': False, 'provisioned': True,
                'smart_enabled': request.get('smart_enabled', True),
                'ru_enabled': request.get('ru_enabled', True),
                'host_uuid': host_uuid, 'country': request['country'], 'ssh_port': request['ssh_port'],
                'managed_ssh': True,
                'host_key_sha256': candidate['host_key_sha256'],
                'verification': check, 'job_id': job, **({'cover': candidate['cover']} if candidate.get('cover') else {})})
            self.network.on_node_added(host_uuid)
            self.export_inventory()
            if domains.enabled(self):
                domains.sync(self.control, self.store.nodes(), self.network.state())
            if self.cover:
                sync_cover_dns(self.control, self.store.nodes())
            self.store.phase(job, 'ready')
            self.store.finish(job)

    def export_inventory(self):
        atomic_json(self.control / 'nodes.json', self.network.enrich(self.store.nodes()))
        self.network.export()

    def capacity(self, node_uuid, patch):
        with self.lock, certificate_lock(self.root):
            if patch.ru_enabled is not None and self.ru_installer.active():raise SafeError('ru_installation_busy')
            node = next((n for n in self.store.nodes() if n['uuid'] == node_uuid), None)
            if node is None:
                raise KeyError(node_uuid)
            before = dict(node)
            old_panel = self.api.call('nodes/' + node_uuid)
            body = {'uuid': node_uuid}
            if patch.quota_tb is not None:
                node['quota_bytes'] = body['trafficLimitBytes'] = round(patch.quota_tb * 10**12)
            if patch.reset_day is not None:
                node['reset_day'] = body['trafficResetDay'] = patch.reset_day
            if patch.speed_mbps is not None:
                node['speed_mbps'] = patch.speed_mbps
            if patch.maintenance is not None:
                node['maintenance'] = patch.maintenance
            if patch.smart_enabled is not None:
                node['smart_enabled'] = patch.smart_enabled
            if patch.ru_enabled is not None:
                node['ru_enabled'] = patch.ru_enabled
            try:
                if len(body) > 1:
                    self.api.call('nodes', 'PATCH', body)
                self.store.save_node(node)
                if patch.ru_enabled is not None:
                    from .ru_egress import RUEgress
                    RUEgress(self.root,self.control,self.ssh_factory).provision(node)
                self.export_inventory()
            except Exception:
                self.store.save_node(before)
                if patch.ru_enabled is not None:
                    try:RUEgress(self.root,self.control,self.ssh_factory).provision(before)
                    except Exception:
                        from .diagnostics import event
                        event('ru_preference_recovery_failed','ERROR',node_uuid=node_uuid)
                if len(body) > 1:
                    self.api.call('nodes', 'PATCH', {k: old_panel[k] for k in body})
                self.export_inventory()
                raise
            return node

    def remove(self, node_uuid, confirmation):
        """Retire one VPN location, leaving its operating system and backups intact."""
        with self.lock, certificate_lock(self.root):
            if self.ru_installer.active():raise SafeError('ru_installation_busy')
            nodes = self.store.nodes()
            node = next((n for n in nodes if n['uuid'] == node_uuid), None)
            if node is None:
                old = self.store.removal(node_uuid)
                if old and old['state'] == 'removed':
                    if old['name'] != confirmation.name or old['ip'] != confirmation.ip:
                        raise SafeError('node_confirmation_mismatch')
                    self.export_inventory()
                    return {'removed': True, 'uuid': node_uuid, 'name': old['name'],
                            'ip': old['ip'], 'server_retained': True}
                raise KeyError(node_uuid)
            if node['name'] != confirmation.name or node['public_ip'] != confirmation.ip:
                raise SafeError('node_confirmation_mismatch')
            self.network.removal_guard(node_uuid)
            if not (node.get('job_id') or node.get('managed_ssh')):
                raise SafeError('node_not_managed_by_backend')
            if not any(n['uuid'] != node_uuid and n.get('provisioned', True) and not n.get('maintenance', False)
                       for n in nodes):
                raise SafeError('cannot_remove_last_node')
            with self.store.db() as db:
                if db.execute("SELECT 1 FROM jobs WHERE ip=? AND state IN ('queued','running')",
                              (node['public_ip'],)).fetchone():
                    raise SafeError('node_installation_in_progress')
            request = {'ip': node['public_ip'], 'ssh_port': node.get('ssh_port', 22),
                       'host_key_sha256': node.get('host_key_sha256')}
            phase = 'ssh_connecting'
            self.store.removal_phase(node, phase)
            ssh = None
            try:
                ssh = self.ssh_factory(self.root, request)
                if ssh.run('id -u').strip() != '0':
                    raise SafeError('node_ssh_not_root')
                hosts = self.api.items('hosts', 'hosts')
                smart = next(h for h in hosts if h['address'] == 'auto.edge.kingline.online')
                smart_nodes = [n if isinstance(n, str) else n['uuid'] for n in smart['nodes']]
                manual = next((h for h in hosts if h['uuid'] == node.get('host_uuid')), None)
                if manual:
                    attached = [n if isinstance(n, str) else n['uuid'] for n in manual['nodes']]
                    if attached != [node_uuid]:
                        raise SafeError('manual_host_node_mismatch')
                phase = 'withdrawing'
                self.store.removal_phase(node, phase)
                node['maintenance'] = True
                node['provisioned'] = False
                self.store.save_node(node)
                self.export_inventory()
                if domains.enabled(self):
                    domains.sync(self.control, self.store.nodes(), self.network.state())
                if self.cover:
                    sync_cover_dns(self.control, self.store.nodes())
                if node_uuid in smart_nodes:
                    self.api.call('hosts', 'PATCH', {'uuid': smart['uuid'],
                                                   'nodes': [n for n in smart_nodes if n != node_uuid]})
                if manual and not manual['isDisabled']:
                    self.api.call('hosts', 'PATCH', {'uuid': manual['uuid'], 'isDisabled': True})
                # Revoke the retired location's private RU peer before removing
                # its SSH management channel. The Raspberry's old 3x-ui is unrelated.
                from .ru_egress import RUEgress
                RUEgress(self.root,self.control,self.ssh_factory).remove(node)
                phase = 'stopping_container'
                self.store.removal_phase(node, phase)
                ssh.run('if test -f /opt/remnanode/docker-compose.yml; then cd /opt/remnanode && '
                        'docker compose down; fi', timeout=120)
                if node.get('manual_connection'):
                    ssh.run(REMOVE_MOBILE_FIREWALL, timeout=30)
                if node.get('cover'):
                    phase = 'removing_https_target'
                    self.store.removal_phase(node, phase)
                    ssh.run(REMOVE_COVER, timeout=120)
                if node.get('job_id'):
                    phase = 'removing_management_network'
                    self.store.removal_phase(node, phase)
                    ssh.run('systemctl disable --now wg-quick@wg-control', timeout=60)
                    self.remove_core_peer(node)
                phase = 'unregistering'
                self.store.removal_phase(node, phase)
                if manual:
                    self.api.call('hosts/' + manual['uuid'], 'DELETE')
                if any(n['uuid'] == node_uuid for n in self.api.items('nodes', 'nodes')):
                    self.api.call('nodes/' + node_uuid, 'DELETE')
                phase = 'cleaning_configuration'
                self.store.removal_phase(node, phase)
                ssh.run('rm -f /opt/remnanode/.env /opt/remnanode/docker-compose.yml')
                self.store.delete_node(node)
                self.export_inventory()
                with self.store.db() as db:
                    observations = [dict(r) for r in db.execute(
                        'SELECT observations.*,probes.region,probes.required FROM observations '
                        'JOIN probes ON probes.id=observations.probe')]
                atomic_json(self.control / 'regional-probes.json', {'observations': observations})
                return {'removed': True, 'uuid': node_uuid, 'name': node['name'],
                        'ip': node['public_ip'], 'server_retained': True}
            except Exception as error:
                completed = self.store.removal(node_uuid)
                if not completed or completed['state'] != 'removed':
                    self.store.removal_phase(node, phase, state='failed',
                                             error=str(error) if isinstance(error, SafeError) else 'removal_failed')
                raise
            finally:
                if ssh:
                    ssh.close()
