import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import diagnostics as diag


def write_from_process(path, source):
    recorder = diag.Recorder(source, path, max_bytes=700, backups=30)
    for index in range(30):
        recorder.event("sample", index=index)
    recorder.close()


def read_events(path):
    return [json.loads(line) for file in sorted(path.parent.glob(path.name + "*"))
            if file.suffix != ".lock" for line in file.read_text().splitlines()]


class DiagnosticsTests(unittest.TestCase):
    def test_rotation_is_valid_and_preserves_events_from_two_processes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diagnostics.jsonl"
            ctx = multiprocessing.get_context("spawn")
            processes = [ctx.Process(target=write_from_process, args=(str(path), source))
                         for source in ("bot", "site-api")]
            for process in processes:
                process.start()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            events = read_events(path)
            self.assertEqual(len(events), 60)
            self.assertEqual({event["service"] for event in events}, {"bot", "site-api"})
            self.assertTrue((path.parent / "diagnostics.jsonl.1").exists())
            self.assertLessEqual(len(list(path.parent.glob("diagnostics.jsonl.*"))), 31)
            self.assertIn(path.stat().st_mode & 0o777, (0o600, 0o640))

    def test_slow_and_failed_operations_and_summary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diagnostics.jsonl"
            recorder = diag.Recorder("test", path)
            recorder.observe("http.GET", 10, target="/api/health", status=200)
            recorder.observe("http.GET", 1500, target="/api/health", status=200)
            recorder.observe("http.GET", 30, target="/api/health", status=503)
            recorder.close()
            events = read_events(path)
            self.assertEqual(sum(e["event"] == "slow_operation" for e in events), 1)
            self.assertEqual(sum(e["event"] == "operation_failed" for e in events), 1)
            summary = next(e for e in events if e["event"] == "latency_summary")
            self.assertEqual(summary["count"], 3)
            self.assertEqual(summary["errors"], 1)
            self.assertEqual(summary["slow"], 1)
            self.assertEqual(summary["p95_ms_upper"], 2000)

    def test_secrets_urls_and_emails_are_redacted(self):
        with patch.dict(os.environ, {"BOT_TOKEN": "123456:test-secret-token"}):
            text = diag.sanitize(
                'failure password="password-one" token=private-token '
                'https://panel.test/sub/private-key?token=secret '
                'person@example.com 123456:test-secret-token'
            )
        for secret in ("password-one", "private-token", "private-key", "person@example.com", "test-secret-token"):
            self.assertNotIn(secret, text)
        self.assertEqual(diag.safe_route("/sub/private-key?secret=1"), "/sub/:id")
        self.assertEqual(diag.safe_route("/api/account/job-status?jobId=secret"), "/api/account/job-status")

    def test_failure_to_write_never_propagates_to_request(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "file-not-directory"
            path.touch()
            recorder = diag.Recorder("test", path / "diagnostics.jsonl")
            recorder.event("test")
            recorder.events.join()
            self.assertGreater(recorder.write_errors, 0)
            recorder.close()
            self.assertFalse(recorder.worker.is_alive())

    def test_warning_handler_captures_stack_without_local_variables(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diagnostics.jsonl"
            recorder = diag.Recorder("test", path)
            with patch.object(diag, "_recorder", recorder):
                try:
                    raise RuntimeError("password=do-not-log")
                except RuntimeError:
                    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
                    diag.WarningHandler().emit(record)
            recorder.close()
            result = read_events(path)[0]
            self.assertEqual(result["error"], "RuntimeError")
            self.assertTrue(result["stack"])
            self.assertNotIn("do-not-log", path.read_text())

    def test_http_status_duration_and_request_id_are_recorded_without_query(self):
        class Handler(diag.RequestDiagnosticsMixin, BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(503)
                self.end_headers()

            def log_message(self, *args):
                pass

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diagnostics.jsonl"
            recorder = diag.Recorder("test", path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                with patch.object(diag, "_recorder", recorder):
                    with self.assertRaises(urllib.error.HTTPError):
                        urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/health?secret=hidden")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                recorder.close()
            result = next(e for e in read_events(path) if e["event"] == "operation_failed")
            self.assertEqual(result["status"], 503)
            self.assertEqual(result["target"], "/api/health")
            self.assertTrue(result["request_id"])
            self.assertNotIn("hidden", path.read_text())


class AsyncDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_timing_preserves_cancellation_and_captures_api_rejection(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diagnostics.jsonl"
            recorder = diag.Recorder("test", path)

            @diag.timed_async("api")
            async def rejected():
                return 200, {"success": False}

            @diag.timed_async("cancelled")
            async def cancelled():
                raise asyncio.CancelledError()

            with patch.object(diag, "_recorder", recorder):
                self.assertEqual(await rejected(), (200, {"success": False}))
                with self.assertRaises(asyncio.CancelledError):
                    await cancelled()
            recorder.close()
            failures = [e for e in read_events(path) if e["event"] == "operation_failed"]
            self.assertEqual({e["error"] for e in failures}, {"APIRejected", "CancelledError"})


if __name__ == "__main__":
    unittest.main()
