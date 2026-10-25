"""One-time normalization of pre-upgrade technical usernames; business data untouched."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
root=Path('/opt/kingline-staging')
with closing(sqlite3.connect(root/'logs/telemetry.db',timeout=15)) as c:
    with closing(sqlite3.connect(root/'backups/telemetry-before-name-normalization.db')) as backup:c.backup(backup)
    with c:
        c.execute('UPDATE metrics_web_xui_user_samples SET username=lower(username)')
        c.execute('DELETE FROM metrics_web_xui_user_samples WHERE id NOT IN (SELECT MAX(id) FROM metrics_web_xui_user_samples GROUP BY username,sampled_at)')
        for table,keys in [('metrics_web_xui_ip_history',['username','ip']),('metrics_web_xui_access_daily_counts',['username','day']),('metrics_web_xui_user_last_seen',['username'])]:
            cols=[r[1] for r in c.execute('PRAGMA table_info('+table+')')]
            expressions=[]
            for col in cols:
                if col=='username':expr='lower(username)'
                elif col in keys:expr=col
                elif col in ('seen_count','request_count'):expr='SUM('+col+')'
                elif col in ('id','first_seen_at'):expr='MIN('+col+')'
                else:expr='MAX('+col+')'
                expressions.append(expr+' AS '+col)
            group=','.join('lower(username)' if k=='username' else k for k in keys)
            c.execute('CREATE TEMP TABLE normalized AS SELECT '+','.join(expressions)+' FROM '+table+' GROUP BY '+group)
            c.execute('DELETE FROM '+table)
            c.execute('INSERT INTO '+table+' SELECT * FROM normalized')
            c.execute('DROP TABLE normalized')
        c.execute('UPDATE OR IGNORE metrics_web_xui_client_aliases SET username=lower(username)')
        c.execute('DELETE FROM metrics_web_xui_device_profiles')
        c.execute('DELETE FROM metrics_web_xui_user_insights_cache')
    print(json.dumps({'business_database_changed':False,'technical_names_normalized':True,'integrity':c.execute('PRAGMA quick_check').fetchone()[0]}))
