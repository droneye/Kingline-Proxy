# handlers/support.py
from __future__ import annotations

from typing import Dict, Set, Optional

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_ID, logger, db  # db нужен для проверки is_blocked


# Пользователи с активным диалогом поддержки (запускают /begin, либо админ включает /on)
ACTIVE_SUPPORT_USERS: Set[int] = set()

# Текущий "адресат" для сообщений админа после /on
ADMIN_ACTIVE_TARGET_ID: Optional[int] = None

# Если всё-таки используем пересылку, карта "message_id в чате админа -> tg_id пользователя"
ADMIN_REPLY_MAP: Dict[int, int] = {}


def _user_tag(u) -> str:
    uname = f"@{u.username}" if u and getattr(u, "username", None) else ""
    full = getattr(u, "full_name", None) or "user"
    uid = getattr(u, "id", None)
    return f"{full} {uname} (id={uid})"


def _cleanup_admin_map_for_user(user_id: int) -> None:
    """Чистим все соответствия reply->user, когда сессия закрыта."""
    bad = [mid for mid, uid in ADMIN_REPLY_MAP.items() if uid == user_id]
    for k in bad:
        ADMIN_REPLY_MAP.pop(k, None)


def close_support_session(user_id: int) -> None:
    """
    Жёстко закрыть сессию поддержки (используем из /block).
    Не шлёт сообщений — только чистит состояние.
    """
    global ADMIN_ACTIVE_TARGET_ID
    ACTIVE_SUPPORT_USERS.discard(user_id)
    _cleanup_admin_map_for_user(user_id)
    if ADMIN_ACTIVE_TARGET_ID == user_id:
        ADMIN_ACTIVE_TARGET_ID = None


def _is_blocked(user_id: int) -> bool:
    try:
        return bool(db.is_blocked(user_id))  # type: ignore[attr-defined]
    except Exception:
        # если вдруг db без метода — не ломаем поддержку
        return False


# ====================== Команды пользователя ======================

async def cmd_begin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь начинает чат с поддержкой."""
    if not update.message:
        return
    user = update.effective_user
    if not user:
        return

    uid = user.id

    # ⛔️ Блокировка — не даём открыть поддержку
    if _is_blocked(uid):
        await update.message.reply_text("⛔️ Доступ к поддержке ограничен.")
        return

    if uid in ACTIVE_SUPPORT_USERS:
        await update.message.reply_text(
            "У тебя уже открыт чат с поддержкой.\nПиши сюда свой вопрос 😉\n"
            "Чтобы завершить общение — используй /end."
        )
        return

    ACTIVE_SUPPORT_USERS.add(uid)

    await update.message.reply_text(
        "✉️ Чат с поддержкой открыт.\n\n"
        "Опиши проблему одним сообщением — можно прикрепить скрины/видео.\n"
        "Чтобы завершить общение — отправь /end."
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🟢 Новый диалог с пользователем: {_user_tag(user)}\n"
                f"Чтобы начать отвечать без реплаев — используй: /on {uid}\n"
                f"Чтобы завершить диалог — /off"
            ),
        )
    except Exception as e:
        logger.warning("Не удалось уведомить админа о начале чата: %s", e)


async def cmd_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Пользователь завершает чат с поддержкой."""
    if not update.message:
        return
    user = update.effective_user
    if not user:
        return

    uid = user.id
    if uid in ACTIVE_SUPPORT_USERS:
        ACTIVE_SUPPORT_USERS.discard(uid)
        _cleanup_admin_map_for_user(uid)

        # если админ был привязан к этому пользователю — отцепляем
        global ADMIN_ACTIVE_TARGET_ID
        if ADMIN_ACTIVE_TARGET_ID == uid:
            ADMIN_ACTIVE_TARGET_ID = None

        await update.message.reply_text("🔚 Чат с поддержкой завершён. Спасибо!")
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"🔴 Пользователь завершил диалог: {_user_tag(user)}",
            )
        except Exception:
            pass
    else:
        await update.message.reply_text("У тебя нет активного диалога. Нажми /begin, чтобы начать.")


# ====================== Команды админа: /on и /off ======================

async def admin_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /on <tg_id>  (для админа)
    Подключает оператора к конкретному пользователю:
      - пользователь получает уведомление, что оператор подключился;
      - все последующие сообщения админа отправляются прямо этому пользователю
        (без необходимости отвечать реплаем).
    Также работает, если сделать /on ответом на пересланное сообщение (fallback).
    """
    if not update.message or not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    target_id: Optional[int] = None

    # 1) /on <tg_id>
    if context.args:
        arg = context.args[0]
        if arg and arg.lstrip("-").isdigit():
            target_id = int(arg)

    # 2) /on в reply на скопированное в админ-чат сообщение
    if not target_id and update.message.reply_to_message:
        replied_mid = update.message.reply_to_message.message_id
        target_id = ADMIN_REPLY_MAP.get(replied_mid)

    if not target_id:
        await update.message.reply_text("Укажи пользователя: /on <tg_id> или ответом на его сообщение.")
        return

    # ⛔️ Заблокированному нельзя открывать поддержку
    if _is_blocked(target_id):
        await update.message.reply_text("Этот пользователь заблокирован. Сначала /unblock.")
        return

    # помечаем диалог активным у пользователя и назначаем текущую цель для админа
    ACTIVE_SUPPORT_USERS.add(target_id)
    global ADMIN_ACTIVE_TARGET_ID
    ADMIN_ACTIVE_TARGET_ID = target_id

    # уведомим пользователя
    try:
        admin_name = update.effective_user.full_name or "Оператор"
        await context.bot.send_message(
            chat_id=target_id,
            text=f"👨‍💻 {admin_name} подключился к чату. Можешь описать проблему — постараемся помочь как можно быстрее.",
        )
    except Exception as e:
        logger.exception("Не удалось отправить уведомление пользователю: %s", e)

    await update.message.reply_text(f"Окей, теперь все сообщения пойдут пользователю {target_id}.")


async def admin_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /off  (для админа)
    Завершает активный диалог (если был установлен через /on):
      - пользователь получает уведомление о завершении;
      - диалог помечается закрытым, сообщения больше не уходят.
    """
    if not update.message or not update.effective_user or update.effective_user.id != ADMIN_ID:
        return

    global ADMIN_ACTIVE_TARGET_ID
    tid = ADMIN_ACTIVE_TARGET_ID
    if not tid:
        await update.message.reply_text("Активный адресат не выбран. Используй /on <tg_id>.")
        return

    # закрываем сессию пользователя
    if tid in ACTIVE_SUPPORT_USERS:
        ACTIVE_SUPPORT_USERS.discard(tid)
    _cleanup_admin_map_for_user(tid)

    # уведомление
    try:
        await context.bot.send_message(
            chat_id=tid,
            text="🔚 Чат с поддержкой завершён оператором. Если потребуется помощь — начни новый через /begin.",
        )
    except Exception:
        pass

    ADMIN_ACTIVE_TARGET_ID = None
    await update.message.reply_text("Диалог закрыт. Сообщения больше не отправляются пользователю.")


# ====================== Роутер сообщений ======================

async def support_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Роутер сообщений техподдержки.
    - Если это пользователь с активной сессией → его сообщения копируем админу.
    - Если это админ и выбран адресат через /on → копируем сообщения прямо этому пользователю.
    - Если пользователь заблокирован → поддержку запрещаем.
    - Команды сюда НЕ приходят (в register_handlers стоит ~filters.COMMAND).
    """
    msg = update.message
    if not msg:
        return
    user = update.effective_user
    if not user:
        return

    # ⛔️ Заблокированным запрещаем поддержку
    if user.id != ADMIN_ID and _is_blocked(user.id):
        try:
            await msg.reply_text("⛔️ Доступ к поддержке ограничен.")
        except Exception:
            pass
        return

    # Сообщения пользователя из активной сессии → админу
    if user.id != ADMIN_ID and user.id in ACTIVE_SUPPORT_USERS:
        try:
            copied = await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=msg.chat.id,
                message_id=msg.message_id,
            )
            ADMIN_REPLY_MAP[copied.message_id] = user.id
        except Exception as e:
            logger.exception("Не удалось переслать сообщение в поддержку: %s", e)
        return

    # Сообщения админа → активному адресату (если /on уже выполнен)
    if user.id == ADMIN_ID:
        if ADMIN_ACTIVE_TARGET_ID:
            try:
                await context.bot.copy_message(
                    chat_id=ADMIN_ACTIVE_TARGET_ID,
                    from_chat_id=msg.chat.id,
                    message_id=msg.message_id,
                )
            except Exception as e:
                logger.exception("Не удалось отправить ответ пользователю: %s", e)
        return
