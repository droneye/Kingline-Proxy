# handlers/menu.py
import random

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import ContextTypes

from config import db, ADMIN_ID  # основной users-DB + твой админ ID
from promos_db import promo_db   # новая БД промокодов
from .support import ACTIVE_SUPPORT_USERS, ADMIN_ACTIVE_TARGET_ID  # чтобы не мешать поддержке
from .location import show_location_menu  # показываем меню выбора локаций


# ===== Главное меню =====
# 1-я строка: Баланс | Тарифы | Ключ
# 2-я строкка: Локации | Рефералка | Инструкция
MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["Баланс", "Тарифы", "Ключ"],
        ["Локации", "Рефералка", "Инструкция"],
    ],
    resize_keyboard=True,
)

# ===== Сообщения для заблокированных =====
BLOCKED_PHRASES = [
    "⛔ Ты заблокирован. Доступ закрыт.",
    "🚫 Аккаунт заблокирован. Ничего не получится.",
    "❌ Доступ запрещён. Ты в бане.",
    "⛔ Увы, ты заблокирован. Кнопки больше не работают.",
    "🔒 Доступ ограничен. Аккаунт в блокировке.",
    "🚫 Бан.",
    "⛔ Заблокирован. VPN и функции бота недоступны.",
    "🔐 Тебе сюда нельзя: аккаунт заблокирован.",
    "🚫 Ты отстранён от сервиса. Доступ закрыт.",
    "⛔ Сервис недоступен: блокировка аккаунта.",
    "🛑 Стоп. Ты заблокирован.",
    "🔐 Здесь тебе больше не рады. Ты заблокирован.",
    "⛔ Ты в чёрном списке. Никаких кнопок.",
    "🔒 Блокировка активна. Команды и меню отключены.",
    "🚫 Аккаунт заблокирован. Не нажимай — не поможет 🙂",
    "⛔ Заблокирован. Хорошим людям — доступ, тебе — бан 😈",
    "⚠️ Доступ отключён. Любые попытки — впустую.",
    "⛔ Сервис недоступен. Ты отключён от системы.",
    "🔒 Твоя учётка в блоке. Доступа нет.",
    "🚫 Нет-нет. Ты заблокирован. Всё.",
]


def _blocked_reply_text() -> str:
    return random.choice(BLOCKED_PHRASES)


# ===== Работа с балансом (промокоды и т.п.) =====

def _change_balance(tg_id: int, delta: int) -> int:
    """Локальная утилита пополнения через существующую БД пользователей."""
    with db._connect() as conn:  # type: ignore[attr-defined]
        cur = conn.cursor()
        cur.execute("SELECT balance_rub FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        if row is None:
            new_val = int(delta)
            cur.execute(
                "INSERT INTO users (tg_id, balance_rub) VALUES (?, ?)",
                (tg_id, new_val),
            )
        else:
            current = int(row[0] or 0)
            new_val = current + int(delta)
            cur.execute(
                "UPDATE users SET balance_rub = ?, updated_at = CURRENT_TIMESTAMP WHERE tg_id = ?",
                (new_val, tg_id),
            )
        conn.commit()
        return new_val


def _is_blocked_safe(tg_id: int) -> bool:
    try:
        return bool(db.is_blocked(tg_id))  # type: ignore[attr-defined]
    except Exception:
        return False


# ===== Главное меню: обработка кнопок ReplyKeyboard =====

async def handle_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    if not user:
        return

    # ⛔️ Если пользователь заблокирован — на любое действие отвечаем одной фразой и выходим
    if user.id != ADMIN_ID and _is_blocked_safe(user.id):
        await update.message.reply_text(
            _blocked_reply_text(),
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return

    # 🔒 НЕ МЕШАЕМ поддержке:
    # 1) если у пользователя активна сессия поддержки — ничего не делаем
    if user.id in ACTIVE_SUPPORT_USERS:
        return
    # 2) если это админ и у него выбран адресат через /on — тоже молчим
    if user.id == ADMIN_ID and ADMIN_ACTIVE_TARGET_ID:
        return

    text = (update.message.text or "").strip()

    # === режим ожидания промокода ===
    if context.user_data.get("await_promo"):
        context.user_data["await_promo"] = False

        code = text.strip().upper().replace(" ", "")
        amount = promo_db.redeem(code)

        if amount is None:
            await update.message.reply_text(
                "❌ Промокод недействителен или уже использован.",
                reply_markup=MAIN_MENU_KEYBOARD,
            )
            return

        # зачисляем на баланс
        new_balance = _change_balance(user.id, amount)
        await update.message.reply_text(
            f"✅ Промокод принят: +{amount} ₽\n"
            f"Новый баланс: {new_balance} ₽",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return

    # === режим ожидания ввода реферального кода ===
    if context.user_data.get("await_ref_code"):
        code_candidate = text.strip().upper().replace(" ", "")
        context.user_data["await_ref_code"] = False

        # если пользователь передумал и нажал кнопку меню/команду — не считаем это кодом
        menu_labels = {
            "БАЛАНС",
            "ТАРИФЫ",
            "КЛЮЧ",
            "ЛОКАЦИИ",
            "РЕФЕРАЛКА",
            "ИНСТРУКЦИЯ",
        }

        if code_candidate.startswith("/") or code_candidate in menu_labels:
            # просто пропускаем дальше в обычную обработку
            pass
        else:
            from .referral import referral_service  # импорт здесь, чтобы избежать циклов

            ok, msg = referral_service.register_code_usage(
                invited_tg_id=user.id,
                code_raw=text,
            )
            await update.message.reply_text(
                msg,
                parse_mode="HTML",
                reply_markup=MAIN_MENU_KEYBOARD,
            )
            return

    # === обычные кнопки ===

    if text in ("💰 Баланс", "Баланс"):
        from .billing import show_balance
        return await show_balance(update, context)

    elif text in ("📚 Тарифы", "Тарифы"):
        from .billing import show_tariffs
        return await show_tariffs(update, context)

    elif text in ("🔑 Ключ", "🛰 Ключ доступа", "Ключ"):
        from .access_key import show_access_key
        return await show_access_key(update, context)

    elif text in ("Локации", "🌍 Локации"):
        return await show_location_menu(update, context)

    elif text in ("Рефералка", "Рефералка 💌"):
        from .referral import show_referral_menu
        return await show_referral_menu(update, context)

    elif text in ("Поддержка", "🆘 Поддержка", "❓ Справка"):
        await update.message.reply_text(
            "Готов помочь 👋\n\n"
            "• Нажми /begin — откроется приватный чат с поддержкой.\n"
            "• Пиши сюда сообщения, прикладывай скрины/видео — их увидит специалист.\n"
            "• Когда закончим — нажми /end.\n\n"
            "Ответы придут сюда же.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return

    elif text in ("📖 Инструкция", "Инструкция"):
        from .instructions import show_instructions_menu
        return await show_instructions_menu(update, context)

    elif text in ("💳 Пополнить", "Пополнить"):
        amounts = (300, 600, 900)
        rows = [
            [InlineKeyboardButton(f"➕ {amt} ₽", callback_data=f"topup_amt_{amt}")]
            for amt in amounts
        ]
        rows.append([InlineKeyboardButton("🎟️ Промокод", callback_data="topup_promo")])
        rows.append([InlineKeyboardButton("❌ Не пополнять", callback_data="topup_cancel")])

        await update.message.reply_text(
            "Выбери сумму пополнения 👇",
            reply_markup=ReplyKeyboardRemove(),
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Сколько пополнить?",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    else:
        await update.message.reply_text(
            "Выбери действие на клавиатуре ниже 🙂",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
