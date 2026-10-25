# handlers/referral.py

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import ContextTypes

from referral import referral_service  # наш сервис из папки referral
from config import logger


def _build_referral_text(user_id: int) -> str:
    """
    Текст основного экрана реферальной программы.
    """
    code = referral_service.get_or_create_user_code(user_id)
    inv_info = referral_service.get_invitation_info_for_user(user_id)

    if inv_info is None:
        extra = (
            "👥 Ты ещё не вводил чужой код.\n"
            "Если хочешь, можешь поддержать друга: введи его код, "
            "и когда ты подключишь Premium тариф, ему продлят подписку на 30 дней.\n\n"
        )
    else:
        inviter_tg_id, rewarded = inv_info
        if rewarded:
            extra = (
                "✅ Ты уже использовал реферальный код друга.\n"
                "Бонус за твою покупку ему уже начислен.\n\n"
            )
        else:
            extra = (
                "⏳ Ты уже ввёл чей-то реферальный код.\n"
                "Когда оформите Premium тариф, другу автоматически начислят бонус.\n\n"
            )

    text = (
        "💌 <b>Реферальная программа</b>\n\n"
        f"🔗 Твой код: <pre>{code}</pre>\n\n"
        "Отправь его друзьям — за каждый подключенный Premium тариф по твоему коду "
        "мы продлеваем твою подписку на <b>30 дней</b>.\n\n"
        f"{extra}"
        "Нажми кнопку ниже, чтобы ввести код друга 👇"
    )
    return text


def _main_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура основного экрана рефералки.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📨 Ввести код друга",
                    callback_data="ref_enter_code",
                )
            ]
        ]
    )


def _enter_code_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура экрана ввода кода.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅ Назад",
                    callback_data="ref_back_to_menu",
                )
            ]
        ]
    )


# ===== Публичные функции, которые дергаем из других модулей =====

async def show_referral_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Показывается при нажатии кнопки «Рефералка» в главном меню.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    text = _build_referral_text(user.id)
    kb = _main_keyboard()

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=kb,
    )


async def handle_referral_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработка inline-кнопок:
      - ref_enter_code      — переход на экран ввода кода
      - ref_back_to_menu    — вернуться к основному экрану рефералки
    """
    query = update.callback_query
    if not query:
        return

    await query.answer()
    data = (query.data or "").strip()
    user = query.from_user

    if not user:
        return

    # --- Нажали «Ввести код друга» ---
    if data == "ref_enter_code":
        # включаем режим ожидания реферального кода
        context.user_data["await_ref_code"] = True

        text = (
            "✏️ <b>Ввод реферального кода</b>\n\n"
            "Отправь в ответном сообщении <b>только код</b> друга "
            "(буквы и цифры без пробелов).\n\n"
            "После этого, когда ты подключишь Premium тариф, "
            "ему автоматически продлят подписку на 30 дней 💎."
        )
        kb = _enter_code_keyboard()

        try:
            await query.edit_message_text(
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Не удалось отредактировать сообщение (ref_enter_code): %s", e)
            await query.message.reply_text(
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        return

    # --- Нажали «Назад» ---
    if data == "ref_back_to_menu":
        context.user_data["await_ref_code"] = False

        text = _build_referral_text(user.id)
        kb = _main_keyboard()

        try:
            await query.edit_message_text(
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        except Exception as e:
            logger.warning("Не удалось отредактировать сообщение (ref_back_to_menu): %s", e)
            await query.message.reply_text(
                text=text,
                parse_mode="HTML",
                reply_markup=kb,
            )
        return
