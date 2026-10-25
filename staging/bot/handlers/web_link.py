from __future__ import annotations

import asyncio
import html
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from config import db, logger
from utils import build_sub_link, now_iso
from .billing_db import BillingDB


BONUS_DAYS = 3


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _tg_username_clean(update: Update, tg_id: int) -> str:
    user = update.effective_user
    raw = ""
    if user:
        raw = user.username or user.first_name or f"user{tg_id}"
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "", raw.replace("@", "").replace(" ", ""))
    return cleaned[:48] or f"user{tg_id}"


def _display_name(update: Update) -> str:
    user = update.effective_user
    if not user:
        return ""
    return user.full_name or user.username or ""


def _active_exp(row: Optional[sqlite3.Row]) -> Optional[datetime]:
    if not row:
        return None
    return _parse_iso(row["subscription_expires_at"]) or _parse_iso(row["key_expires_at"])


def _is_active_premium(row: Optional[sqlite3.Row]) -> bool:
    exp = _active_exp(row)
    return bool(row and (row["tariff"] or "").lower() == "premium" and exp and exp > _utc_now())


def _row_balance(row: Optional[sqlite3.Row]) -> int:
    return int((row and row["balance_rub"]) or 0)


def _normalize_location_pref(value: object) -> str:
    raw = str(value or "auto").strip().lower()
    if raw.startswith("lt") or raw in {"литва", "lithuania", "🇱🇹"}:
        return "lt"
    if raw.startswith("nl") or raw == "de" or raw in {
        "германия",
        "germany",
        "deutschland",
        "нидерланды",
        "netherlands",
        "holland",
        "🇩🇪",
        "🇳🇱",
    }:
        return "nl"
    return "auto"


def _merge_location_pref(primary: object, fallback: object) -> str:
    primary_loc = _normalize_location_pref(primary)
    if primary_loc != "auto":
        return primary_loc

    fallback_loc = _normalize_location_pref(fallback)
    if fallback_loc != "auto":
        return fallback_loc

    return "auto"


def _ensure_schema() -> None:
    with db._connect() as conn:  # type: ignore[attr-defined]
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_link_codes (
                code TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                bonus_days INTEGER NOT NULL DEFAULT 3,
                expires_at TEXT NOT NULL,
                used_at TEXT,
                used_tg_id INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(web_accounts)").fetchall()
        }
        if "telegram_bonus_claimed" not in columns:
            conn.execute(
                "ALTER TABLE web_accounts ADD COLUMN telegram_bonus_claimed INTEGER DEFAULT 0"
            )
        if "telegram_bonus_claimed_at" not in columns:
            conn.execute("ALTER TABLE web_accounts ADD COLUMN telegram_bonus_claimed_at TEXT")
        conn.commit()


async def cmd_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return

    _ensure_schema()

    args = context.args or []
    code = (args[0] if args else "").strip().upper()
    if not code:
        await update.message.reply_text(
            "Отправь команду с кодом с сайта:\n\n"
            "<code>/link 9F2A7C81B4D6E0A3</code>\n\n"
            "Код появляется в разделе «Мой кабинет» после выдачи ссылки подписки.",
            parse_mode="HTML",
        )
        return

    tg_id = int(update.effective_user.id)

    try:
        with db._connect() as conn:  # type: ignore[attr-defined]
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            rec = cur.execute(
                """
                SELECT
                    c.code,
                    c.account_id,
                    c.bonus_days,
                    c.expires_at,
                    c.used_at,
                    a.email,
                    a.name,
                    a.web_tg_id,
                    a.linked_tg_id,
                    a.telegram_bonus_claimed
                FROM web_link_codes c
                JOIN web_accounts a ON a.id = c.account_id
                WHERE c.code = ?
                LIMIT 1
                """,
                (code,),
            ).fetchone()

            if not rec:
                conn.rollback()
                await update.message.reply_text("Код не найден или уже истёк.")
                return

            if rec["used_at"]:
                conn.rollback()
                await update.message.reply_text("Этот код уже использован.")
                return

            expires_at = _parse_iso(rec["expires_at"])
            if not expires_at or expires_at <= _utc_now():
                conn.rollback()
                await update.message.reply_text("Срок действия кода истёк. Создай новый код на сайте.")
                return

            linked_tg_id = rec["linked_tg_id"]
            if linked_tg_id and int(linked_tg_id) != tg_id:
                conn.rollback()
                await update.message.reply_text("Этот web-аккаунт уже связан с другим Telegram.")
                return

            existing_link = cur.execute(
                """
                SELECT id
                FROM web_accounts
                WHERE linked_tg_id = ? AND id != ?
                LIMIT 1
                """,
                (tg_id, rec["account_id"]),
            ).fetchone()
            if existing_link:
                conn.rollback()
                await update.message.reply_text("Этот Telegram уже связан с другим web-аккаунтом.")
                return

            if int(rec["telegram_bonus_claimed"] or 0) == 1:
                conn.rollback()
                await update.message.reply_text("Бонус за Telegram уже был выдан.")
                return

            web_tg_id = int(rec["web_tg_id"])
            columns = """
                tg_id,
                tg_name,
                username,
                tariff,
                sub_id,
                sub_name,
                key_created_at,
                key_expires_at,
                client_id1,
                client_id2,
                client_id3,
                balance_rub,
                free_used,
                subscription_started_at,
                subscription_expires_at,
                location_pref
            """
            web_user = cur.execute(
                f"SELECT {columns} FROM users WHERE tg_id = ? LIMIT 1",
                (web_tg_id,),
            ).fetchone()
            tg_user = cur.execute(
                f"SELECT {columns} FROM users WHERE tg_id = ? LIMIT 1",
                (tg_id,),
            ).fetchone()

            if not web_user or not web_user["sub_id"]:
                conn.rollback()
                await update.message.reply_text(
                    "Сначала получи ссылку подписки на сайте, потом привяжи Telegram."
                )
                return

            cur.execute(
                """
                UPDATE web_link_codes
                SET used_at = ?, used_tg_id = ?
                WHERE code = ?
                """,
                (now_iso(), tg_id, code),
            )
            cur.execute(
                """
                UPDATE web_accounts
                SET linked_tg_id = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (tg_id, rec["account_id"]),
            )
            conn.commit()

        web_exp = _active_exp(web_user)
        tg_exp = _active_exp(tg_user)
        base_exp = max(
            [dt for dt in (web_exp, tg_exp, _utc_now()) if dt is not None]
        )
        bonus_days = int(rec["bonus_days"] or BONUS_DAYS)
        new_exp = base_exp + timedelta(days=bonus_days)
        expiry_time_ms = int(new_exp.timestamp() * 1000)

        username_clean = _tg_username_clean(update, tg_id)
        tg_name = _display_name(update) or (tg_user and tg_user["tg_name"]) or rec["name"] or rec["email"]
        sub_id = str((tg_user and tg_user["sub_id"]) or web_user["sub_id"])
        sub_name = str((tg_user and tg_user["sub_name"]) or web_user["sub_name"] or sub_id)
        tariff = "premium" if _is_active_premium(web_user) or _is_active_premium(tg_user) else (
            (tg_user and tg_user["tariff"]) or web_user["tariff"] or "free"
        )
        free_used = max(int(web_user["free_used"] or 0), int((tg_user and tg_user["free_used"]) or 0))
        balance_total = _row_balance(web_user) + _row_balance(tg_user)
        location_pref = _merge_location_pref(
            web_user["location_pref"],
            tg_user and tg_user["location_pref"],
        )

        await update.message.reply_text("Код принят. Объединяю сайт и Telegram в одну подписку...")

        async with BillingDB.subscription_update_lock(f"web_link:{tg_id}"):
            await BillingDB.delete_all_user_clients(
                sub_id=str(web_user["sub_id"]),
                username_clean=str(web_user["username"] or ""),
            )
            if tg_user and tg_user["sub_id"] and str(tg_user["sub_id"]) != str(web_user["sub_id"]):
                await BillingDB.delete_all_user_clients(
                    sub_id=str(tg_user["sub_id"]),
                    username_clean=str(tg_user["username"] or username_clean),
                )

            client_ids, expiry_ts = await BillingDB.create_clients_for_user(
                sub_id=sub_id,
                username_clean=username_clean,
                expiry_time_ms=expiry_time_ms,
            )

        if expiry_ts:
            new_exp = datetime.fromtimestamp(
                (expiry_ts / 1000) if expiry_ts > 10_000_000_000 else expiry_ts,
                tz=timezone.utc,
            )

        key_created_at = now_iso()
        key_expires_at = new_exp.strftime("%Y-%m-%dT%H:%M:%SZ")

        db.upsert_after_create(
            tg_id=tg_id,
            tg_name=tg_name,
            username=username_clean,
            sub_id=sub_id,
            sub_name=sub_name,
            key_created_at=key_created_at,
            key_expires_at=key_expires_at,
            client_ids=client_ids,
            tariff=tariff,
            free_used=free_used,
        )
        BillingDB.update_subscription_timestamps(tg_id, key_created_at, key_expires_at)

        with db._connect() as conn:  # type: ignore[attr-defined]
            conn.execute(
                """
                UPDATE users
                SET balance_rub = ?,
                    location_pref = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tg_id = ?
                """,
                (balance_total, location_pref, tg_id),
            )
            from vpn_preferences import merge
            merge(conn,web_tg_id,tg_id)
            conn.execute("DELETE FROM users WHERE tg_id = ?", (web_tg_id,))
            conn.execute(
                """
                UPDATE web_accounts
                SET telegram_bonus_claimed = 1,
                    telegram_bonus_claimed_at = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (now_iso(), rec["account_id"]),
            )
            conn.commit()

        asyncio.create_task(BillingDB.restart_xray_safe())

        sub_link = build_sub_link(sub_id)
        exp_text = (new_exp + timedelta(hours=3)).strftime("%d.%m.%Y %H:%M")
        await update.message.reply_text(
            "Готово. Сайт и Telegram теперь используют одну подписку.\n\n"
            f"Бонус: +{bonus_days} дня.\n"
            f"Имя ключа в панели: <b>{html.escape(username_clean)}</b>\n"
            f"Новый срок: <b>{html.escape(exp_text)}</b> МСК.\n\n"
            "Ссылка для подключения:\n"
            f"<pre>{html.escape(sub_link)}</pre>",
            parse_mode="HTML",
        )

    except Exception as exc:
        logger.exception("Ошибка привязки web-аккаунта через /link")
        await update.message.reply_text(
            "Не удалось завершить привязку. Попробуй создать новый код на сайте "
            "или напиши в поддержку."
        )
