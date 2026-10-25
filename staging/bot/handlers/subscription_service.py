"""One renewal transaction for bot, website and referral rewards."""
from datetime import datetime, timedelta, timezone
from html import escape
from contextlib import closing
import logging
import time

from config import db, FULL_TARIFF_PRICE
from utils import build_sub_link
from .billing_db import BillingDB
from .subscription_delivery import queue_receipt


logger = logging.getLogger(__name__)


def ensure_panel_sync_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS subscription_panel_sync (
        tg_id INTEGER NOT NULL, panel_tag TEXT NOT NULL,
        next_attempt_at REAL NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (tg_id, panel_tag)
    )""")


def expiry_datetime(user):
    value = user.get("key_expires_at") or user.get("subscription_expires_at")
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def receipt_text(kind, is_extension, expires, sub_id, balance):
    if kind == "trial":
        title = "Пробная подписка активирована на 24 часа."
    elif kind == "referral":
        title = "Подписка продлена на 30 дней за приглашение."
    else:
        title = "Premium продлён на 30 дней." if is_extension else "Premium активирован на 30 дней."
    paid = f"\nСписано {FULL_TARIFF_PRICE} ₽. Баланс: {balance} ₽." if kind == "premium" else ""
    date = expires.astimezone(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M")
    return (
        f"<b>{title}</b>{paid}\nДоступ до <b>{date}</b> (МСК).\n\n"
        f"Ваша ссылка для подключения:\n<pre>{escape(build_sub_link(sub_id))}</pre>\n\n"
        "Обновите подписку в приложении. Ссылку менять не нужно. Первое подключение — в «Инструкция»."
    )


# Both website and bot share the durable native-Remnawave implementation.
from .durable_subscription import (activate_subscription, retry_panel_sync,
    SubscriptionPending, request_key, operation_stage)
