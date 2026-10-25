import base64
import hashlib
import io
import json
from pathlib import Path
import shlex
import time

import paramiko


class SafeError(RuntimeError):
    """Only stable codes, never remote output or credentials, reach the API/log."""


class TrustPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if self.expected and fingerprint != self.expected:
            raise SafeError("ssh_host_key_mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(client._host_keys_filename)


class SSH:
    def __init__(self, root, request, credentials=None):
        self.root, self.request = Path(root), request
        self.client = paramiko.SSHClient()
        known = self.root / "known_hosts"
        known.touch(mode=0o600, exist_ok=True)
        self.client.load_host_keys(str(known))
        expected = request.get('host_key_sha256')
        hostname = request['ip'] if request['ssh_port'] == 22 else f'[{request["ip"]}]:{request["ssh_port"]}'
        existing = self.client.get_host_keys().lookup(hostname)
        if expected and existing:
            matches = {kind: key for kind, key in existing.items() if 'SHA256:' +
                       base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=') == expected}
            if not matches:
                raise SafeError('ssh_host_key_mismatch')
            self.client.get_host_keys()[hostname] = matches
        self.client.set_missing_host_key_policy(TrustPolicy(request.get("host_key_sha256")))
        credentials = credentials or {}
        kwargs = dict(hostname=request["ip"], port=request["ssh_port"], username="root",
                      timeout=15, banner_timeout=15, auth_timeout=20, look_for_keys=False, allow_agent=False)
        if credentials.get("private_key"):
            for kind in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
                try:
                    kwargs["pkey"] = kind.from_private_key(io.StringIO(credentials["private_key"]),
                                                          password=credentials.get("passphrase"))
                    break
                except (paramiko.SSHException, ValueError):
                    continue
            if "pkey" not in kwargs:
                raise SafeError("invalid_private_key_or_passphrase")
        elif credentials.get("password"):
            kwargs["password"] = credentials["password"]
        else:
            kwargs["key_filename"] = str(self.root / "id_ed25519")
        try:
            self.client.connect(**kwargs)
            key = self.client.get_transport().get_remote_server_key()
            self.fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
            if request.get("host_key_sha256") and self.fingerprint != request["host_key_sha256"]:
                raise SafeError("ssh_host_key_mismatch")
            self.client.get_transport().set_keepalive(15)
        except SafeError:
            self.client.close()
            raise
        except paramiko.BadHostKeyException:
            self.client.close()
            raise SafeError("ssh_host_key_changed") from None
        except paramiko.AuthenticationException:
            self.client.close()
            raise SafeError("ssh_authentication_failed") from None
        except Exception:
            self.client.close()
            raise SafeError("ssh_connection_failed") from None

    def close(self):
        self.client.close()

    def run(self, command, payload=None, timeout=60):
        channel = self.client.get_transport().open_session(timeout=15)
        channel.settimeout(15)
        channel.exec_command(command)
        if payload is not None:
            channel.sendall(payload.encode() if isinstance(payload, str) else payload)
        channel.shutdown_write()
        output = bytearray()
        deadline = time.monotonic() + timeout
        while True:
            if channel.recv_ready():
                chunk = channel.recv(65536)
                if len(output) < 1024 * 1024:
                    output.extend(chunk)
            if channel.recv_stderr_ready():
                channel.recv_stderr(65536)  # Remote diagnostics can contain secrets. Never publish them.
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            if time.monotonic() > deadline:
                channel.close()
                raise SafeError("remote_operation_timeout")
            time.sleep(0.03)
        code = channel.recv_exit_status()
        channel.close()
        if code:
            raise SafeError("remote_operation_failed")
        return output.decode(errors="replace")

    def python(self, source, data=None, timeout=60):
        return self.run("python3 -c " + shlex.quote(source), json.dumps(data or {}), timeout)

    def upload(self, source, destination):
        with self.client.open_sftp() as sftp:
            with sftp.open(destination, "w") as output:
                output.write(Path(source).read_bytes())
            sftp.chmod(destination, 0o600)


PREFLIGHT = r'''
import json,os,pathlib,platform,shutil,socket,subprocess
p=json.load(__import__('sys').stdin)
def fail(code):
 print(json.dumps({'ok':False,'error':code})); raise SystemExit(0)
if os.geteuid()!=0: fail('root_required')
if not pathlib.Path('/run/systemd/system').exists(): fail('systemd_required')
release=dict(line.split('=',1) for line in pathlib.Path('/etc/os-release').read_text().splitlines() if '=' in line)
release={k:v.strip('"') for k,v in release.items()}
if release.get('ID')!='ubuntu' or release.get('VERSION_ID') not in ['22.04','24.04','26.04']:
 fail('ubuntu_lts_22_24_26_required')
if platform.machine()!='x86_64': fail('amd64_required_for_pinned_image')
marker=pathlib.Path('/opt/kingline-node/identity.json')
owned=marker.exists() and json.loads(marker.read_text()).get('job_id')==p['job_id']
if marker.exists() and not owned: fail('server_owned_by_another_job')
if not owned:
 for f in ['/opt/remnanode/docker-compose.yml','/usr/local/x-ui','/etc/x-ui','/etc/wireguard/wg-control.conf','/etc/kingline-firewall.nft']:
  if pathlib.Path(f).exists(): fail('existing_service_requires_manual_migration')
 if shutil.which('docker'):
  r=subprocess.run(['docker','ps','-aq'],capture_output=True,text=True)
  if r.returncode==0 and r.stdout.strip(): fail('nonempty_docker_host')
 if shutil.which('nft'):
  r=subprocess.run(['nft','list','ruleset'],capture_output=True,text=True)
  if r.returncode==0 and ('hook input' in r.stdout or 'hook forward' in r.stdout): fail('existing_firewall_requires_manual_migration')
 for port in [80,443,2222,8443,51820]:
  s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM if port==51820 else socket.SOCK_STREAM)
  try: s.bind(('0.0.0.0',port))
  except OSError: fail('required_port_busy')
  finally: s.close()
if shutil.disk_usage('/').free < 3*1024**3: fail('at_least_3gib_free_required')
memory=int(next(x.split()[1] for x in pathlib.Path('/proc/meminfo').read_text().splitlines() if x.startswith('MemTotal:')))
if memory<750000: fail('at_least_768mib_ram_required')
print(json.dumps({'ok':True,'ubuntu':release['VERSION_ID'],'architecture':platform.machine(),'owned':owned}))
'''

INSTALL_KEY = r'''
import json,os,pathlib
p=json.load(__import__('sys').stdin)
root=pathlib.Path('/root/.ssh'); root.mkdir(mode=0o700,exist_ok=True)
file=root/'authorized_keys'
key='from="'+p['core_public_ip']+',10.77.0.1",no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty '+p['public_key'].strip()
old=file.read_text() if file.exists() else ''
if p['public_key'].split()[1] not in old:
 with file.open('a') as output: output.write(('\n' if old and not old.endswith('\n') else '')+key+'\n')
file.chmod(0o600)
'''
