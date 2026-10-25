# handlers/__init__.py
from __future__ import annotations

from functools import wraps

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import ADMIN_ID, db

# === Импорт обработчиков ===
from .start import cmd_start
from .menu import handle_menu_buttons
from .billing import show_tariffs, show_balance, show_topup_info
from .access_key import show_access_key
from .web_link import cmd_link

from .admin import (
    cmd_list,
    cmd_setbal,
    cmd_admin,
    cmd_promo,
    cmd_block,
    cmd_unblock,
)

from .support import (
    cmd_begin,
    cmd_end,
    cmd_webmsg,
    admin_on,
    admin_off,
    support_router,
)

from .broadcast import (
    admin_send,         # /send on|off (всем)
    admin_broadcast,    # /broadcast <tg_id> on | /broadcast off (одному)
    broadcast_capture,  # перехват сообщений админа в режиме рассылки
)


# ===== Общая защита заблокированных пользователей =====
BLOCKED_TEXT = "⛔ Аккаунт заблокирован. Доступ к функциям бота ограничен."


def _is_blocked_user(user) -> bool:
    if not user or user.id == ADMIN_ID:
        return False
    try:
        return bool(db.is_blocked(user.id))
    except Exception:
        return False


async def _reject_blocked(update, context) -> bool:
    if not _is_blocked_user(update.effective_user):
        return False

    query = update.callback_query
    if query:
        try:
            await query.answer(BLOCKED_TEXT, show_alert=True)
        except Exception:
            pass
        return True

    message = update.effective_message
    if message:
        await message.reply_text(BLOCKED_TEXT)
    return True


def _guard_blocked(handler):
    @wraps(handler)
    async def wrapped(update, context):
        if await _reject_blocked(update, context):
            return
        return await handler(update, context)

    return wrapped


# ===== Универсальный обработчик inline-кнопок =====
async def _dispatch_callback(update, context):
    query = update.callback_query
    if not query:
        return

    if await _reject_blocked(update, context):
        return

    try:
        await query.answer()
    except Exception:
        pass

    data = (query.data or "").strip()
    if data.startswith('vpn_mode_'):
        from .vpn_mode import callback
        return await callback(update, context)

    # --- Рефералька ---
    if data.startswith("ref_"):
        from .referral import handle_referral_callback
        return await handle_referral_callback(update, context)

    # --- Локации (Литва / Германия / Автовыбор) ---
    if data.startswith("loc_"):
        from .location import handle_location_callback
        return await handle_location_callback(update, context)

    # --- Тарифы (вкл. продление полного) ---
    if data.startswith("tariff_"):
        from .billing import handle_tariff_callback
        return await handle_tariff_callback(update, context)

    # --- Пополнение баланса ---
    if data.startswith("topup_"):
        from .billing import handle_topup_callback
        return await handle_topup_callback(update, context)

    # --- Инструкции (кнопки Android / iOS / Windows / Mac / TV) ---
    if data.startswith("guide_"):
        from .instructions import handle_instructions_callback
        return await handle_instructions_callback(update, context)

    # --- Фолбэк: неизвестная кнопка ---
    chat_id = query.message.chat.id if query.message else update.effective_user.id
    await context.bot.send_message(
        chat_id,
        f"Эта кнопка пока не подключена: <code>{data}</code>",
        parse_mode="HTML",
    )


# ===== Админ-обёртки к общим командам =====
async def cmd_list_admin(update, context):
    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        return
    await cmd_list(update, context)


async def cmd_setbal_admin(update, context):
    user = update.effective_user
    if not user or user.id != ADMIN_ID:
        return
    await cmd_setbal(update, context)


# ===== Регистрация всех хендлеров =====
def register_handlers(app: Application) -> None:
    # --- Команды для всех пользователей ---
    app.add_handler(CommandHandler("start", _guard_blocked(cmd_start)), group=0)
    app.add_handler(CommandHandler("balance", _guard_blocked(show_balance)), group=0)
    app.add_handler(CommandHandler("tariffs", _guard_blocked(show_tariffs)), group=0)
    app.add_handler(CommandHandler("topup", _guard_blocked(show_topup_info)), group=0)
    app.add_handler(CommandHandler("key", _guard_blocked(show_access_key)), group=0)
    app.add_handler(CommandHandler("link", _guard_blocked(cmd_link)), group=0)

    # --- Поддержка (пользователь) ---
    app.add_handler(CommandHandler("begin", _guard_blocked(cmd_begin)), group=0)
    app.add_handler(CommandHandler("end", _guard_blocked(cmd_end)), group=0)

    # --- Поддержка (админ) ---
    app.add_handler(CommandHandler("on", admin_on, filters=filters.User(ADMIN_ID)), group=0)
    app.add_handler(CommandHandler("off", admin_off, filters=filters.User(ADMIN_ID)), group=0)
    app.add_handler(CommandHandler("webmsg", cmd_webmsg, filters=filters.User(ADMIN_ID)), group=0)

    # --- Рассылки (админ) ---
    app.add_handler(CommandHandler("send", admin_send, filters=filters.User(ADMIN_ID)), group=0)
    app.add_handler(CommandHandler("broadcast", admin_broadcast, filters=filters.User(ADMIN_ID)), group=0)

    # Перехват сообщений админа в режиме рассылки (только НЕ команды)
    app.add_handler(
        MessageHandler(filters.User(ADMIN_ID) & ~filters.COMMAND & filters.ALL, broadcast_capture),
        group=5,
    )

    # --- Админские команды ---
    app.add_handler(CommandHandler("list", cmd_list_admin, filters=filters.User(ADMIN_ID)), group=0)
    app.add_handler(CommandHandler("setbal", cmd_setbal_admin, filters=filters.User(ADMIN_ID)), group=0)
    app.add_handler(CommandHandler("admin", cmd_admin, filters=filters.User(ADMIN_ID)), group=0)
    app.add_handler(CommandHandler("promo", cmd_promo, filters=filters.User(ADMIN_ID)), group=0)

    # ✅ ВАЖНО: блокировка/разблокировка
    app.add_handler(CommandHandler("block", cmd_block, filters=filters.User(ADMIN_ID)), group=0)
    app.add_handler(CommandHandler("unblock", cmd_unblock, filters=filters.User(ADMIN_ID)), group=0)

    # --- Inline-кнопки (общий роутер) ---
    app.add_handler(CallbackQueryHandler(_dispatch_callback), group=0)

    # --- Главное меню (только текст и не команды) ---
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_buttons),
        group=10,
    )

    # --- Роутер поддержки (всё кроме команд) ---
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, support_router),
        group=20,
    )
