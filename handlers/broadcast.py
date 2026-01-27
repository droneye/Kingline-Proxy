# handlers/broadcast.py
from __future__ import annotations

import asyncio
from typing import List, Tuple, Optional

from telegram import Update
from telegram.ext import ContextTypes
from telegram.ext import ApplicationHandlerStop

from config import ADMIN_ID, logger, db


# ===== Глобальное состояние рассылок =====

# Глобальная рассылка: собираем сообщения, потом шлём всем
SEND_BROADCAST_ACTIVE: bool = False
BROADCAST_QUEUE: List[Tuple[int, int]] = []  # (from_chat_id, message_id)

# Таргет-рассылка одному пользователю
TARGET_BROADCAST_ACTIVE: bool = False
TARGET_BROADCAST_USER_ID: Optional[int] = None
TARGET_BROADCAST_QUEUE: List[Tuple[int, int]] = []  # (from_chat_id, message_id)


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
        target_ids = [int(u["tg_id"]) for u in users if u.get("tg_id")]

        sent = 0
        failed = 0

        await update.message.reply_text(
            f"🚀 Запускаю рассылку: {len(BROADCAST_QUEUE)} сообщение(й) × {len(target_ids)} получателей…"
        )

        for (from_chat_id, message_id) in BROADCAST_QUEUE:
            for uid in target_ids:
                if uid == ADMIN_ID:
                    continue
                try:
                    await context.bot.copy_message(
                        chat_id=uid,
                        from_chat_id=from_chat_id,
                        message_id=message_id,
                    )
                    sent += 1
                except Exception as e:
                    failed += 1
                    logger.warning("Рассылка: не удалось отправить %s: %s", uid, e)
                await asyncio.sleep(0.05)

        BROADCAST_QUEUE = []

        await update.message.reply_text(
            f"✅ Рассылка завершена.\n"
            f"Отправлено: {sent}\n"
            f"Ошибок: {failed}"
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
        BROADCAST_QUEUE.append((msg.chat.id, msg.message_id))
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
