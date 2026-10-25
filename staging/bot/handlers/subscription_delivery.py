"""Persist caller-rendered HTML receipts without changing subscriptions or balances.

Queue on the subscription transaction's connection, commit it, then optionally
call deliver_receipt. Schedule retry_pending externally every 15 seconds.
Delivery is at least once: a crash after Telegram accepts a send but before the
sent state commits can cause a later duplicate. Leases exclude concurrent sends
while valid, and their tokens fence off writes from obsolete owners.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from datetime import timedelta
import logging
import sqlite3
from time import time
from uuid import uuid4

from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter

from config import db


logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BACKOFF_SECONDS = 15
MAX_BACKOFF_SECONDS = 300
SEND_TIMEOUT_SECONDS = 30
LEASE_SECONDS = 120
BATCH_SIZE = 50


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the outbox and indexes without committing the caller's transaction.

    All timestamps are UTC Unix seconds. last_error stores only a class name.
    Do not use executescript here: it can commit an existing transaction.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subscription_delivery_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
            attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            next_attempt_at REAL NOT NULL DEFAULT 0,
            lease_until REAL,
            lease_token TEXT,
            last_error TEXT,
            created_at REAL NOT NULL,
            sent_at REAL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS subscription_delivery_due "
        "ON subscription_delivery_outbox (status, next_attempt_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS subscription_delivery_leases "
        "ON subscription_delivery_outbox (status, lease_until)"
    )


def queue_receipt(conn: sqlite3.Connection, chat_id: int, text: str) -> int:
    """Enqueue HTML unchanged; the caller owns commit/rollback and URL rendering."""
    ensure_schema(conn)
    now = time()
    cursor = conn.execute(
        "INSERT INTO subscription_delivery_outbox "
        "(chat_id, text, created_at, next_attempt_at) VALUES (?, ?, ?, ?)",
        (chat_id, text, now, now),
    )
    return int(cursor.lastrowid)


def _claim_receipt(receipt_id: int) -> tuple[int, str, int, str] | None:
    with closing(db._connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        ensure_schema(conn)
        now = time()
        row = conn.execute(
            "SELECT chat_id, text, attempts, status, next_attempt_at, lease_until "
            "FROM subscription_delivery_outbox WHERE id = ?",
            (receipt_id,),
        ).fetchone()
        if row is None:
            return None
        chat_id, text, attempts, status, next_attempt_at, lease_until = row
        if status in ("sent", "failed"):
            return None
        if status == "pending" and next_attempt_at > now:
            return None
        if status == "sending" and lease_until > now:
            return None
        if attempts >= MAX_ATTEMPTS:
            # A crash on the last attempt must not leave a receipt retrying forever.
            conn.execute(
                "UPDATE subscription_delivery_outbox SET status = 'failed', "
                "lease_token = NULL, lease_until = NULL, last_error = 'TimeoutError' "
                "WHERE id = ?",
                (receipt_id,),
            )
            logger.error("receipt_id=%s error=%s", receipt_id, "TimeoutError")
            return None
        token = uuid4().hex
        conn.execute(
            "UPDATE subscription_delivery_outbox SET status = 'sending', "
            "attempts = attempts + 1, lease_token = ?, lease_until = ? WHERE id = ?",
            (token, now + LEASE_SECONDS, receipt_id),
        )
        return chat_id, text, attempts + 1, token


def _finish_receipt(
    receipt_id: int,
    token: str,
    status: str,
    *,
    next_attempt_at: float = 0,
    last_error: str | None = None,
) -> bool:
    with closing(db._connect()) as conn, conn:
        cursor = conn.execute(
            "UPDATE subscription_delivery_outbox SET status = ?, "
            "next_attempt_at = ?, last_error = ?, sent_at = ?, "
            "lease_token = NULL, lease_until = NULL "
            "WHERE id = ? AND status = 'sending' AND lease_token = ?",
            (
                status,
                next_attempt_at,
                last_error,
                time() if status == "sent" else None,
                receipt_id,
                token,
            ),
        )
        return cursor.rowcount == 1


def _record_failure(
    receipt_id: int, token: str, attempts: int, error: Exception, *, transient: bool
) -> None:
    retry = transient and attempts < MAX_ATTEMPTS
    delay = min(BACKOFF_SECONDS * 2 ** (attempts - 1), MAX_BACKOFF_SECONDS)
    if isinstance(error, RetryAfter):
        retry_after = error.retry_after
        if isinstance(retry_after, timedelta):
            retry_after = retry_after.total_seconds()
        # Telegram's minimum delay may exceed our normal backoff cap.
        delay = max(delay, float(retry_after))
    error_class = type(error).__name__
    _finish_receipt(
        receipt_id,
        token,
        "pending" if retry else "failed",
        next_attempt_at=time() + delay if retry else 0,
        last_error=error_class,
    )
    log = logger.warning if retry else logger.error
    log("receipt_id=%s error=%s", receipt_id, error_class)


async def deliver_receipt(bot, receipt_id: int) -> bool:
    """Return True only when this call sends and persists success.

    Missing, leased, delayed, sent, and failed receipts return False. Transient
    failures get at most five total attempts; other send errors are terminal.
    Cancellation leaves the lease for recovery. No transaction spans an await.
    """
    try:
        receipt = _claim_receipt(receipt_id)
        if receipt is None:
            return False
        chat_id, text, attempts, token = receipt
        try:
            await asyncio.wait_for(
                bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                ),
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except (Forbidden, BadRequest) as error:
            # BadRequest is also a NetworkError, so catch it first.
            _record_failure(receipt_id, token, attempts, error, transient=False)
        except (NetworkError, RetryAfter, asyncio.TimeoutError) as error:
            _record_failure(receipt_id, token, attempts, error, transient=True)
        except Exception as error:
            _record_failure(receipt_id, token, attempts, error, transient=False)
        else:
            return _finish_receipt(receipt_id, token, "sent")
    except Exception as error:
        # In particular, a failed acknowledgement leaves its lease recoverable.
        logger.error("receipt_id=%s error=%s", receipt_id, type(error).__name__)
    return False


async def retry_pending(context) -> None:
    """JobQueue callback: try up to 50 due or expired receipts using context.bot."""
    try:
        with closing(db._connect()) as conn, conn:
            ensure_schema(conn)
            now = time()
            rows = conn.execute(
                "SELECT id FROM subscription_delivery_outbox "
                "WHERE (status = 'pending' AND next_attempt_at <= ?) "
                "OR (status = 'sending' AND lease_until <= ?) "
                "ORDER BY next_attempt_at, id LIMIT ?",
                (now, now, BATCH_SIZE),
            ).fetchall()
    except Exception as error:
        logger.error("receipt_id=%s error=%s", None, type(error).__name__)
        return
    for (receipt_id,) in rows:
        await deliver_receipt(context.bot, receipt_id)
