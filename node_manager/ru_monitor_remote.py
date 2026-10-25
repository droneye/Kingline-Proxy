"""Closed SSH interface for probe installation, manifest sync and safe report collection."""
import grp,hashlib,json,os,pwd,re,shutil,subprocess,sys
from pathlib import Path
ROOT=Path('/opt/kingline-ru-monitor');USER='kingline-probe'
def run(*args,**kw):return subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**kw)
def write(path,data,mode=0o640,owner=False):
    p=Path(path);temporary=p.with_suffix(p.suffix+'.new');temporary.write_text(data);temporary.chmod(mode)
    account=pwd.getpwnam(USER);os.chown(temporary,account.pw_uid if owner else 0,account.pw_gid);temporary.replace(p)
def install(p):
    try:grp.getgrnam(USER)
    except KeyError:run('groupadd','--system',USER)
    try:pwd.getpwnam(USER)
    except KeyError:run('useradd','--system','--gid',USER,'--no-create-home','--shell','/usr/sbin/nologin',USER)
    account=pwd.getpwnam(USER);ROOT.mkdir(mode=0o750,exist_ok=True);ROOT.chmod(0o750);os.chown(ROOT,0,account.pw_gid)
    for folder in (ROOT/'state',ROOT/'state/requests'):
        folder.mkdir(mode=0o700,exist_ok=True);os.chown(folder,account.pw_uid,account.pw_gid)
    source=p['worker'];assert hashlib.sha256(source.encode()).hexdigest()==p['worker_sha']
    write(ROOT/'worker.py',source,0o640)
    if not (ROOT/'xray').exists():
        container=p['container'];assert re.fullmatch(r'kingline-ru-remnanode(?:-[0-9]+)?',container)
        binary=subprocess.check_output(['docker','exec',container,'sh','-c','command -v xray'],text=True).strip()
        assert binary.startswith('/usr/local/bin/') and re.fullmatch(r'[/A-Za-z0-9_.-]+',binary)
        run('docker','cp','-L',container+':'+binary,str(ROOT/'xray'));(ROOT/'xray').chmod(0o755)
    run(str(ROOT/'xray'),'version',timeout=5)
    unit='''[Unit]
Description=Kingline home Russian network VPN probes
After=network-online.target
[Service]
Type=oneshot
User=kingline-probe
Group=kingline-probe
UMask=0077
ExecStart=/usr/bin/python3 /opt/kingline-ru-monitor/worker.py
TimeoutStartSec=900
MemoryMax=256M
CPUQuota=40%
TasksMax=96
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/kingline-ru-monitor/state
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
CapabilityBoundingSet=
[Install]
WantedBy=multi-user.target
'''
    Path('/etc/systemd/system/kingline-ru-monitor.service').write_text(unit)
    Path('/etc/systemd/system/kingline-ru-monitor.timer').write_text('''[Unit]
Description=Hourly checks from the Russian home network
[Timer]
OnCalendar=hourly
RandomizedDelaySec=60
Persistent=true
Unit=kingline-ru-monitor.service
[Install]
WantedBy=timers.target
''')
    run('systemctl','daemon-reload');run('systemctl','enable','--now','kingline-ru-monitor.timer')
    return {'installed':True,'worker_sha':p['worker_sha']}
def sync(p):
    if not (ROOT/'worker.py').exists():return {'installed':False}
    if p.get('manifest'):
        encoded=json.dumps(p['manifest'],sort_keys=True)
        if not (ROOT/'manifest.json').exists() or (ROOT/'manifest.json').read_text()!=encoded:write(ROOT/'manifest.json',encoded)
    running=subprocess.check_output(['systemctl','show','kingline-ru-monitor.service','-p','ActiveState','--value'],text=True).strip() in {'active','activating','reloading'}
    processed_file=ROOT/'state/processed.json';processed=json.loads(processed_file.read_text()) if processed_file.exists() else {}
    inflight_file=ROOT/'state/inflight.json';inflight=json.loads(inflight_file.read_text()) if running and inflight_file.exists() else {}
    revision=(p.get('manifest') or {}).get('revision')
    for request in p.get('requests',[]):
        assert re.fullmatch(r'[a-f0-9-]{36}',request['id'])
        if processed.get(request['id'],{}).get('revision')==revision:continue
        if inflight.get('manifest_revision')==revision and request['id'] in inflight.get('requests',[]):continue
        file=ROOT/'state/requests'/(request['id']+'.json')
        if not file.exists():write(file,json.dumps(request),0o600,owner=True)
    report_file=ROOT/'state/report.json'
    if not running and (any((ROOT/'state/requests').glob('*.json')) or not report_file.exists()):run('systemctl','start','--no-block','kingline-ru-monitor.service');running=True
    if report_file.exists() and report_file.stat().st_size>256000:raise ValueError('oversized_report')
    report=json.loads(report_file.read_text()) if report_file.exists() else None
    return {'installed':True,'worker_sha':hashlib.sha256((ROOT/'worker.py').read_bytes()).hexdigest(),'running':running,'report':report}
if __name__=='__main__':
    os.umask(0o077);payload=json.load(sys.stdin)
    try:print(json.dumps({'install':install,'sync':sync}[payload['mode']](payload)))
    except Exception as error:print(json.dumps({'ok':False,'error':'probe_remote_'+type(error).__name__}))
