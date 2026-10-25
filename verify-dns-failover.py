#!/usr/bin/env python3
"""Run on Mac. Controlled node outage; always bring back the node and controller."""
import collections
import datetime
import json
from pathlib import Path
import subprocess
import time

def ssh(host, command):
    return subprocess.check_output(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                                    'root@' + host, command], timeout=90).decode().strip()

def sample(count):
    code = """python3 - <<'PY'
import collections,json,subprocess,time
result={}
for ip in ['198.51.100.10','198.51.100.31','198.51.100.32']:
 values=[]
 for _ in range(COUNT):
  query=subprocess.run(['dig','@'+ip,'auto.edge.kingline.online','A','+short','+time=2','+tries=2'],capture_output=True,text=True)
  values.append(query.stdout.strip() if query.returncode==0 else 'TIMEOUT')
  time.sleep(0.03)
 result[ip]=dict(collections.Counter(values))
print(json.dumps(result))
PY""".replace('COUNT', str(count))
    return json.loads(ssh('198.51.100.10', code))

report = {'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
report['healthy_distribution'] = sample(60)
if any('' in counts for counts in report['healthy_distribution'].values()):
    report['initial_warmup_distribution'] = report.pop('healthy_distribution')
    time.sleep(15)
    report['healthy_distribution'] = sample(60)
assert all(set(counts) == {'198.51.100.31', '198.51.100.32'} for counts in report['healthy_distribution'].values()), 'Baseline DNS is not healthy'
print('Healthy DNS distribution:', json.dumps(report['healthy_distribution']), flush=True)
try:
    ssh('198.51.100.10', 'systemctl stop kingline-smart-dns')
    ssh('198.51.100.31', 'docker stop remnanode')
    print('Germany node paused; quota controller paused; checking independent DNS failover', flush=True)
    stopped = ssh('198.51.100.31', "docker inspect --format '{{.State.Running}}' remnanode")
    assert stopped == 'false', 'Node did not stop'
    time.sleep(45)
    report['germany_down_controller_stopped'] = sample(10)
    Path('verification-dns-failover.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Failover answers:', json.dumps(report['germany_down_controller_stopped']), flush=True)
    assert all(set(counts) == {'198.51.100.32'}
               for counts in report['germany_down_controller_stopped'].values()), 'DNS did not fail over'
    print('All three DNS replicas route to Denmark while the controller is stopped', flush=True)
finally:
    ssh('198.51.100.31', 'docker start remnanode')
    ssh('198.51.100.10', 'systemctl start kingline-smart-dns')
    print('Germany node and quota controller restarted', flush=True)
time.sleep(20)
report['recovered_distribution'] = sample(60)
assert all(set(counts)=={'198.51.100.31','198.51.100.32'} for counts in report['recovered_distribution'].values())
Path('verification-dns-failover.json').write_text(json.dumps(report, indent=2) + '\n')
