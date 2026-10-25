"""Meaningful failure-window tests against disposable staging SQLite files."""
import asyncio
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

temporary = tempfile.TemporaryDirectory()
os.environ['DB_PATH'] = temporary.name + '/business.db'
os.environ['KINGLINE_SUBSCRIPTION_LOCK_DIR'] = temporary.name
os.environ['REFERRAL_DB_PATH'] = temporary.name + '/ref.db'
os.environ['PROMOS_DB_PATH'] = temporary.name + '/promos.db'
from config import db
from handlers.billing_db import BillingDB
from handlers.durable_subscription import activate_subscription, request_key, SubscriptionPending, retry_panel_sync, schema

class DurableProvisioningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tg = -900001
        BillingDB.ensure_user_row(self.tg, 'Failure-window test', 'failure_test')
        with closing(db._connect()) as c, c:
            schema(c)
            c.execute('DELETE FROM subscription_operations')
            c.execute('DELETE FROM subscription_delivery_outbox') if c.execute("SELECT 1 FROM sqlite_master WHERE name='subscription_delivery_outbox'").fetchone() else None
            c.execute('UPDATE users SET balance_rub=600,free_used=0,sub_id=NULL,tariff=NULL,key_expires_at=NULL,subscription_expires_at=NULL WHERE tg_id=?', (self.tg,))
        self.key = request_key.set('test-request-1')

    def tearDown(self): request_key.reset(self.key)

    async def test_timeout_after_panel_acceptance_replays_exact_expiry_and_one_charge(self):
        calls = []
        async def panel(sub_id, username, expires, **kwargs):
            calls.append((sub_id, expires))
            if len(calls) == 1: raise TimeoutError('Panel may already have accepted the user')
            return ['00000000-0000-4000-8000-000000000001'], expires
        with patch.object(BillingDB, 'create_clients_for_user', panel):
            with self.assertRaises(SubscriptionPending):
                await activate_subscription(tg_id=self.tg,tg_name='Test',username='test',kind='premium')
            self.assertEqual(db.get_user(self.tg)['balance_rub'], 300)
            with closing(db._connect()) as c, c:
                c.execute('UPDATE subscription_operations SET next_attempt_at=0')
            await retry_panel_sync()
            result = await activate_subscription(tg_id=self.tg,tg_name='Test',username='test',kind='premium')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(db.get_user(self.tg)['balance_rub'], 300)
        self.assertEqual(result['user']['tariff'], 'premium')

    async def test_local_commit_failure_reconciles_without_free_access_or_second_charge(self):
        calls = []
        async def panel(sub_id, username, expires, **kwargs):
            calls.append((sub_id, expires));return ['00000000-0000-4000-8000-000000000002'], expires
        original = db.upsert_after_create
        with patch.object(BillingDB, 'create_clients_for_user', panel):
            with patch.object(db,'upsert_after_create',side_effect=RuntimeError('simulated SQLite failure')):
                with self.assertRaises(SubscriptionPending):
                    await activate_subscription(tg_id=self.tg,tg_name='Test',username='test',kind='premium')
            self.assertEqual(db.get_user(self.tg)['balance_rub'], 300)
            result = await activate_subscription(tg_id=self.tg,tg_name='Test',username='test',kind='premium')
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(db.get_user(self.tg)['balance_rub'], 300)

    async def test_insufficient_balance_never_creates_an_intent_or_calls_panel(self):
        BillingDB.change_balance(self.tg, -600)
        with patch.object(BillingDB,'create_clients_for_user') as provision:
            with self.assertRaisesRegex(ValueError,'insufficient_balance'):
                await activate_subscription(tg_id=self.tg,tg_name='Test',username='test',kind='premium')
            provision.assert_not_called()
        with closing(db._connect()) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM subscription_operations').fetchone()[0],0)

class ExpiredImportTests(unittest.TestCase):
    def test_expired_copy_is_disabled_without_sending_a_forbidden_past_patch(self):
        import hashlib
        from remnawave import Remnawave
        from unittest.mock import Mock
        api=object.__new__(Remnawave)
        api.squad='staging-squad'
        name='stg_'+hashlib.sha256(b'expired-fixture').hexdigest()[:20]
        user={'id':999,'username':name,'tag':'STAGING','shortUuid':'expired-test',
            'vlessUuid':'00000000-0000-4000-8000-000000000003','status':'EXPIRED'}
        api.find=Mock(return_value=user)
        api.call=Mock(return_value=user)
        api.ensure('expired-fixture','test',1000,enabled=True)
        payload=api.call.call_args.args[2]
        self.assertEqual(payload['status'],'DISABLED')
        self.assertNotIn('expireAt',payload)

if __name__ == '__main__': unittest.main()
