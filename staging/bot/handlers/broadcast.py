# handlers/broadcast.py
from __future__ import annotations

import asyncio
import os
import re
import smtplib
import sqlite3
from storage import connect as connect_sqlite
from dataclasses import dataclass
from email.message import EmailMessage
from html import escape
from typing import List, Tuple, Optional

from telegram import Message, Update
from telegram.ext import ContextTypes
from telegram.ext import ApplicationHandlerStop

from config import ADMIN_ID, logger, db


# ===== Глобальное состояние рассылок =====

BRAND_NAME = "Kingline Proxy"
SITE_URL = os.getenv("KINGLINE_SITE_URL", "https://kingline.online").rstrip("/")
BOT_PUBLIC_URL = os.getenv("KINGLINE_BOT_URL", "https://t.me/kinglinbot").strip()


@dataclass
class BroadcastItem:
    from_chat_id: int
    message_id: int
    plain_text: str
    html_text: str
    kind: str

# Глобальная рассылка: собираем сообщения, потом шлём всем
SEND_BROADCAST_ACTIVE: bool = False
BROADCAST_QUEUE: List[BroadcastItem] = []

# Таргет-рассылка одному пользователю
TARGET_BROADCAST_ACTIVE: bool = False
TARGET_BROADCAST_USER_ID: Optional[int] = None
TARGET_BROADCAST_QUEUE: List[Tuple[int, int]] = []  # (from_chat_id, message_id)


def _message_kind(msg: Message) -> str:
    if msg.text:
        return "Текст"
    if msg.photo:
        return "Фото"
    if msg.video:
        return "Видео"
    if msg.document:
        return "Файл"
    if msg.animation:
        return "Анимация"
    if msg.voice:
        return "Голосовое"
    if msg.audio:
        return "Аудио"
    return "Сообщение"


def _html_lines(text: str) -> str:
    return escape(text or "").replace("\n", "<br>")


def _broadcast_item_from_message(msg: Message) -> BroadcastItem:
    plain = msg.text or msg.caption or ""
    html_text = _html_lines(plain)
    kind = _message_kind(msg)
    if not html_text:
        html_text = (
            "<p style=\"margin:0;color:#756b6f;\">"
            "В Telegram-рассылке было вложение. Откройте бот, чтобы посмотреть его."
            "</p>"
        )
    return BroadcastItem(
        from_chat_id=int(msg.chat.id),
        message_id=int(msg.message_id),
        plain_text=plain,
        html_text=html_text,
        kind=kind,
    )


def _smtp_config():
    smtp_host = os.getenv("SMTP_HOST", "127.0.0.1").strip()
    smtp_from = (
        os.getenv("SMTP_FROM", "").strip()
        or os.getenv("SMTP_USER", "").strip()
        or os.getenv("MAIL_FROM", "").strip()
        or f"{BRAND_NAME} <no-reply@kingline.online>"
    )
    if not smtp_host or not smtp_from:
        raise RuntimeError("mail_not_configured")

    default_port = "25" if smtp_host in {"127.0.0.1", "localhost"} else (
        "465" if os.getenv("SMTP_SSL") == "1" else "587"
    )
    smtp_port = int(os.getenv("SMTP_PORT", default_port))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    use_ssl = os.getenv("SMTP_SSL", "0") == "1"
    default_tls = "0" if smtp_host in {"127.0.0.1", "localhost"} else "1"
    use_tls = os.getenv("SMTP_TLS", default_tls) != "0" and not use_ssl
    return smtp_host, smtp_port, smtp_from, smtp_user, smtp_password, use_ssl, use_tls


def _is_mail_configured() -> bool:
    try:
        _smtp_config()
        return True
    except RuntimeError:
        return False


def _ensure_web_announcement_schema(conn: sqlite3.Connection) -> bool:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'web_accounts'"
    ).fetchone()
    if not exists:
        return False

    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(web_accounts)").fetchall()
    }
    if "email_announcements_enabled" not in columns:
        conn.execute(
            "ALTER TABLE web_accounts ADD COLUMN email_announcements_enabled INTEGER DEFAULT 1"
        )
        conn.commit()
    return True


def _list_email_recipients() -> list[sqlite3.Row]:
    with connect_sqlite(db.path, timeout=20) as conn:  # type: ignore[attr-defined]
        conn.row_factory = sqlite3.Row
        if not _ensure_web_announcement_schema(conn):
            return []
        return conn.execute(
            """
            SELECT id, email, name
            FROM web_accounts
            WHERE email_verified = 1
              AND email IS NOT NULL
              AND TRIM(email) != ''
              AND COALESCE(email_announcements_enabled, 1) = 1
            ORDER BY id ASC
            """
        ).fetchall()


def _email_subject(items: list[BroadcastItem]) -> str:
    for item in items:
        cleaned = re.sub(r"\s+", " ", item.plain_text or "").strip()
        if cleaned:
            return f"{BRAND_NAME}: {cleaned[:68]}"
    return f"{BRAND_NAME}: важное объявление"


def _broadcast_email_html(items: list[BroadcastItem], recipient_name: str = "") -> str:
    logo = f"{SITE_URL}/brand/kingline-proxy-logo.svg"
    greeting = (
        f"Здравствуйте, {escape(recipient_name)}."
        if recipient_name
        else "Здравствуйте."
    )
    blocks = []
    for index, item in enumerate(items, start=1):
        blocks.append(
            '<div style="margin-top:14px;border:1px solid rgba(215,25,47,.14);'
            'border-radius:20px;background:rgba(255,255,255,.62);padding:16px;">'
            f'<div style="color:#9d1021;font-size:12px;font-weight:800;">{escape(item.kind)} #{index}</div>'
            f'<div style="margin-top:8px;color:#231d20;font-size:16px;line-height:1.65;">{item.html_text}</div>'
            "</div>"
        )
    messages_html = "".join(blocks)
    preview = "Важное объявление от Kingline Proxy."
    return f"""<!doctype html>
<html>
  <body style="margin:0;background:#f7f2f3;color:#171113;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
    <div style="display:none;overflow:hidden;line-height:1px;opacity:0;max-height:0;max-width:0;">{preview}</div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:linear-gradient(180deg,#fff7f8,#f1e8eb);padding:28px 12px;">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:580px;">
            <tr>
              <td style="padding:0 0 14px;text-align:center;">
                <img src="{escape(logo, quote=True)}" width="72" height="72" alt="{BRAND_NAME}" style="display:inline-block;width:72px;height:72px;border-radius:22px;box-shadow:0 16px 36px rgba(215,25,47,.18);">
                <div style="margin-top:10px;color:#151113;font-size:18px;font-weight:900;">{BRAND_NAME}</div>
              </td>
            </tr>
            <tr>
              <td style="border:1px solid rgba(255,255,255,.78);border-radius:30px;background:rgba(255,255,255,.78);box-shadow:0 24px 60px rgba(64,28,34,.14);padding:28px;">
                <div style="display:inline-block;padding:7px 11px;border:1px solid rgba(215,25,47,.18);border-radius:999px;background:rgba(215,25,47,.08);color:#9d1021;font-size:12px;font-weight:800;">важное объявление</div>
                <h1 style="margin:16px 0 8px;color:#151113;font-size:30px;line-height:1.08;font-weight:900;">Новое сообщение</h1>
                <p style="margin:0;color:#756b6f;font-size:16px;line-height:1.6;">{greeting} Ниже сообщение из официальной рассылки.</p>
                {messages_html}
                <a href="{escape(BOT_PUBLIC_URL, quote=True)}" style="display:block;margin:22px 0 0;padding:15px 18px;border-radius:18px;background:linear-gradient(135deg,#f04455,#b80f22);color:#fff;font-size:16px;font-weight:800;text-align:center;text-decoration:none;">Открыть Telegram-бота</a>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 6px 0;color:#8b8085;font-size:12px;line-height:1.5;text-align:center;">
                Важные объявления можно выключить в разделе «Мой кабинет».
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def _broadcast_email_text(items: list[BroadcastItem]) -> str:
    parts = [f"Важное объявление {BRAND_NAME}", ""]
    for index, item in enumerate(items, start=1):
        parts.append(f"{item.kind} #{index}")
        parts.append(item.plain_text or "В Telegram-рассылке было вложение.")
        parts.append("")
    parts.append(f"Мой кабинет: {SITE_URL}")
    parts.append(f"Telegram-бот: {BOT_PUBLIC_URL}")
    return "\n".join(parts)


def _send_email(to_email: str, subject: str, text: str, html: str) -> None:
    from staging_safety import capture_mail
    return capture_mail(to_email, subject, text, html)

def _unused_production_send_email(to_email, subject, text, html):
    smtp_host, smtp_port, smtp_from, smtp_user, smtp_password, use_ssl, use_tls = _smtp_config()
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_from
    message["To"] = to_email
    message.set_content(text)
    message.add_alternative(html, subtype="html")

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(smtp_host, smtp_port, timeout=12) as smtp:
        if use_tls:
            smtp.starttls()
        if smtp_user and smtp_password:
            smtp.login(smtp_user, smtp_password)
        smtp.send_message(message)


def send_broadcast_emails(items: list[BroadcastItem]) -> tuple[int, int, str]:
    if not items:
        return 0, 0, "empty"
    recipients = _list_email_recipients()
    if not recipients:
        return 0, 0, "no_recipients"
    if not _is_mail_configured():
        return 0, len(recipients), "mail_not_configured"

    subject = _email_subject(items)
    text = _broadcast_email_text(items)
    sent = 0
    failed = 0
    for recipient in recipients:
        try:
            html = _broadcast_email_html(items, recipient["name"] or "")
            _send_email(recipient["email"], subject, text, html)
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning(
                "Email-рассылка: не удалось отправить account_id=%s email=%s: %s",
                recipient["id"],
                recipient["email"],
                exc,
            )
    return sent, failed, "sent"


# ===== Общая рассылка (/send on|off) =====

async def admin_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /send on  — включить набор рассылки (сообщения админа копятся в очередь)
    /send off — разослать все накопленные сообщения всем пользователям
    """
    if not update.message:
        return
    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        return

    global SEND_BROADCAST_ACTIVE, BROADCAST_QUEUE
    global TARGET_BROADCAST_ACTIVE, TARGET_BROADCAST_USER_ID, TARGET_BROADCAST_QUEUE

    args = (context.args or [])
    cmd = (args[0].lower() if args else "").strip()

    if cmd == "on":
        # выключим таргет-режим, если вдруг был включён
        TARGET_BROADCAST_ACTIVE = False
        TARGET_BROADCAST_USER_ID = None
        TARGET_BROADCAST_QUEUE = []

        SEND_BROADCAST_ACTIVE = True
        BROADCAST_QUEUE = []  # начинаем с чистого листа
        await update.message.reply_text(
            "🟢 Режим массовой рассылки включён.\n"
            "Напиши одно или несколько сообщений (текст/медиа) — они попадут в очередь.\n"
            "Когда закончишь, отправь: /send off\n\n"
            "Примечание: сообщения не отправляются сразу — они уйдут всем только после /send off."
        )
        return

    if cmd == "off":
        if not SEND_BROADCAST_ACTIVE:
            await update.message.reply_text("Режим массовой рассылки уже выключен.")
            return

        SEND_BROADCAST_ACTIVE = False

        if not BROADCAST_QUEUE:
            await update.message.reply_text("Очередь пуста. Нечего рассылать.")
            return

        # Список получателей (все tg_id из users)
        users = db.list_users() or []
        target_ids = [int(u["tg_id"]) for u in users if u.get("tg_id") and int(u["tg_id"]) > 0]

        sent = 0
        failed = 0

        await update.message.reply_text(
            f"🚀 Запускаю рассылку: {len(BROADCAST_QUEUE)} сообщение(й) × {len(target_ids)} получателей…"
        )

        for item in BROADCAST_QUEUE:
            for uid in target_ids:
                if uid == ADMIN_ID:
                    continue
                try:
                    await context.bot.copy_message(
                        chat_id=uid,
                        from_chat_id=item.from_chat_id,
                        message_id=item.message_id,
                    )
                    sent += 1
                except Exception as e:
                    failed += 1
                    logger.warning("Рассылка: не удалось отправить %s: %s", uid, e)
                await asyncio.sleep(0.05)

        try:
            email_sent, email_failed, email_status = await asyncio.to_thread(
                send_broadcast_emails,
                list(BROADCAST_QUEUE),
            )
        except Exception as exc:
            logger.exception("Email-рассылка завершилась ошибкой: %s", exc)
            email_sent, email_failed, email_status = 0, 0, "failed"

        BROADCAST_QUEUE = []

        email_line = f"Почта: отправлено {email_sent}, ошибок {email_failed}"
        if email_status == "mail_not_configured":
            email_line = "Почта: SMTP не настроен, письма не отправлены"
        elif email_status == "no_recipients":
            email_line = "Почта: нет зарегистрированных получателей с включенными важными объявлениями"
        elif email_status == "failed":
            email_line = "Почта: ошибка при подготовке писем"

        await update.message.reply_text(
            f"✅ Рассылка завершена.\n"
            f"Отправлено: {sent}\n"
            f"Ошибок: {failed}\n"
            f"{email_line}"
        )
        return

    await update.message.reply_text(
        "Использование:\n"
        "  /send on  — включить набор массовой рассылки (копить сообщения)\n"
        "  /send off — отправить накопленные всем и выйти из режима"
    )


# ===== Таргет-рассылка одному пользователю (/broadcast <tg_id> on|off) =====

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /broadcast <tg_id> on  — включить таргет-режим для указанного пользователя
    /broadcast off         — отправить накопленные сообщения этому пользователю и выйти
    """
    if not update.message:
        return
    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        return

    global SEND_BROADCAST_ACTIVE, BROADCAST_QUEUE
    global TARGET_BROADCAST_ACTIVE, TARGET_BROADCAST_USER_ID, TARGET_BROADCAST_QUEUE

    args = context.args or []

    # выключение
    if args and args[0].lower() == "off":
        if not TARGET_BROADCAST_ACTIVE or not TARGET_BROADCAST_USER_ID:
            await update.message.reply_text("Таргет-режим уже выключен.")
            return

        target_id = TARGET_BROADCAST_USER_ID
        TARGET_BROADCAST_ACTIVE = False
        TARGET_BROADCAST_USER_ID = None

        if not TARGET_BROADCAST_QUEUE:
            await update.message.reply_text("Очередь пуста. Нечего отправлять.")
            TARGET_BROADCAST_QUEUE = []
            return

        await update.message.reply_text(
            f"🚀 Отправляю {len(TARGET_BROADCAST_QUEUE)} сообщение(й) пользователю {target_id}…"
        )

        sent = 0
        failed = 0

        for (from_chat_id, message_id) in TARGET_BROADCAST_QUEUE:
            try:
                await context.bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                )
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning("Таргет-рассылка: не удалось отправить %s: %s", target_id, e)
            await asyncio.sleep(0.05)

        TARGET_BROADCAST_QUEUE = []

        await update.message.reply_text(
            f"✅ Таргет-рассылка завершена. Отправлено: {sent}, ошибок: {failed}"
        )
        return

    # включение: ожидаем "<tg_id> on"
    if len(args) >= 2 and args[1].lower() == "on":
        tgt_raw = args[0].strip()
        if not tgt_raw.lstrip("-").isdigit():
            await update.message.reply_text("Укажи корректный tg_id: /broadcast <tg_id> on")
            return
        tgt_id = int(tgt_raw)

        # выключим массовую, если включена
        SEND_BROADCAST_ACTIVE = False
        BROADCAST_QUEUE = []

        TARGET_BROADCAST_ACTIVE = True
        TARGET_BROADCAST_USER_ID = tgt_id
        TARGET_BROADCAST_QUEUE = []

        await update.message.reply_text(
            f"🟢 Таргет-режим включён для пользователя {tgt_id}.\n"
            "Теперь отправляй одно или несколько сообщений — они попадут в очередь.\n"
            "Когда закончишь, отправь: /broadcast off"
        )
        return

    # помощь
    await update.message.reply_text(
        "Использование:\n"
        "  /broadcast <tg_id> on  — начать набор сообщений для конкретного пользователя\n"
        "  /broadcast off         — отправить все накопленные и выйти из режима"
    )


# ===== Перехват сообщений админа в режиме рассылки (любой) =====

async def broadcast_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Перехватываем любые сообщения админа в режимах рассылки:
      - если активен массовый режим — копим в BROADCAST_QUEUE
      - если активен таргет-режим — копим в TARGET_BROADCAST_QUEUE
    Ничего никому не шлём до /send off или /broadcast off.
    """
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        return

    global SEND_BROADCAST_ACTIVE, BROADCAST_QUEUE
    global TARGET_BROADCAST_ACTIVE, TARGET_BROADCAST_USER_ID, TARGET_BROADCAST_QUEUE

    # игнор команд, чтобы они не попадали в очередь
    if (msg.text or "").strip().startswith("/"):
        return

    if SEND_BROADCAST_ACTIVE:
        BROADCAST_QUEUE.append(_broadcast_item_from_message(msg))
        try:
            await msg.reply_text(f"Добавлено в массовую рассылку. В очереди: {len(BROADCAST_QUEUE)}")
        except Exception:
            pass
        raise ApplicationHandlerStop

    if TARGET_BROADCAST_ACTIVE and TARGET_BROADCAST_USER_ID:
        TARGET_BROADCAST_QUEUE.append((msg.chat.id, msg.message_id))
        try:
            await msg.reply_text(
                f"Добавлено в таргет-очередь → {TARGET_BROADCAST_USER_ID}. "
                f"Всего: {len(TARGET_BROADCAST_QUEUE)}"
            )
        except Exception:
            pass
        raise ApplicationHandlerStop
