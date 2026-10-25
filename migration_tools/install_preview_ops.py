from pathlib import Path
import subprocess
ROOT=Path('/opt/kingline-staging');PY='/opt/kingline-manager/venv/bin/python'
for name,script,interval in [('telemetry','collect_telemetry.py','30s'),('watchdog','watchdog.py','60s'),('backup','backup.py',None)]:
    service=f'''[Unit]
Description=Kingline website {name}
After=docker.service network-online.target
[Service]
Type=oneshot
ExecStart={PY} {ROOT}/ops/{script}
TimeoutStartSec=90
Nice=10
CPUQuota=40%
MemoryMax=256M
UMask=0077
'''
    timer='[Unit]\nDescription=Kingline website '+name+' schedule\n[Timer]\n'
    timer+=f'OnBootSec=45s\nOnUnitInactiveSec={interval}\nAccuracySec=3s\n' if interval else 'OnCalendar=*-*-* 03:25:00\nPersistent=true\nRandomizedDelaySec=120\n'
    timer+='[Install]\nWantedBy=timers.target\n'
    Path('/etc/systemd/system/kingline-preview-'+name+'.service').write_text(service)
    Path('/etc/systemd/system/kingline-preview-'+name+'.timer').write_text(timer)
subprocess.run(['systemctl','daemon-reload'],check=True)
for name in ('telemetry','watchdog','backup'):
    subprocess.run(['systemctl','enable','--now','kingline-preview-'+name+'.timer'],check=True,capture_output=True)
subprocess.run(['systemctl','start','kingline-preview-backup.service'],check=True)
print('Statistics, recovery and verified daily backups installed.')
