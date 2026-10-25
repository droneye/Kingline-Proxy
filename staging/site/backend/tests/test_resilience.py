import asyncio
import atexit
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, patch
import urllib.error
import urllib.parse


TEST_DATA = tempfile.TemporaryDirectory(prefix="kingline-resilience-")
atexit.register(TEST_DATA.cleanup)
os.environ["KINGLINE_DB_PATH"] = os.path.join(TEST_DATA.name, "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend import server as site
from handlers import billing_db
import sub_proxy
from xui import XUIClient


class XUIResilienceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        XUIClient._unavailable_until.clear()

    async def test_login_timeout_closes_session_and_backs_off_then_recovers(self):
        client = XUIClient("http://panel.test", "test", "test")
        client.timeout = 0.03

        async def slow_request(*args):
            await asyncio.sleep(10)

        with patch.object(client, "_post_json", side_effect=slow_request) as request:
            with self.assertRaises(TimeoutError):
                await client.__aenter__()
            self.assertIsNone(client._session)
            with self.assertRaisesRegex(RuntimeError, "temporarily unavailable"):
                await client.__aenter__()
            self.assertEqual(request.call_count, 1)

        XUIClient._unavailable_until[(client.root, client.webbasepath, client.username)] = 0
        with patch.object(client, "_post_json", return_value=(200, {"success": True})):
            async with client:
                self.assertFalse(client._session.closed)
        self.assertIsNone(client._session)

    async def test_login_cancel_closes_session_without_cooling_down_healthy_host(self):
        client = XUIClient("http://panel.test", "test", "test")
        entered = asyncio.Event()
        sessions = []

        async def slow_login():
            sessions.append(client._session)
            entered.set()
            await asyncio.sleep(10)

        with patch.object(client, "login", side_effect=slow_login):
            task = asyncio.create_task(client.__aenter__())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(sessions[0].closed)
        self.assertFalse(XUIClient._unavailable_until)

    async def test_login_preserves_form_fallback(self):
        client = XUIClient("http://panel.test", "test", "test")
        with patch.object(client, "_post_json", return_value=(415, {})), patch.object(
            client, "_post_form", return_value=(200, {"success": True})
        ) as form:
            async with client:
                self.assertEqual(client._api_base, "http://panel.test/panel")
        form.assert_awaited_once()

    async def test_preflight_is_parallel_and_only_skips_optional_outage(self):
        entered = []
        all_entered = asyncio.Event()

        class PanelClient:
            def __init__(self, root_url, **kwargs):
                self.tag = root_url

            async def __aenter__(self):
                entered.append(self.tag)
                if len(entered) == 3:
                    all_entered.set()
                await all_entered.wait()
                if self.tag == "FR":
                    raise TimeoutError()
                return self

            async def __aexit__(self, *args):
                pass

            async def server_status(self):
                return {}

        panels = [dict(tag=tag, url=tag, username="", password="") for tag in ("LT", "NL", "FR")]
        with patch.object(billing_db, "XUIClient", PanelClient):
            skipped = await asyncio.wait_for(billing_db.BillingDB._preflight_panel_health(panels), 1)
        self.assertEqual(skipped, {"FR"})

    async def test_cancelled_lock_waiter_does_not_remove_owner_marker_or_hold_lock(self):
        with tempfile.TemporaryDirectory() as folder, patch.multiple(
            billing_db,
            SUBSCRIPTION_LOCK_DIR=folder,
            SUBSCRIPTION_FLOCK_PATH=os.path.join(folder, "update.flock"),
            SUBSCRIPTION_MARKER_PATH=os.path.join(folder, "update.lock"),
        ):
            async def waiter():
                async with billing_db.BillingDB.subscription_update_lock("waiter"):
                    self.fail("waiter acquired the owner's lock")

            async with billing_db.BillingDB.subscription_update_lock("owner"):
                task = asyncio.create_task(waiter())
                await asyncio.sleep(0.02)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertIn("owner", Path(billing_db.SUBSCRIPTION_MARKER_PATH).read_text())
            async with asyncio.timeout(1), billing_db.BillingDB.subscription_update_lock("next"):
                pass

    async def test_restart_delay_does_not_hold_subscription_lock(self):
        locked = False

        @asynccontextmanager
        async def lock(reason):
            nonlocal locked
            locked = True
            yield
            locked = False

        async def sleep(delay):
            self.assertFalse(locked)

        async def restart():
            self.assertTrue(locked)

        with patch.object(site.BillingDB, "subscription_update_lock", lock), patch.object(
            site.BillingDB, "restart_xray_safe", side_effect=restart
        ), patch.object(site.asyncio, "sleep", side_effect=sleep):
            await site.delayed_xray_restart("test")

    async def test_online_publishes_healthy_panel_before_slow_panel_completes(self):
        async def fetch(panel):
            await asyncio.sleep(0.01 if panel["tag"] == "LT" else 0.1)
            ok = panel["tag"] == "LT"
            return dict(tag=panel["tag"], host="test", ok=ok, count=int(ok),
                        items=[dict(username="test")] if ok else [])

        with patch.object(site.BillingDB, "_get_panels", return_value=[{"tag":"LT"}, {"tag":"FR"}]), patch.object(
            site, "fetch_xui_panel_online", side_effect=fetch
        ), patch.object(site, "xui_online_name_map", return_value={}):
            task = asyncio.create_task(site.fetch_xui_online_snapshot())
            await asyncio.sleep(0.04)
            self.assertFalse(task.done())
            self.assertEqual(site.XUI_ONLINE_CACHE["data"]["count"], 1)
            self.assertTrue(site.XUI_ONLINE_CACHE["data"]["partial"])
            result = await task
        self.assertEqual(result["count"], 1)
        self.assertFalse(result["loading"])
        self.assertFalse(result["panels"][1]["ok"])

    async def test_online_request_has_total_deadline(self):
        class StalledClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                await asyncio.sleep(10)

            async def __aexit__(self, *args):
                pass

        with patch.object(site, "XUIClient", StalledClient), patch.object(site, "XUI_ONLINE_TIMEOUT_SECONDS", 0.02):
            result = await asyncio.wait_for(site.fetch_xui_panel_online({"tag":"FR"}), 0.5)
        self.assertFalse(result["ok"])


class CacheAndSubscriptionTests(unittest.TestCase):
    def test_concurrent_online_requests_share_one_refresh_and_return_stale_data(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        stale = {"ok": True, "count": 7, "users": [], "panels": []}
        site.XUI_ONLINE_CACHE.update(created=0, data=stale, refreshing=False)

        async def fetch():
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
            return {**stale, "count": 8}

        original_refresh = site.refresh_xui_online_cache

        def refresh():
            try:
                original_refresh()
            finally:
                finished.set()

        with patch.object(site, "fetch_xui_online_snapshot", side_effect=fetch) as fetch_mock, patch.object(
            site, "refresh_xui_online_cache", side_effect=refresh
        ):
            try:
                with ThreadPoolExecutor(max_workers=8) as executor:
                    replies = list(executor.map(lambda _: site.admin_online_snapshot(), range(24)))
                self.assertTrue(started.wait(1))
                self.assertEqual(fetch_mock.call_count, 1)
                self.assertTrue(all(reply["count"] == 7 and reply["stale"] for reply in replies))
            finally:
                release.set()
                self.assertTrue(finished.wait(2))
        self.assertEqual(site.admin_online_snapshot()["count"], 8)
        self.assertFalse(site.XUI_ONLINE_CACHE["refreshing"])

    def test_all_panels_offline_is_not_reported_as_empty_online(self):
        result = site.build_xui_online_snapshot([
            dict(tag="LT", host="test", ok=False, items=[], count=0)
        ], {})
        self.assertEqual(result["error"], "panels_unavailable")
        self.assertIsNone(result["updatedAt"])

    def test_optional_subscription_404_does_not_disable_backend_for_other_users(self):
        sub_proxy.OPTIONAL_BACKEND_FAILURES.clear()
        parsed = urllib.parse.urlparse("http://panel.test")
        error = urllib.error.HTTPError("http://panel.test", 404, "missing", {}, None)
        with patch.object(sub_proxy.urllib.request, "urlopen", side_effect=error) as request:
            for sub_id in ("first", "second"):
                status, _, _ = sub_proxy._fetch_from_backend(
                    "http://panel.test", parsed, None, f"/sub/{sub_id}", label="fr"
                )
                self.assertEqual(status, 404)
        self.assertEqual(request.call_count, 2)
        self.assertFalse(sub_proxy.OPTIONAL_BACKEND_FAILURES)

    def test_optional_subscription_transport_failure_is_not_retried_per_user(self):
        sub_proxy.OPTIONAL_BACKEND_FAILURES.clear()
        parsed = urllib.parse.urlparse("http://panel.test")
        with patch.object(sub_proxy.urllib.request, "urlopen", side_effect=TimeoutError()) as request:
            with self.assertRaises(TimeoutError):
                sub_proxy._fetch_from_backend("http://panel.test", parsed, None, "/sub/first", label="fr")
            with self.assertRaises(ConnectionError):
                sub_proxy._fetch_from_backend("http://panel.test", parsed, None, "/sub/second", label="fr")
        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
