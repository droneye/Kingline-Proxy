import asyncio
from contextlib import closing
from datetime import timedelta
import importlib.util
import logging
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter


MODULE_PATH = Path(__file__).resolve().parents[1] / "handlers/subscription_delivery.py"
HTML = (
    '<b>Your key</b> <a href="https://primary.test/sub/secret-key">Primary</a>\n'
    '<a href="https://backup.test/sub/secret-key">Backup</a>'
)
CHAT_ID = -987654321


def load_delivery(database):
    # Both config and handlers.__init__ have production startup side effects.
    config = ModuleType("config")
    config.db = database
    spec = importlib.util.spec_from_file_location("subscription_delivery_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"config": config}):
        spec.loader.exec_module(module)
    return module


class SubscriptionDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "outbox.sqlite"
        self.database = SimpleNamespace(_connect=Mock(side_effect=self.connect))
        self.delivery = load_delivery(self.database)
        self.now = 1_000_000.0
        self.delivery.time = lambda: self.now
        self.bot = SimpleNamespace(send_message=AsyncMock())
        self.context = SimpleNamespace(bot=self.bot)

    def connect(self):
        return sqlite3.connect(self.path)

    def queue(self, text=HTML):
        with closing(self.connect()) as conn, conn:
            return self.delivery.queue_receipt(conn, CHAT_ID, text)

    def row(self, receipt_id):
        with closing(self.connect()) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute(
                "SELECT * FROM subscription_delivery_outbox WHERE id = ?", (receipt_id,)
            ).fetchone())

    def assert_safe_logs(self, captured, receipt_id, error_class):
        self.assertTrue(captured.records)
        for record in captured.records:
            self.assertIn(record.levelno, (logging.WARNING, logging.ERROR))
            self.assertEqual(record.getMessage(), f"receipt_id={receipt_id} error={error_class}")
            self.assertIsNone(record.exc_info)
            self.assertIsNone(record.stack_info)

    def test_schema_creation_and_subscription_creation_roll_back_together(self):
        with closing(self.connect()) as conn:
            conn.execute("CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, key TEXT)")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO subscriptions VALUES (1, 'secret-key')")
            receipt_id = self.delivery.queue_receipt(conn, CHAT_ID, HTML)
            self.delivery.ensure_schema(conn)
            self.assertIsInstance(receipt_id, int)
            self.assertTrue(conn.in_transaction)
            conn.rollback()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0], 0)
            self.assertIsNone(conn.execute(
                "SELECT name FROM sqlite_master WHERE name = 'subscription_delivery_outbox'"
            ).fetchone())
        self.database._connect.assert_not_called()

    def test_queue_does_not_commit_an_existing_schema_transaction(self):
        with closing(self.connect()) as conn:
            self.delivery.ensure_schema(conn)
            conn.execute("CREATE TABLE subscriptions (id INTEGER PRIMARY KEY, key TEXT)")
            conn.execute("INSERT INTO subscriptions VALUES (1, 'secret-key')")
            self.delivery.queue_receipt(conn, CHAT_ID, HTML)
            self.assertTrue(conn.in_transaction)
            with closing(self.connect()) as observer:
                self.assertEqual(observer.execute(
                    "SELECT COUNT(*) FROM subscription_delivery_outbox"
                ).fetchone()[0], 0)
                self.assertEqual(observer.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0], 0)
            conn.rollback()
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM subscription_delivery_outbox"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0], 0)
        self.database._connect.assert_not_called()

    async def test_success_sends_stored_html_once(self):
        receipt_id = self.queue()
        self.assertTrue(await self.delivery.deliver_receipt(self.bot, receipt_id))
        self.bot.send_message.assert_awaited_once_with(
            chat_id=CHAT_ID, text=HTML, parse_mode="HTML", disable_web_page_preview=True
        )
        row = self.row(receipt_id)
        self.assertEqual((row["status"], row["attempts"], row["text"]), ("sent", 1, HTML))
        self.assertEqual(row["sent_at"], self.now)
        self.assertIsNone(row["lease_token"])
        self.assertIsNone(row["lease_until"])
        self.assertIsNone(row["last_error"])
        self.assertFalse(await self.delivery.deliver_receipt(self.bot, receipt_id))
        self.assertFalse(await self.delivery.deliver_receipt(self.bot, receipt_id + 100))
        await self.delivery.retry_pending(self.context)
        self.bot.send_message.assert_awaited_once()

    async def test_network_error_is_persisted_then_retried_after_restart(self):
        receipt_id = self.queue()
        self.bot.send_message.side_effect = NetworkError(HTML)
        with self.assertLogs(self.delivery.logger, level="WARNING") as captured:
            self.assertFalse(await self.delivery.deliver_receipt(self.bot, receipt_id))
        self.assert_safe_logs(captured, receipt_id, "NetworkError")
        row = self.row(receipt_id)
        self.assertEqual((row["status"], row["attempts"], row["last_error"]), ("pending", 1, "NetworkError"))
        self.assertEqual(row["next_attempt_at"], self.now + 15)
        self.assertIsNone(row["lease_token"])
        restarted = load_delivery(self.database)
        restarted.time = lambda: self.now
        self.now += 14
        await restarted.retry_pending(self.context)
        self.assertFalse(await restarted.deliver_receipt(self.bot, receipt_id))
        self.bot.send_message.assert_awaited_once()
        self.now += 1
        self.bot.send_message.side_effect = None
        await restarted.retry_pending(self.context)
        self.assertEqual(self.row(receipt_id)["status"], "sent")
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_retry_after_supports_seconds_and_timedelta_and_honors_minimum(self):
        for timedelta_mode, minimum in (("false", 1), ("false", 900), ("true", 900)):
            with self.subTest(timedelta_mode=timedelta_mode, minimum=minimum):
                receipt_id = self.queue()
                bot = SimpleNamespace(send_message=AsyncMock())
                with patch.dict(os.environ, {"PTB_TIMEDELTA": timedelta_mode}):
                    error = RetryAfter(timedelta(seconds=minimum) if timedelta_mode == "true" else minimum)
                    self.assertIsInstance(error.retry_after, timedelta if timedelta_mode == "true" else int)
                    bot.send_message.side_effect = error
                    with self.assertLogs(self.delivery.logger, level="WARNING") as captured:
                        self.assertFalse(await self.delivery.deliver_receipt(bot, receipt_id))
                self.assert_safe_logs(captured, receipt_id, "RetryAfter")
                due = self.row(receipt_id)["next_attempt_at"]
                self.assertEqual(due, self.now + max(15, minimum))
                self.now = due - 1
                self.assertFalse(await self.delivery.deliver_receipt(bot, receipt_id))
                bot.send_message.assert_awaited_once()
                self.now = due
                bot.send_message.side_effect = None
                self.assertTrue(await self.delivery.deliver_receipt(bot, receipt_id))

    async def test_retries_have_exponential_backoff_and_stop_after_five_attempts(self):
        receipt_id = self.queue()
        self.bot.send_message.side_effect = NetworkError(HTML)
        for attempt, delay in enumerate((15, 30, 60, 120, None), start=1):
            with self.assertLogs(self.delivery.logger, level="WARNING"):
                self.assertFalse(await self.delivery.deliver_receipt(self.bot, receipt_id))
            row = self.row(receipt_id)
            self.assertEqual(row["attempts"], attempt)
            if delay is not None:
                self.assertEqual(row["status"], "pending")
                self.assertEqual(row["next_attempt_at"], self.now + delay)
                self.now += delay - 1
                await self.delivery.retry_pending(self.context)
                self.assertEqual(self.bot.send_message.await_count, attempt)
                self.now += 1
            else:
                self.assertEqual(row["status"], "failed")
        self.now += 100_000
        await self.delivery.retry_pending(self.context)
        self.assertFalse(await self.delivery.deliver_receipt(self.bot, receipt_id))
        self.assertEqual(self.bot.send_message.await_count, 5)

    async def test_permanent_errors_are_terminal_and_logs_do_not_contain_secrets(self):
        for error_type in (Forbidden, BadRequest, ValueError):
            with self.subTest(error_type=error_type):
                receipt_id = self.queue()
                bot = SimpleNamespace(send_message=AsyncMock(side_effect=error_type(HTML)))
                with self.assertLogs(self.delivery.logger, level="WARNING") as captured:
                    self.assertFalse(await self.delivery.deliver_receipt(bot, receipt_id))
                self.assert_safe_logs(captured, receipt_id, error_type.__name__)
                row = self.row(receipt_id)
                self.assertEqual((row["status"], row["attempts"]), ("failed", 1))
                self.assertEqual(row["last_error"], error_type.__name__)
                self.assertIsNone(row["lease_token"])
                self.now += 100_000
                for _ in range(3):
                    await self.delivery.retry_pending(SimpleNamespace(bot=bot))
                    self.assertFalse(await self.delivery.deliver_receipt(bot, receipt_id))
                bot.send_message.assert_awaited_once()

    async def test_immediate_delivery_and_workers_cannot_send_concurrently(self):
        receipt_id = self.queue()
        started = asyncio.Event()
        release = asyncio.Event()

        async def send(**kwargs):
            started.set()
            await release.wait()

        self.bot.send_message.side_effect = send
        immediate = asyncio.create_task(self.delivery.deliver_receipt(self.bot, receipt_id))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            row = self.row(receipt_id)
            self.assertEqual((row["status"], row["attempts"]), ("sending", 1))
            self.assertEqual(row["lease_until"], self.now + 120)
            self.assertFalse(await self.delivery.deliver_receipt(self.bot, receipt_id))
            await asyncio.gather(
                self.delivery.retry_pending(self.context),
                self.delivery.retry_pending(self.context),
            )
            self.bot.send_message.assert_awaited_once()
        finally:
            release.set()
            result = await immediate
        self.assertTrue(result)

    def test_claim_is_atomic_across_independent_connections_and_event_loops(self):
        receipt_id = self.queue()
        barrier = threading.Barrier(2)
        release = threading.Event()
        claimed = threading.Event()
        loser_finished = threading.Event()
        results = []
        errors = []

        async def send(**kwargs):
            claimed.set()
            while not release.is_set():
                await asyncio.sleep(0.01)

        self.bot.send_message.side_effect = send
        other = load_delivery(self.database)
        other.time = lambda: self.now

        def run(module):
            try:
                barrier.wait(timeout=5)
                result = asyncio.run(module.deliver_receipt(self.bot, receipt_id))
                results.append(result)
                if not result:
                    loser_finished.set()
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=run, args=(module,))
                   for module in (self.delivery, other)]
        for thread in threads:
            thread.start()
        try:
            self.assertTrue(claimed.wait(5))
            self.assertTrue(loser_finished.wait(5))
            self.assertEqual(self.row(receipt_id)["attempts"], 1)
            self.bot.send_message.assert_awaited_once()
        finally:
            release.set()
            for thread in threads:
                thread.join(timeout=5)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [False, True])

    async def test_worker_reclaims_crash_and_stale_owner_cannot_update_receipt(self):
        receipt_id = self.queue()
        old_claim = self.delivery._claim_receipt(receipt_id)
        self.now += 119
        await self.delivery.retry_pending(self.context)
        self.bot.send_message.assert_not_awaited()
        self.now += 1

        async def send(**kwargs):
            active = self.row(receipt_id)
            self.assertNotEqual(active["lease_token"], old_claim[3])
            self.assertFalse(self.delivery._finish_receipt(receipt_id, old_claim[3], "sent"))
            self.assertFalse(self.delivery._finish_receipt(
                receipt_id, old_claim[3], "failed", last_error="Forbidden"
            ))
            self.assertEqual(self.row(receipt_id), active)

        self.bot.send_message.side_effect = send
        await self.delivery.retry_pending(self.context)
        row = self.row(receipt_id)
        self.assertEqual((row["status"], row["attempts"]), ("sent", 2))
        self.bot.send_message.assert_awaited_once()

    async def test_crashes_also_exhaust_the_attempt_budget(self):
        receipt_id = self.queue()
        for _ in range(5):
            self.assertIsNotNone(self.delivery._claim_receipt(receipt_id))
            self.now += 120
        with self.assertLogs(self.delivery.logger, level="ERROR") as captured:
            await self.delivery.retry_pending(self.context)
        self.assert_safe_logs(captured, receipt_id, "TimeoutError")
        self.assertEqual(self.row(receipt_id)["status"], "failed")
        await self.delivery.retry_pending(self.context)
        self.bot.send_message.assert_not_awaited()

    async def test_cancelled_send_leaves_a_recoverable_lease(self):
        receipt_id = self.queue()
        started = asyncio.Event()

        async def send(**kwargs):
            started.set()
            await asyncio.Event().wait()

        self.bot.send_message.side_effect = send
        task = asyncio.create_task(self.delivery.deliver_receipt(self.bot, receipt_id))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.row(receipt_id)["status"], "sending")
        self.bot.send_message.side_effect = None
        await self.delivery.retry_pending(self.context)
        self.bot.send_message.assert_awaited_once()
        self.now += 120
        await self.delivery.retry_pending(self.context)
        self.assertEqual(self.row(receipt_id)["status"], "sent")

    async def test_hung_send_times_out_before_lease_expiry_and_can_retry(self):
        receipt_id = self.queue()

        async def send(**kwargs):
            await asyncio.Event().wait()

        self.bot.send_message.side_effect = send
        self.assertLess(self.delivery.SEND_TIMEOUT_SECONDS, self.delivery.LEASE_SECONDS)
        with patch.object(self.delivery, "SEND_TIMEOUT_SECONDS", 0.01):
            with self.assertLogs(self.delivery.logger, level="WARNING") as captured:
                self.assertFalse(await self.delivery.deliver_receipt(self.bot, receipt_id))
        self.assert_safe_logs(captured, receipt_id, "TimeoutError")
        self.assertEqual(self.row(receipt_id)["status"], "pending")
        self.bot.send_message.side_effect = None
        self.now += 15
        await self.delivery.retry_pending(self.context)
        self.assertEqual(self.row(receipt_id)["status"], "sent")

    async def test_failed_success_commit_is_recoverable_and_logs_only_error_class(self):
        receipt_id = self.queue()
        with patch.object(self.delivery, "_finish_receipt", side_effect=sqlite3.OperationalError(HTML)):
            with self.assertLogs(self.delivery.logger, level="ERROR") as captured:
                self.assertFalse(await self.delivery.deliver_receipt(self.bot, receipt_id))
        self.assert_safe_logs(captured, receipt_id, "OperationalError")
        self.assertEqual(self.row(receipt_id)["status"], "sending")
        self.now += 120
        await self.delivery.retry_pending(self.context)
        self.assertEqual(self.row(receipt_id)["status"], "sent")
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_worker_continues_after_a_permanent_failure_and_limits_batch(self):
        receipts = [self.queue() for _ in range(4)]
        self.bot.send_message.side_effect = [Forbidden(HTML), None, None, None]
        with patch.object(self.delivery, "BATCH_SIZE", 2):
            with self.assertLogs(self.delivery.logger, level="ERROR"):
                await self.delivery.retry_pending(self.context)
            self.assertEqual([self.row(r)["status"] for r in receipts], ["failed", "sent", "pending", "pending"])
            await self.delivery.retry_pending(self.context)
        self.assertEqual([self.row(r)["status"] for r in receipts], ["failed", "sent", "sent", "sent"])

    async def test_empty_database_and_connection_errors_do_not_escape_or_leak(self):
        await self.delivery.retry_pending(self.context)
        self.assertFalse(await self.delivery.deliver_receipt(self.bot, 42))
        with patch.object(self.database, "_connect", side_effect=sqlite3.OperationalError(HTML)):
            with self.assertLogs(self.delivery.logger, level="ERROR") as captured:
                self.assertFalse(await self.delivery.deliver_receipt(self.bot, 42))
            self.assert_safe_logs(captured, 42, "OperationalError")
            with self.assertLogs(self.delivery.logger, level="ERROR") as captured:
                await self.delivery.retry_pending(self.context)
            self.assert_safe_logs(captured, None, "OperationalError")
        self.bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
