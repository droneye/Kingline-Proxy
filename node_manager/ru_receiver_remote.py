"""Bootstrap a replacement RU receiver. Called over SSH; payload never logged."""
import json,os,platform,re,shutil,socket,subprocess,sys
from pathlib import Path
os.umask(0o077)
def run(*args,**kwargs):
    subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kwargs)
def write(path,text,mode=0o600):
    path=Path(path);path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.new');temporary.write_text(text);temporary.chmod(mode);temporary.replace(path)
def preflight(p):
    if os.geteuid()!=0:raise RuntimeError('root_required')
    release=dict(line.split('=',1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    release={k:v.strip('"') for k,v in release.items()}
    supported={'ubuntu':{'22.04','24.04','26.04'},'debian':{'12','13'}}
    if release.get('VERSION_ID') not in supported.get(release.get('ID'),set()):raise RuntimeError('ru_supported_linux_required')
    if platform.machine() not in {'x86_64','aarch64'}:raise RuntimeError('ru_architecture_not_supported')
    if not Path('/run/systemd/system').exists():raise RuntimeError('systemd_required')
    marker=Path('/opt/kingline-ru-installer/owner.json')
    owned=marker.exists() and json.loads(marker.read_text()).get('core')==p['core_ip']
    if not owned and not p.get('allow_legacy'):
        if any(Path(f).exists() for f in ['/etc/x-ui','/usr/local/x-ui','/opt/remnanode','/etc/wireguard/wg-control.conf']):raise RuntimeError('existing_service_requires_manual_migration')
        if shutil.which('docker') and subprocess.check_output(['docker','ps','-aq'],text=True).strip():raise RuntimeError('nonempty_docker_host')
    if shutil.disk_usage('/').free<3*1024**3:raise RuntimeError('at_least_3gib_free_required')
    return {'ok':True,'os':release['ID'],'version':release['VERSION_ID'],'architecture':platform.machine()}
def packages(p):
    preflight(p)
    write('/opt/kingline-ru-installer/owner.json',json.dumps({'core':p['core_ip']}))
    env={**os.environ,'DEBIAN_FRONTEND':'noninteractive','NEEDRESTART_MODE':'a'}
    needed=[name for name,command in [('wireguard-tools','wg'),('nftables','nft'),('curl','curl'),('ca-certificates',None)] if command is None or not shutil.which(command)]
    docker_ready=bool(shutil.which('docker') and subprocess.run(['docker','compose','version'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0)
    if needed or not docker_ready:
        run('apt-get','-o','DPkg::Lock::Timeout=180','update',env=env,timeout=300)
        run('apt-get','-y','-o','DPkg::Lock::Timeout=180','install',*needed,'gnupg',env=env,timeout=600)
    if not docker_ready:
        release=dict(line.split('=',1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
        osid=release['ID'].strip('"');codename=release['VERSION_CODENAME'].strip('"')
        assert osid in {'ubuntu','debian'} and re.fullmatch('[a-z]+',codename)
        arch=subprocess.check_output(['dpkg','--print-architecture'],text=True).strip();assert arch in {'amd64','arm64'}
        run('install','-m','0755','-d','/etc/apt/keyrings')
        run('curl','-fsSL','--max-time','30','https://download.docker.com/linux/'+osid+'/gpg','-o','/etc/apt/keyrings/docker.asc')
        fingerprint=subprocess.check_output(['gpg','--show-keys','--with-colons','/etc/apt/keyrings/docker.asc'],stderr=subprocess.DEVNULL).decode()
        assert '9DC858229FC7DD38854AE2D88D81803C0EBFCD88' in fingerprint,'docker_signing_key_changed'
        Path('/etc/apt/keyrings/docker.asc').chmod(0o644)
        write('/etc/apt/sources.list.d/kingline-docker.sources',f'Types: deb\nURIs: https://download.docker.com/linux/{osid}\nSuites: {codename}\nComponents: stable\nArchitectures: {arch}\nSigned-By: /etc/apt/keyrings/docker.asc\n',0o644)
        run('apt-get','-o','DPkg::Lock::Timeout=180','update',env=env,timeout=300)
        run('apt-get','-y','-o','DPkg::Lock::Timeout=180','install','docker-ce','docker-ce-cli','containerd.io','docker-compose-plugin',env=env,timeout=600)
    run('systemctl','enable','--now','docker')
    return {'ready':True}
def install_key(p):
    key=p['ssh_public_key'].strip();assert len(key.split())>=2 and key.split()[0]=='ssh-ed25519'
    file=Path('/root/.ssh/authorized_keys');file.parent.mkdir(mode=0o700,exist_ok=True)
    line='from="'+p['core_ip']+',10.79.0.1",no-agent-forwarding,no-port-forwarding,no-X11-forwarding '+key
    old=file.read_text() if file.exists() else ''
    if key.split()[1] not in old:write(file,old+('' if old.endswith('\n') or not old else '\n')+line+'\n')
    return {'installed':True}
def control(p):
    generation=int(p['generation']);assert 1<=generation<=254
    interface='wg-ruc'+str(generation);key=Path('/etc/wireguard/ru-management'+str(generation)+'.key')
    if not key.exists():write(key,subprocess.check_output(['wg','genkey']).decode())
    public=subprocess.check_output(['wg','pubkey'],input=key.read_bytes()).decode().strip()
    if p.get('core_public_key'):
        private=key.read_text().strip()
        write('/etc/wireguard/'+interface+'.conf',f'[Interface]\nAddress = 10.79.{generation}.2/32\nListenPort = {53000+generation}\nMTU = 1380\nPrivateKey = {private}\n\n[Peer]\nPublicKey = {p["core_public_key"]}\nAllowedIPs = 10.79.0.1/32\nEndpoint = {p["core_ip"]}:51821\nPersistentKeepalive = 25\n')
        run('systemctl','enable','--now','wg-quick@'+interface)
    return {'public_key':public,'management_ip':f'10.79.{generation}.2'}
def retire(p):
    generation=int(p.get('generation',0));assert 0<=generation<=254
    suffix='-'+str(generation) if generation else ''
    subprocess.run(['systemctl','disable','--now','kingline-ru-node'+suffix],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    return {'retired':True,'old_xui_untouched':True}
if __name__=='__main__':
    payload=json.load(sys.stdin)
    try:
        action={'preflight':preflight,'packages':packages,'install_key':install_key,'control':control,'retire':retire}[payload['mode']]
        print(json.dumps(action(payload)))
    except Exception as error:
        # Error values from package tools or a remote SSH server can contain
        # credentials. Return stable codes only; never print a traceback.
        allowed={'root_required','ru_supported_linux_required','ru_architecture_not_supported','systemd_required','existing_service_requires_manual_migration','nonempty_docker_host','at_least_3gib_free_required'}
        code=str(error) if str(error) in allowed else 'ru_remote_'+type(error).__name__
        print(json.dumps({'ok':False,'error':code}));raise SystemExit(0)
