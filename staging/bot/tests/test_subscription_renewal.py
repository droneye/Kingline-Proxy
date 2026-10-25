import asyncio
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import logging
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from db import SubscriptionDB
from telegram.error import NetworkError


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class IsolatedServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = SubscriptionDB(str(Path(folder.name) / "test.sqlite"))
        config = ModuleType("config")
        config.db = self.db
        config.FULL_TARIFF_PRICE = 300
        config.INBOUND_IDS = [4, 5, 6]
        config.XUI_PANELS = []
        config.SUB_BASE = "https://test.example/sub/"
        config.XUI_SUB_SUFFIX = ""
        config.logger = logging.getLogger("renewal-test")
        package = ModuleType("renewal_test")
        package.__path__ = [str(ROOT / "handlers")]
        modules = patch.dict(sys.modules, {"config": config, "renewal_test": package})
        modules.start()
        self.addCleanup(modules.stop)
        load_module("utils", "utils.py")
        self.billing = load_module("renewal_test.billing_db", "handlers/billing_db.py")
        self.delivery = load_module("renewal_test.subscription_delivery", "handlers/subscription_delivery.py")
        self.service = load_module("renewal_test.subscription_service", "handlers/subscription_service.py")
        self.billing.SUBSCRIPTION_LOCK_DIR = folder.name
        self.billing.SUBSCRIPTION_FLOCK_PATH = str(Path(folder.name) / "renewal.flock")
        self.billing.SUBSCRIPTION_MARKER_PATH = str(Path(folder.name) / "renewal.lock")
        self.Billing = self.billing.BillingDB

    def seed(self, days=5, tariff="premium", tg_id=123):
        self.expiry = (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0)
        self.db.upsert_after_create(
            tg_id=tg_id, tg_name="Test", username="original", sub_id="stable-key",
            sub_name="stable-key", key_created_at="2026-01-01T00:00:00Z",
            key_expires_at=self.expiry.isoformat(), client_ids=["id1", "id2", "id3"],
            tariff=tariff, free_used=0,
        )
        self.Billing.change_balance(tg_id, 900)

    async def activate(self, kind="premium", tg_id=123):
        return await self.service.activate_subscription(
            tg_id=tg_id, tg_name="Test", username="renamed", kind=kind,
        )

    @staticmethod
    async def provision(sub_id, username, expiry, **kwargs):
        await asyncio.sleep(0.01)
        return ["id1", "id2", "id3"], expiry


class RenewalTests(IsolatedServiceTest):
    async def test_concurrent_renewals_grant_both_months_and_keep_keys(self):
        self.seed()
        with patch.object(self.Billing, "create_clients_for_user", side_effect=self.provision) as create:
            await asyncio.gather(self.activate(), self.activate())
        user = self.db.get_user(123)
        self.assertEqual(self.service.expiry_datetime(user), self.expiry + timedelta(days=60))
        self.assertEqual(user["balance_rub"], 300)
        self.assertEqual(user["sub_id"], "stable-key")
        self.assertEqual(user["username"], "original")
        self.assertEqual(user["key_expires_at"], user["subscription_expires_at"])
        self.assertEqual(create.await_count, 2)
        with closing(self.db._connect()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM subscription_delivery_outbox").fetchone()[0], 2)

    async def test_telegram_failure_cannot_refund_successful_renewal(self):
        self.seed()
        with patch.object(self.Billing, "create_clients_for_user", side_effect=self.provision):
            result = await self.activate()
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=NetworkError("offline")))
        self.assertFalse(await self.delivery.deliver_receipt(bot, result["receipt_id"]))
        self.assertEqual(self.db.get_user(123)["balance_rub"], 600)
        self.assertEqual(self.service.expiry_datetime(self.db.get_user(123)), self.expiry + timedelta(days=30))
        with closing(self.db._connect()) as conn:
            self.assertEqual(conn.execute("SELECT status FROM subscription_delivery_outbox").fetchone()[0], "pending")

    async def test_provision_failure_refunds_and_does_not_change_dates(self):
        self.seed()
        with patch.object(self.Billing, "create_clients_for_user", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                await self.activate()
        self.assertEqual(self.db.get_user(123)["balance_rub"], 900)
        self.assertEqual(self.service.expiry_datetime(self.db.get_user(123)), self.expiry)

    async def test_outbox_failure_rolls_back_subscription_and_refunds(self):
        self.seed()
        with patch.object(self.Billing, "create_clients_for_user", side_effect=self.provision), patch.object(
            self.service, "queue_receipt", side_effect=RuntimeError("disk full")
        ):
            with self.assertRaises(RuntimeError):
                await self.activate()
        self.assertEqual(self.db.get_user(123)["balance_rub"], 900)
        self.assertEqual(self.service.expiry_datetime(self.db.get_user(123)), self.expiry)

    async def test_last_hour_of_premium_cannot_be_overwritten_by_trial(self):
        self.seed(days=1/24)
        with patch.object(self.Billing, "create_clients_for_user", new_callable=AsyncMock) as create:
            with self.assertRaisesRegex(ValueError, "premium_active"):
                await self.activate("trial")
        create.assert_not_called()

    async def test_referral_keeps_existing_subscription_and_does_not_charge(self):
        self.seed()
        with patch.object(self.Billing, "create_clients_for_user", side_effect=self.provision) as create:
            result = await self.activate("referral")
        self.assertEqual(result["user"]["balance_rub"], 900)
        self.assertEqual(create.call_args.args[:2], ("stable-key", "original"))

    async def test_web_only_user_does_not_queue_telegram_to_negative_id(self):
        self.seed(tg_id=-123)
        with patch.object(self.Billing, "create_clients_for_user", side_effect=self.provision):
            result = await self.activate(tg_id=-123)
        self.assertIsNone(result["receipt_id"])

    async def test_cancelled_provision_refunds(self):
        self.seed()
        with patch.object(self.Billing, "create_clients_for_user", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await self.activate()
        self.assertEqual(self.db.get_user(123)["balance_rub"], 900)


class PanelVerificationTests(IsolatedServiceTest):
    def setUp(self):
        super().setUp()
        self.state = {}
        self.calls = []
        self.bad_readback = None
        self.fail_optional = False
        for tag in ("LT", "NL", "FR"):
            self.state[tag] = [{"id": spec["inbound_id"], "clients": [{
                "id": f'uuid-{spec["uuid_index"]}', "email": f'test #{spec["email_num"]}',
                "subId": "key", "enable": True, "expiryTime": 1,
            }]} for spec in self.Billing._panel_slot_plan(tag)]
        owner = self

        class Panel:
            def __init__(self, root_url, **kwargs):
                self.tag = root_url
                self.reads = 0

            async def __aenter__(self):
                if self.tag == "FR" and owner.fail_optional:
                    raise TimeoutError()
                return self

            async def __aexit__(self, *args):
                pass

            async def list_inbounds(self):
                self.reads += 1
                result = deepcopy(owner.state[self.tag])
                if self.tag == "NL" and self.reads > 1 and owner.bad_readback:
                    owner.bad_readback(result)
                return result

            def _normalize_client_for_update(self, client, **kwargs):
                return dict(client)

            async def update_client(self, inbound_id, client_id, client_obj):
                owner.calls.append((self.tag, client_id))
                inbound = next(ib for ib in owner.state[self.tag] if ib["id"] == inbound_id)
                inbound["clients"] = [client_obj if c["id"] == client_id else c for c in inbound["clients"]]

            async def add_client_append(self, inbound_id, email, flow, sub_id, expiry_time, uuid, total_gb):
                owner.calls.append((self.tag, uuid))
                inbound = next(ib for ib in owner.state[self.tag] if ib["id"] == inbound_id)
                inbound["clients"].append(dict(id=uuid, email=email, subId=sub_id, enable=True, expiryTime=expiry_time))

        for patcher in (
            patch.object(self.billing, "XUIClient", Panel),
            patch.object(self.Billing, "_get_panels", return_value=[
                dict(tag=tag, url=tag, username="", password="") for tag in self.state
            ]),
            patch.object(self.Billing, "_preflight_panel_health", side_effect=lambda panels: set()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def create(self):
        return await self.Billing.create_clients_for_user("key", "test", 2_000_000_000_000)

    async def test_missing_primary_slot_uses_uuid_from_secondary(self):
        self.state["LT"][0]["clients"] = []
        await self.create()
        self.assertEqual(self.state["LT"][0]["clients"][0]["id"], "uuid-0")

    async def test_each_required_slot_must_confirm_expiry_enable_and_owner(self):
        for field, value in [("expiryTime", 1), ("enable", False), ("subId", "other")]:
            with self.subTest(field=field):
                self.bad_readback = lambda rows: rows[0]["clients"][0].update({field: value})
                with self.assertRaisesRegex(RuntimeError, "verification failed"):
                    await self.create()

    async def test_missing_required_readback_rejected(self):
        self.bad_readback = lambda rows: rows.clear()
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            await self.create()

    async def test_conflicting_uuid_rejected_before_any_write(self):
        self.state["NL"][0]["clients"][0]["id"] = "conflict"
        with self.assertRaisesRegex(RuntimeError, "Conflicting shared"):
            await self.create()
        self.assertEqual(self.calls, [])

    async def test_another_subscriptions_email_cannot_be_taken_over(self):
        self.state["LT"][0]["clients"][0]["subId"] = "someone-else"
        with self.assertRaisesRegex(RuntimeError, "ownership conflict"):
            await self.create()
        self.assertEqual(self.calls, [])

    async def test_optional_failure_after_preflight_does_not_block_required_panels(self):
        self.fail_optional = True
        clients, expiry = await self.create()
        self.assertEqual(len(clients), 3)
        self.assertEqual(expiry, 2_000_000_000_000)
