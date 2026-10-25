"""Probe the actually issued staging user through existing standard node configs."""
import copy
import json
from pathlib import Path
import socket
import sqlite3
import sys

sys.path.insert(0,'/opt/kingline-manager/app')
from node_manager.probe import verify_tunnel
control = Path('/opt/kingline-control')
state = json.loads((control/'owner-profile.json').read_text())
with sqlite3.connect('/opt/kingline-staging/data/vpn_bot.db') as c:
    row = c.execute('SELECT vless_uuid FROM remna_links WHERE sub_id=(SELECT sub_id FROM users WHERE tg_id=-900000001)').fetchone()
assert row and row[0] != state['user']['vlessUuid']
state = copy.deepcopy(state)
state['user']['vlessUuid'] = row[0]
report = {}
for name, address, connect in [('Germany','198.51.100.31',None),('Denmark','198.51.100.32',None),
        ('Smart',socket.gethostbyname('auto.edge.kingline.online'),'auto.edge.kingline.online')]:
    if name == 'Smart': assert address in ['198.51.100.31','198.51.100.32']
    try:
        report[name] = verify_tunnel(control,address,state_override=state,connect_address=connect,
            exit_urls=('https://api.ipify.org','https://checkip.amazonaws.com'),dns_resolvers=('9.9.9.9','1.1.1.1'))
    except Exception as e:
        report[name] = {'ok':False,'error':str(e)}
Path('/opt/kingline-staging/verification-tunnels.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
