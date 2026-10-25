"""Read-only release checks; no fake bills, claims for owner, or Telegram sends."""
from contextlib import closing
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

root=Path('/opt/kingline-manager/data');control=Path('/opt/kingline-control')
report={}
class Unix(http.client.HTTPConnection):
    def __init__(self,path):super().__init__('localhost',timeout=15);self.path=path
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout);self.sock.connect(self.path)

def uds(path,route,body=None):
    c=Unix(path)
    try:
        c.request('POST' if body is not None else 'GET',route,body=json.dumps(body) if body is not None else None,
            headers={'Content-Type':'application/json'} if body is not None else {})
        response=c.getresponse();return response.status,json.loads(response.read())
    finally:c.close()

status,data=uds('/run/kingline-site-admin/bridge.sock','/nodes')
report['node_inventory_http']=status
report['reminder_flag_available']=all(type(n.get('admin',{}).get('payment_reminders_enabled')) is bool for n in data.get('nodes',[]))
report['billable_nodes']=sum(bool(n.get('admin',{}).get('monthly_cost')) and n.get('admin',{}).get('payment_reminders_enabled',True) for n in data.get('nodes',[]))
owner=json.loads((control/'ru-monitor.json').read_text())['owner_id']
status,_=uds('/run/kingline-node-review/bot.sock','/billing/claim',{'actor_id':owner+1})
report['wrong_owner_http']=status
with closing(sqlite3.connect('file:'+str(root/'manager.sqlite3')+'?mode=ro',uri=True)) as db:
    row=db.execute("SELECT value FROM billing_meta WHERE key='last_poll_at'").fetchone()
    report['scheduler_poll_fresh']=bool(row and 0<=time.time()-row[0]<120)
    report['scheduled_bills']=db.execute("SELECT count(*) FROM node_bills WHERE state='pending'").fetchone()[0]
    report['sent_payment_notices']=db.execute("SELECT count(*) FROM billing_outbox WHERE state='sent'").fetchone()[0]
report['manager_services_active']=all(subprocess.run(['systemctl','is-active','--quiet',name]).returncode==0 for name in
    ('kingline-node-manager','kingline-site-admin','kingline-node-review-bot'))
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
base='https://test.edge.kingline.online'
with opener.open(base+'/api/health',timeout=15) as response:report['site_health_http']=response.status
with opener.open(base+'/',timeout=15) as response:html=response.read().decode()
asset=re.search(r'src="(/assets/[^\"]+\.js)"',html).group(1)
with opener.open(base+asset,timeout=15) as response:bundle=response.read().decode()
report['reminder_ui_served']='Напоминать об оплате' in bundle and 'payment_reminders_enabled' in bundle
try:
    with opener.open(base+'/api/admin/documentation',timeout=15) as response:report['anonymous_documentation_http']=response.status
except urllib.error.HTTPError as error:report['anonymous_documentation_http']=error.code
source=hashlib.sha256(Path('/opt/kingline-staging/docs/service.md').read_bytes()).hexdigest()
image=subprocess.check_output(['docker','compose','exec','-T','api','python','-c',
    'from pathlib import Path;import hashlib;print(hashlib.sha256(Path("/app/docs/service.md").read_bytes()).hexdigest())'],cwd='/opt/kingline-staging').decode().strip()
report['documentation_matches_image']=source==image
(control/'node-billing-verification.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report))
assert report['node_inventory_http']==report['site_health_http']==200
assert report['wrong_owner_http']==409 and report['anonymous_documentation_http']==401
assert all(report[k] for k in ('reminder_flag_available','scheduler_poll_fresh','manager_services_active','reminder_ui_served','documentation_matches_image'))
