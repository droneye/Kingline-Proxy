"""Bounded, asynchronous JSONL diagnostics shared by the bot and site."""
import asyncio
import atexit
from collections import Counter
from contextvars import ContextVar
from datetime import datetime, timezone
import fcntl
from functools import wraps
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
import traceback
from urllib.parse import urlsplit
import uuid


REQUEST_ID = ContextVar("diagnostic_request_id", default=None)
_recorder = None
_BUCKETS = (1, 5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 30000, float("inf"))
_URL = re.compile(r"(?:https?|vless|vmess|trojan|ss)://[^\s\"'<>]+", re.I)
_ASSIGNMENT = re.compile(
    r"(?i)(password|passwd|private_key|passphrase|token|secret|authorization|cookie|sub_id|tg_id|username)"
    r"([\"']?\s*[:=]\s*)(?:[\"'][^\"']*[\"']|[^\s,;}]+)"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


def sanitize(value):
    text = str(value)
    for key, secret in os.environ.items():
        if len(secret) >= 6 and any(part in key.upper() for part in ("TOKEN", "PASSWORD", "SECRET", "API_KEY")):
            text = text.replace(secret, "[redacted]")
    def redact_url(match):
        try:
            host = urlsplit(match[0]).hostname or "remote"
        except ValueError:
            host = "remote"
        return host + "/[redacted]"

    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----", "[private key redacted]", text)
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
    text = _URL.sub(redact_url, text)
    text = _ASSIGNMENT.sub(r"\1\2[redacted]", text)
    return _EMAIL.sub("[email]", text)[:3000]


def sanitize_fields(value):
    if isinstance(value, str): return sanitize(value)
    if isinstance(value, dict):
        return {str(k): ('[redacted]' if re.search(r'password|private_key|passphrase|token|secret|cookie|authorization', str(k), re.I) else sanitize_fields(v)) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [sanitize_fields(v) for v in value[:100]]
    return value


def safe_route(path):
    try:
        path = urlsplit(path or "").path
    except ValueError:
        return "/invalid"
    if path.startswith("/sub/"):
        return "/sub/:id"
    if path.startswith("/api/"):
        parts = path.split("/")[:6]
        return "/" + "/".join(
            part if re.fullmatch(r"[a-z][a-z-]{0,40}", part) else ":id" for part in parts[1:]
        )
    return "/health" if path in ("/", "/health") else "/other"


def exception_fields(exc):
    return {
        "error": type(exc).__name__,
        "message": sanitize(exc),
        "stack": [
            {"file": os.path.basename(frame.filename), "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)[-16:]
        ],
    }


class _PrivateFileHandler(RotatingFileHandler):
    def handleError(self, record):
        raise sys.exc_info()[1]

    def _open(self):
        fd = os.open(self.baseFilename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o640)
        return os.fdopen(fd, "a", encoding="utf-8")


class Recorder:
    def __init__(self, service, path, max_bytes=20 * 1024 * 1024, backups=10, interval=60):
        self.service = service
        self.path = Path(path)
        self.max_bytes = max_bytes
        self.backups = backups
        self.interval = interval
        self.events = queue.Queue(maxsize=2048)
        self.lock = threading.Lock()
        self.stats = {}
        self.dropped = 0
        self.write_errors = 0
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self._run, name="diagnostics-writer", daemon=True)
        self.worker.start()

    def event(self, event, level="INFO", **fields):
        item = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "service": self.service, "pid": os.getpid(), "level": level, "event": event, **fields}
        request_id = REQUEST_ID.get()
        if request_id:
            item["request_id"] = request_id
        try:
            self.events.put_nowait(item)
        except queue.Full:
            with self.lock:
                self.dropped += 1

    def observe(self, operation, duration_ms, *, target="", status=None, error=None, slow_ms=1000):
        operation, target = operation[:120], target[:120]
        duration_ms = round(max(0, duration_ms), 3)
        failed = bool(error) or (status is not None and status >= 400)
        key = (operation, target)
        with self.lock:
            if key not in self.stats and len(self.stats) >= 256:
                key = ("other", "")
            stat = self.stats.setdefault(key, {"count": 0, "errors": 0, "slow": 0,
                                             "sum": 0, "max": 0, "buckets": [0] * len(_BUCKETS),
                                             "statuses": Counter()})
            stat["count"] += 1
            stat["errors"] += int(failed)
            stat["slow"] += int(duration_ms >= slow_ms)
            stat["sum"] += duration_ms
            stat["max"] = max(stat["max"], duration_ms)
            if status is not None:
                stat["statuses"][str(status)] += 1
            for index, limit in enumerate(_BUCKETS):
                if duration_ms <= limit:
                    stat["buckets"][index] += 1
                    break
        if failed or duration_ms >= slow_ms:
            self.event("operation_failed" if failed else "slow_operation", "WARNING",
                       operation=operation, target=target, duration_ms=duration_ms,
                       status=status, error=error)

    def flush_metrics(self):
        with self.lock:
            stats, self.stats = self.stats, {}
            dropped, self.dropped = self.dropped, 0
            write_errors, self.write_errors = self.write_errors, 0
        for (operation, target), stat in stats.items():
            cumulative = 0
            p95 = None
            for limit, count in zip(_BUCKETS, stat["buckets"]):
                cumulative += count
                if cumulative >= stat["count"] * 0.95:
                    p95 = limit if limit != float("inf") else None
                    break
            self.event("latency_summary", operation=operation, target=target,
                       window_seconds=self.interval, count=stat["count"], errors=stat["errors"],
                       slow=stat["slow"], avg_ms=round(stat["sum"] / stat["count"], 3),
                       max_ms=stat["max"], p95_ms_upper=p95, statuses=dict(stat["statuses"]))
        if dropped or write_errors:
            self.event("diagnostics_loss", "ERROR", dropped_events=dropped, write_errors=write_errors)

    def _write(self, item):
        self.path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
        # Every process rotates under the same lock and opens the current file.
        fd = os.open(str(self.path) + ".lock", os.O_WRONLY | os.O_CREAT, 0o640)
        with os.fdopen(fd, "a") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            handler = _PrivateFileHandler(self.path, maxBytes=self.max_bytes,
                                          backupCount=self.backups, encoding="utf-8", delay=True)
            try:
                line = json.dumps(sanitize_fields(item), ensure_ascii=True, allow_nan=False)
                record = logging.LogRecord("diagnostics", logging.INFO, "", 0, line, (), None)
                handler.emit(record)
            finally:
                handler.close()

    def _run(self):
        next_flush = time.monotonic() + self.interval
        while not self.stop.is_set() or not self.events.empty():
            if time.monotonic() >= next_flush:
                self.flush_metrics()
                next_flush = time.monotonic() + self.interval
            try:
                item = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._write(item)
            except Exception:
                with self.lock:
                    self.write_errors += 1
            finally:
                self.events.task_done()

    def close(self):
        if self.stop.is_set():
            return
        self.flush_metrics()
        self.stop.set()
        self.worker.join(timeout=3)


class WarningHandler(logging.Handler):
    def emit(self, record):
        try:
            fields = {"logger": record.name, "message": sanitize(record.getMessage()),
                      "file": os.path.basename(record.pathname), "line": record.lineno}
            if record.exc_info and record.exc_info[1]:
                fields.update(exception_fields(record.exc_info[1]))
            event("application_log", record.levelname, **fields)
        except Exception:
            pass


def configure(service):
    global _recorder
    if _recorder is not None:
        return _recorder
    path = os.getenv("KINGLINE_DIAGNOSTICS_PATH", "/var/log/kingline/diagnostics.jsonl")
    _recorder = Recorder(service, path, max_bytes=int(os.getenv('KINGLINE_LOG_MAX_BYTES', '2097152')), backups=int(os.getenv('KINGLINE_LOG_BACKUPS', '3')))
    logging.getLogger().addHandler(WarningHandler(logging.WARNING))
    previous_hook = sys.excepthook
    previous_thread_hook = threading.excepthook

    def exception_hook(kind, value, tb):
        event("uncaught_exception", "ERROR", **exception_fields(value))
        previous_hook(kind, value, tb)

    def thread_hook(args):
        event("thread_exception", "ERROR", thread=args.thread.name, **exception_fields(args.exc_value))
        previous_thread_hook(args)

    sys.excepthook = exception_hook
    threading.excepthook = thread_hook
    atexit.register(_recorder.close)
    event("service_started")
    return _recorder


def event(name, level="INFO", **fields):
    if _recorder is not None:
        _recorder.event(name, level, **fields)


def observe(operation, duration_ms, **fields):
    if _recorder is not None:
        _recorder.observe(operation, duration_ms, **fields)


def timed_async(operation, *, slow_ms=1000, target_attr=None):
    def decorate(func):
        @wraps(func)
        async def wrapped(*args, **kwargs):
            started = time.monotonic()
            error, status = None, None
            target = urlsplit(getattr(args[0], target_attr, "")).hostname or "" if target_attr and args else ""
            try:
                result = await func(*args, **kwargs)
                if isinstance(result, tuple) and result and isinstance(result[0], int):
                    status = result[0]
                    if len(result) > 1 and isinstance(result[1], dict) and result[1].get("success") is False:
                        error = "APIRejected"
                return result
            except BaseException as exc:
                error = type(exc).__name__
                raise
            finally:
                observe(operation, (time.monotonic() - started) * 1000,
                        target=target, status=status, error=error, slow_ms=slow_ms)
        return wrapped
    return decorate


def timed_sync(operation, *, slow_ms=1000):
    def decorate(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            started, error = time.monotonic(), None
            try:
                return func(*args, **kwargs)
            except BaseException as exc:
                error = type(exc).__name__
                raise
            finally:
                observe(operation, (time.monotonic() - started) * 1000, error=error, slow_ms=slow_ms)
        return wrapped
    return decorate


async def watch_event_loop():
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def handle_error(active_loop, context):
        exc = context.get("exception")
        event("async_exception", "ERROR", **(exception_fields(exc) if exc else {"message": sanitize(context.get("message"))}))
        if previous_handler:
            previous_handler(active_loop, context)
        else:
            active_loop.default_exception_handler(context)

    loop.set_exception_handler(handle_error)
    try:
        while True:
            started = loop.time()
            await asyncio.sleep(1)
            observe("event_loop_lag", max(0, loop.time() - started - 1) * 1000, slow_ms=200)
    finally:
        loop.set_exception_handler(previous_handler)


class RequestDiagnosticsMixin:
    def parse_request(self):
        self._diag_started = time.monotonic()
        self._diag_token = REQUEST_ID.set(uuid.uuid4().hex)
        return super().parse_request()

    def end_headers(self):
        if REQUEST_ID.get(): self.send_header("X-Request-ID", REQUEST_ID.get())
        return super().end_headers()

    def send_response(self, code, message=None):
        self._diag_status = code
        return super().send_response(code, message)

    def handle_one_request(self):
        self._diag_started = None
        self._diag_status = None
        self._diag_token = None
        error = None
        try:
            return super().handle_one_request()
        except Exception as exc:
            error = type(exc).__name__
            event("http_exception", "ERROR", **exception_fields(exc))
            raise
        finally:
            if self._diag_started is not None:
                observe("http." + str(getattr(self, "command", None) or "UNKNOWN"),
                        (time.monotonic() - self._diag_started) * 1000,
                        target=safe_route(getattr(self, "path", "")),
                        status=self._diag_status, error=error)
            if self._diag_token is not None:
                REQUEST_ID.reset(self._diag_token)
