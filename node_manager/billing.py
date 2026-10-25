"""Owner payment reminders. No provider payments or changes to VPN availability."""
import calendar
from contextlib import closing
from datetime import datetime, timedelta, timezone
import html
import json
import math
import re
import secrets
import sqlite3
import time

from . import diagnostics as diag
from .ssh import SafeError

MOSCOW=timezone(timedelta(hours=3))
DAY=86400
REPEAT=6*3600
LEASE=300


def next_due(after,day):
    local=datetime.fromtimestamp(after,MOSCOW)
    year,month=local.year,local.month
    def at(y,m):return datetime(y,m,min(day,calendar.monthrange(y,m)[1]),tzinfo=MOSCOW).timestamp()
    value=at(year,month)
    if value<=after:
        year,month=(year+1,1) if month==12 else (year,month+1)
        value=at(year,month)
    return value


class BillingReminders:
    def __init__(self,manager,clock=None):
        self.m=manager;self.store=manager.store;self.clock=clock or time.time
        with self.store.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS billing_meta(key TEXT PRIMARY KEY,value REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS node_bills(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,node TEXT NOT NULL,due REAL NOT NULL,
                    reset_day INTEGER NOT NULL,label TEXT NOT NULL,ip TEXT NOT NULL,
                    cost REAL NOT NULL,currency TEXT NOT NULL,state TEXT NOT NULL,
                    next_alert REAL NOT NULL,paid_at REAL,created REAL NOT NULL,updated REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_node_bill ON node_bills(node) WHERE state='pending';
                CREATE TABLE IF NOT EXISTS billing_outbox(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,bill INTEGER NOT NULL,token TEXT UNIQUE NOT NULL,
                    state TEXT NOT NULL,lease REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,
                    created REAL NOT NULL,message_id INTEGER);
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_billing_notice ON billing_outbox(bill) WHERE state='pending';
            ''')
            db.execute("INSERT OR IGNORE INTO billing_meta VALUES('started_at',?)",(self.clock(),))

    def owner(self,actor):
        self.m.reviews.owner(actor)

    def eligible(self):
        path=self.m.root/'site-admin.sqlite3'
        if not path.exists():return {}
        with closing(sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=5)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inventory'").fetchone():return {}
            metadata={r[0]:(json.loads(r[1]),r[2]) for r in db.execute('SELECT ip,metadata,updated FROM inventory')}
        result={}
        for node in self.store.nodes():
            if not node.get('provisioned',True):continue
            value,updated=metadata.get(node['public_ip'],({},0))
            if value.get('payment_reminders_enabled',True) is not True:continue
            cost=value.get('monthly_cost');day=node.get('reset_day');currency=value.get('currency','EUR')
            if type(cost) not in (int,float) or not math.isfinite(cost) or not 0<cost<=10000000:continue
            if type(day) is not int or not 1<=day<=31 or not re.fullmatch(r'[A-Z]{3}',str(currency)):continue
            result[node['uuid']]={'node':node['uuid'],'day':day,'cost':cost,'currency':currency,
                'label':str(node.get('label') or node.get('display_name') or node['name'])[:80],
                'ip':node['public_ip'],'updated':updated}
        return result

    @staticmethod
    def retire(db,identifier,state,now):
        db.execute('UPDATE node_bills SET state=?,updated=? WHERE id=?',(state,now,identifier))
        db.execute("UPDATE billing_outbox SET state='cancelled' WHERE bill=? AND state='pending'",(identifier,))

    def sync(self):
        current=self.eligible();now=self.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            started=db.execute("SELECT value FROM billing_meta WHERE key='started_at'").fetchone()[0]
            pending={r['node']:dict(r) for r in db.execute("SELECT * FROM node_bills WHERE state='pending'")}
            for node,bill in pending.items():
                value=current.get(node)
                if not value or value['day']!=bill['reset_day']:
                    self.retire(db,bill['id'],'cancelled',now)
            for node,value in current.items():
                bill=pending.get(node)
                if bill and bill['reset_day']!=value['day']:bill=None
                if bill:
                    due=bill['due']
                else:
                    paid=db.execute("SELECT MAX(due) FROM node_bills WHERE node=? AND state='paid'",(node,)).fetchone()[0] or 0
                    base=max(started,min(now,value['updated']),paid)
                    # A changed reset day starts from its next occurrence, rather
                    # than creating an old overdue bill at a newly edited date.
                    if node in pending:base=max(base,now)
                    due=next_due(base,value['day'])
                upcoming=next_due(due,value['day'])
                while now>=upcoming-DAY:
                    due=upcoming;upcoming=next_due(due,value['day'])
                if bill and due!=bill['due']:
                    self.retire(db,bill['id'],'superseded',now);bill=None
                if bill:
                    db.execute('UPDATE node_bills SET label=?,ip=?,cost=?,currency=?,updated=? WHERE id=?',
                        (value['label'],value['ip'],value['cost'],value['currency'],now,bill['id']))
                else:
                    db.execute("""INSERT INTO node_bills(node,due,reset_day,label,ip,cost,currency,state,next_alert,created,updated)
                        VALUES(?,?,?,?,?,?,?,'pending',?,?,?)""",
                        (node,due,value['day'],value['label'],value['ip'],value['cost'],value['currency'],due-DAY,now,now))
            # Keep a bounded billing history and stale-button protection.
            db.execute("DELETE FROM billing_outbox WHERE bill IN (SELECT id FROM node_bills WHERE state!='pending' AND updated<?)",(now-400*DAY,))
            db.execute("DELETE FROM node_bills WHERE state!='pending' AND updated<?",(now-400*DAY,))
            db.execute("DELETE FROM billing_outbox WHERE state!='pending' AND created<?",(now-90*DAY,))

    @staticmethod
    def text(bill):
        due=datetime.fromtimestamp(bill['due'],MOSCOW).strftime('%d.%m.%Y')
        cost=f"{bill['cost']:g} {bill['currency']}"
        return ('💳 <b>Оплатите сервер</b>\n\n'+html.escape(bill['label'])+' · '+html.escape(bill['ip'])+
            '\nСтоимость: <b>'+html.escape(cost)+'/месяц</b>\nДата оплаты и сброса: <b>'+due+
            '</b>\n\nПосле оплаты нажмите «Оплачено». До подтверждения напомню через 6 часов.')

    def claim(self,actor):
        self.owner(actor);self.sync();now=self.clock();items=[]
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT OR REPLACE INTO billing_meta VALUES('last_poll_at',?)",(now,))
            bills=db.execute("SELECT * FROM node_bills WHERE state='pending' AND next_alert<=? ORDER BY next_alert LIMIT 20",(now,)).fetchall()
            for bill in bills:
                row=db.execute("SELECT * FROM billing_outbox WHERE bill=? AND state='pending'",(bill['id'],)).fetchone()
                if not row:
                    token=secrets.token_urlsafe(18)
                    cursor=db.execute("INSERT INTO billing_outbox(bill,token,state,created) VALUES(?,?,'pending',?)",(bill['id'],token,now))
                    row=db.execute('SELECT * FROM billing_outbox WHERE id=?',(cursor.lastrowid,)).fetchone()
                if row['lease']>now:continue
                db.execute('UPDATE billing_outbox SET lease=?,attempts=attempts+1 WHERE id=?',(now+LEASE,row['id']))
                items.append({'id':row['id'],'chat_id':actor,'text':self.text(bill),
                    'keyboard':[[{'text':'✅ Оплачено','callback_data':'kb:'+row['token']}]]})
                if len(items)>=4:break
        return items

    def ack(self,actor,identifier,message_id):
        self.owner(actor);now=self.clock()
        if type(identifier) is not int or type(message_id) is not int or min(identifier,message_id)<=0:raise SafeError('billing_invalid_notice')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM billing_outbox WHERE id=?',(identifier,)).fetchone()
            if not row:raise SafeError('billing_invalid_notice')
            if row['state']=='pending':
                db.execute("UPDATE billing_outbox SET state='sent',message_id=? WHERE id=?",(message_id,identifier))
                db.execute("UPDATE node_bills SET next_alert=?,updated=? WHERE id=? AND state='pending'",(now+REPEAT,now,row['bill']))
        return {'ok':True}

    def callback(self,actor,data):
        self.owner(actor)
        if not re.fullmatch(r'kb:[A-Za-z0-9_-]{24}',data):raise SafeError('billing_button_expired')
        self.sync();now=self.clock()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT b.* FROM node_bills b JOIN billing_outbox o ON o.bill=b.id WHERE o.token=?',(data[3:],)).fetchone()
            if not row:raise SafeError('billing_button_expired')
            if row['state'] not in ('pending','paid'):
                return {'ok':True,'message':'Это напоминание уже неактуально. Настройки сервера или расчётный месяц изменились.'}
            if row['state']=='pending':
                db.execute("UPDATE node_bills SET state='paid',paid_at=?,updated=? WHERE id=?",(now,now,row['id']))
                db.execute("UPDATE billing_outbox SET state='cancelled' WHERE bill=? AND state='pending'",(row['id'],))
                diag.event('node_payment_confirmed',node_uuid=row['node'],bill_id=row['id'])
            due=datetime.fromtimestamp(row['due'],MOSCOW).strftime('%d.%m.%Y')
            return {'ok':True,'message':'✅ <b>Оплата отмечена</b>\n'+html.escape(row['label'])+' · '+due+
                '\nНапоминания за этот период остановлены. Следующий месяц — по расписанию.'}
