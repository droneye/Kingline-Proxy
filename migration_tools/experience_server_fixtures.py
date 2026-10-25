"""Run on the NEW host only. Temporary synthetic data in the isolated owner account."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT=Path('/opt/kingline-staging')
CONTAINER=Path('/app/bot').exists()
STATE=Path('/run/kingline/experience-fixture-state.json') if CONTAINER else ROOT/'run/experience-fixture-state.json'

def db():return sqlite3.connect('/data/vpn_bot.db' if CONTAINER else ROOT/'data/vpn_bot.db')

def setup():
    if STATE.exists():print('Fixture already prepared');return
    with closing(db()) as c,c:
        c.row_factory=sqlite3.Row
        account=c.execute("SELECT * FROM web_accounts WHERE email='owner@example.invalid'").fetchone()
        assert account and account['web_tg_id']==-900000001
        user=c.execute('SELECT * FROM users WHERE tg_id=-900000001').fetchone()
        state={'account_id':account['id'],'original':{k:user[k] for k in ('balance_rub','key_expires_at','subscription_expires_at')},'support_ids':[]}
        now=datetime.now(timezone.utc)
        texts=[('user','Здравствуйте! Подскажите, как добавить подписку на iPhone?'),('admin','Здравствуйте! Откройте «Инструкция», выберите iPhone и пройдите три шага. Если приложение уже установлено, начните с шага «Добавить».'),('user','Спасибо! Проверяю отображение длинного сообщения: '+('проверка_переноса_'*18)),('admin','Ссылка остаётся прежней после продления. В приложении достаточно обновить подписку.'),('user','Понятно, всё получилось.'),('admin','Отлично! Если понадобится помощь — напишите сюда, история сохранится.')]
        for i,(direction,text) in enumerate(texts):
            row=c.execute('INSERT INTO web_support_messages(account_id,direction,message_text,attachments_json,sender_name,created_at) VALUES(?,?,?,\'[]\',?,?)',(account['id'],direction,text,'Поддержка' if direction=='admin' else 'Владелец',(now-timedelta(minutes=12-i)).isoformat()))
            state['support_ids'].append(row.lastrowid)
        c.execute('UPDATE users SET balance_rub=300 WHERE tg_id=-900000001')
    STATE.write_text(json.dumps(state));os.chmod(STATE,0o600)
    print('Synthetic chat and 300 sandbox rubles prepared for the isolated owner fixture')

def cleanup():
    state=json.loads(STATE.read_text())
    # Restore ONLY the isolated test user's expiry in its native STAGING account.
    sys.path.insert(0,'/app/bot')
    from remnawave import Remnawave
    with closing(db()) as c:
        c.row_factory=sqlite3.Row;user=dict(c.execute('SELECT * FROM users WHERE tg_id=-900000001').fetchone())
    native=Remnawave();entry=native.find(user['sub_id']);assert entry and entry.get('tag')=='STAGING' and entry['username'].startswith('stg_')
    expiry=state['original']['subscription_expires_at'] or state['original']['key_expires_at']
    epoch=int(datetime.fromisoformat(expiry.replace('Z','+00:00')).timestamp())
    native.ensure(user['sub_id'],user['username'],epoch*1000,enabled=True)
    with closing(db()) as c,c:
        old=state['original'];c.execute('UPDATE users SET balance_rub=?,key_expires_at=?,subscription_expires_at=? WHERE tg_id=-900000001',(old['balance_rub'],old['key_expires_at'],old['subscription_expires_at']))
        c.executemany('DELETE FROM web_support_messages WHERE id=? AND account_id=?',[(i,state['account_id']) for i in state['support_ids']])
    STATE.unlink()
    print('Synthetic conversation removed; only the test fixture balance and expiry restored')

if __name__=='__main__':setup() if sys.argv[1]=='setup' else cleanup()
