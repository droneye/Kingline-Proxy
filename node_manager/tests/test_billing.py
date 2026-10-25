from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from node_manager.billing import BillingReminders, MOSCOW, DAY, REPEAT, LEASE, next_due
from node_manager.models import Credentials
from node_manager.review_bot_socket import create_app as bot_app
from node_manager.site_admin import Vault, Metadata
from node_manager.ssh import SafeError
from node_manager.store import Store

NODE='11111111-1111-4111-a111-111111111111'
def at(value):return datetime.fromisoformat(value).replace(tzinfo=MOSCOW).timestamp()


@pytest.fixture
def billing(tmp_path):
    root=tmp_path/'data';store=Store(root)
    store.save_node({'uuid':NODE,'public_ip':'203.0.113.10','name':'DE-test','label':'Германия <test>','reset_day':19,'provisioned':True})
    def owner(actor):
        if actor!=42:raise SafeError('review_owner_only')
    m=SimpleNamespace(root=root,store=store,reviews=SimpleNamespace(owner=owner))
    clock=[at('2026-10-17T12:00:00')]
    obj=BillingReminders(m,clock=lambda:clock[0]);obj.test_clock=clock
    obj.vault=Vault(root);obj.vault.save('203.0.113.10',Metadata(monthly_cost=4.9),Credentials(password='secret-canary'))
    return obj


def test_calendar_short_month_leap_year_year_boundary_and_exact_deadline():
    assert next_due(at('2026-01-31T00:00:00'),31)==at('2026-02-28T00:00:00')
    assert next_due(at('2028-01-31T00:00:00'),31)==at('2028-02-29T00:00:00')
    assert next_due(at('2026-12-31T00:00:00'),1)==at('2027-01-01T00:00:00')
    assert next_due(at('2026-10-18T23:59:59'),19)==at('2026-10-19T00:00:00')


def test_default_enabled_and_no_notice_without_positive_monthly_cost(billing):
    b=billing;b.test_clock[0]=at('2026-10-18T00:00:00')
    for cost in (None,0):
        b.vault.save('203.0.113.10',Metadata(monthly_cost=cost))
        assert b.claim(42)==[]
    b.vault.save('203.0.113.10',Metadata(monthly_cost=4.9))
    assert b.vault.metadata('203.0.113.10')['payment_reminders_enabled'] is True
    assert len(b.claim(42))==1
    assert b.vault.reveal('203.0.113.10')['password']=='secret-canary'


def test_old_metadata_defaults_enabled_without_migrating_or_revealing_secrets(billing):
    b=billing
    with b.vault.db() as db:db.execute('UPDATE inventory SET metadata=?',(json.dumps({'monthly_cost':4.9,'currency':'EUR'}),))
    assert b.vault.metadata('203.0.113.10')['payment_reminders_enabled'] is True
    b.test_clock[0]=at('2026-10-18T00:00:00')
    assert 'secret-canary' not in json.dumps(b.claim(42))


def test_first_notice_is_24h_before_due_then_every_six_hours_until_paid(billing):
    b=billing;due=at('2026-10-19T00:00:00')
    assert b.claim(42)==[]
    b.test_clock[0]=due-DAY-1;assert b.claim(42)==[]
    b.test_clock[0]=due-DAY;notice=b.claim(42)[0]
    assert '19.10.2026' in notice['text'] and '4.9 EUR' in notice['text']
    assert '&lt;test&gt;' in notice['text'] and 'secret-canary' not in notice['text']
    b.ack(42,notice['id'],101)
    b.test_clock[0]+=REPEAT-1;assert b.claim(42)==[]
    b.test_clock[0]+=1;second=b.claim(42)[0];b.ack(42,second['id'],102)
    b.test_clock[0]+=REPEAT;third=b.claim(42)[0]
    token=notice['keyboard'][0][0]['callback_data']
    result=b.callback(42,token)
    assert 'Оплата отмечена' in result['message']
    assert b.callback(42,token)==result
    b.ack(42,third['id'],103)
    b.test_clock[0]+=REPEAT*8;assert b.claim(42)==[]
    b.test_clock[0]=at('2026-11-18T00:00:00');new=b.claim(42)[0]
    assert '19.11.2026' in new['text']
    assert b.callback(42,token)==result
    with b.store.db() as db:assert db.execute("SELECT count(*) FROM node_bills WHERE state='pending'").fetchone()[0]==1


def test_disable_or_cost_removal_cancels_queued_reminders_and_old_buttons(billing):
    b=billing;b.test_clock[0]=at('2026-10-18T00:00:00');notice=b.claim(42)[0]
    b.vault.save('203.0.113.10',Metadata(monthly_cost=4.9,payment_reminders_enabled=False))
    assert b.claim(42)==[]
    assert 'неактуально' in b.callback(42,notice['keyboard'][0][0]['callback_data'])['message']
    b.ack(42,notice['id'],11)
    b.test_clock[0]+=2*REPEAT;assert b.claim(42)==[]
    b.vault.save('203.0.113.10',Metadata(monthly_cost=None,payment_reminders_enabled=True))
    assert b.claim(42)==[]


def test_hidden_or_maintenance_nodes_still_cost_money_but_removed_nodes_do_not(billing):
    b=billing;node=b.store.nodes()[0];node.update(hidden=True,maintenance=True);b.store.save_node(node)
    b.test_clock[0]=at('2026-10-18T00:00:00');notice=b.claim(42)[0]
    with b.store.db() as db:db.execute('DELETE FROM nodes WHERE uuid=?',(NODE,))
    assert b.claim(42)==[]
    assert 'неактуально' in b.callback(42,notice['keyboard'][0][0]['callback_data'])['message']


def test_lease_retry_restart_and_ack_are_durable_and_idempotent(billing):
    b=billing;b.test_clock[0]=at('2026-10-18T00:00:00');notice=b.claim(42)[0]
    restarted=BillingReminders(b.m,clock=b.clock)
    b.test_clock[0]+=LEASE-1;assert restarted.claim(42)==[]
    b.test_clock[0]+=1;retried=restarted.claim(42)[0]
    assert retried['id']==notice['id'] and retried['keyboard']==notice['keyboard']
    restarted.ack(42,notice['id'],9)
    b.test_clock[0]+=20;restarted.ack(42,notice['id'],9)
    with b.store.db() as db:due=db.execute("SELECT next_alert FROM node_bills WHERE state='pending'").fetchone()[0]
    assert due==b.test_clock[0]-20+REPEAT
    assert restarted.claim(42)==[]


def test_two_pollers_cannot_claim_same_notice(billing):
    b=billing;b.test_clock[0]=at('2026-10-18T00:00:00')
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:b.claim(42),range(2)))
    assert sum(len(value) for value in results)==1


def test_date_edit_invalidates_old_button_and_moves_schedule(billing):
    b=billing;b.test_clock[0]=at('2026-10-18T00:00:00');notice=b.claim(42)[0]
    node=b.store.nodes()[0];node['reset_day']=20;b.store.save_node(node)
    assert 'неактуально' in b.callback(42,notice['keyboard'][0][0]['callback_data'])['message']
    assert b.claim(42)==[]
    b.test_clock[0]=at('2026-10-19T00:00:00');assert '20.10.2026' in b.claim(42)[0]['text']


def test_new_month_does_not_accumulate_multiple_overdue_notices(billing):
    b=billing;b.test_clock[0]=at('2026-10-18T00:00:00');old=b.claim(42)[0];b.ack(42,old['id'],5)
    b.test_clock[0]=at('2026-11-18T00:00:00');items=b.claim(42)
    assert len(items)==1 and '19.11.2026' in items[0]['text']
    assert 'неактуально' in b.callback(42,old['keyboard'][0][0]['callback_data'])['message']


def test_other_actors_and_unrecognised_callbacks_cannot_confirm_payments(billing):
    b=billing;b.test_clock[0]=at('2026-10-18T00:00:00');notice=b.claim(42)[0]
    for action in (lambda:b.claim(43),lambda:b.ack(43,notice['id'],5),lambda:b.callback(43,notice['keyboard'][0][0]['callback_data'])):
        with pytest.raises(SafeError,match='owner_only'):action()
    for data in ('kb:wrong','kb:'+'z'*24,'kr:'+'a'*24):
        with pytest.raises(SafeError,match='billing_button_expired'):b.callback(42,data)


def test_bot_bridge_limits_payment_actions_and_rejects_secrets():
    class Client:
        calls=[]
        def call(self,*args):self.calls.append(args);return {'ok':True,'items':[]}
    backend=Client();client=TestClient(bot_app(backend))
    assert client.post('/billing/claim',json={'actor_id':42}).status_code==200
    assert backend.calls[-1][1]=='/v1/node-billing/bot/claim'
    assert client.post('/billing/callback',json={'actor_id':42,'data':'kb:'+'a'*24}).status_code==200
    assert client.post('/billing/callback',json={'actor_id':42,'data':'kr:'+'a'*24}).status_code==422
    response=client.post('/billing/ack',json={'actor_id':42,'id':1,'message_id':2,'password':'secret-canary'})
    assert response.status_code==422 and 'secret-canary' not in response.text
    assert client.post('/billing/nodes/delete',json={'actor_id':42}).status_code==404
