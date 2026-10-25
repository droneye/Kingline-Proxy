#!/usr/bin/env python3
"""Recover only isolated website/bot containers, never the production VPN."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
ROOT=Path('/opt/kingline-staging')
import sys
sys.path.insert(0, str(ROOT/'bot'))
import diagnostics as diag
os.environ['KINGLINE_DIAGNOSTICS_PATH']=str(ROOT/'logs/operations.jsonl')
diag.configure('watchdog')

for service in ('api','bot'):
    name='kingline-staging-'+service+'-1'
    try:
        d=json.loads(subprocess.check_output(['docker','inspect',name],timeout=10))[0]
        age=time.time()-__import__('datetime').datetime.fromisoformat(d['State']['StartedAt'].replace('Z','+00:00')).timestamp()
        bad=not d['State']['Running'] or d['State'].get('Health',{}).get('Status')=='unhealthy'
        if service=='bot' and age>300:
            heartbeat=ROOT/'run/bot-heartbeat'
            bad=bad or not heartbeat.exists() or time.time()-heartbeat.stat().st_mtime>180
        if bad and age>120:
            subprocess.run(['docker','restart','--time','20',name],check=True,timeout=35,stdout=subprocess.DEVNULL)
            diag.event('watchdog_recovered','WARNING',operation=service);print('Recovered '+service)
    except Exception as e:diag.event('watchdog_failed','ERROR',operation=service,error=type(e).__name__);print(service+': '+type(e).__name__)
