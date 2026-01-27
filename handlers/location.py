# handlers/location.py

from __future__ import annotations

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from config import db  # SubscriptionDB / обёртка над vpn_bot.db

# Коды локаций, которые храним в БД:
LOCATION_AUTO = "auto"
LOCATION_LT = "lt"
LOCATION_NL = "nl"


def _location_label(code: str) -> str:
    """Человеческая подпись для кода локации."""
    if code == LOCATION_LT:
        return "🇱🇹 Литва"
    if code == LOCATION_NL:
        return "🇳🇱 Нидерланды"
    return "🤖 Автовыбор"


def _build_location_text_and_keyboard(
    current: str,
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Строим красивый текст + inline-клавиатуру с галочками возле выбранной локации.
    Автовыбор — по умолчанию и первой строкой.
    """
    if current not in (LOCATION_AUTO, LOCATION_LT, LOCATION_NL):
        current = LOCATION_AUTO

    current_label = _location_label(current)

    text = (
        f"🌍 <b>Текущая локация: {current_label}</b>\n\n"
        "После смены локации <b>переподключите Proxy</b>, "
        "чтобы обновить местоположение.\n\n"
        "Выберите желаемую локацию:\n\n"
        "<i>Изменения вступят в силу в течении минуты.</i>"
    )

    def mark(code: str) -> str:
        return "✅" if code == current else "▫️"

    # Автовыбор — САМАЯ ВЕРХНЯЯ строка
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{mark(LOCATION_AUTO)} 🤖 Автовыбор",
                    callback_data="loc_auto",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{mark(LOCATION_LT)} 🇱🇹 Литва",
                    callback_data="loc_lt",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{mark(LOCATION_NL)} 🇳🇱 Нидерланды",
                    callback_data="loc_nl",
                )
            ],
        ]
    )

    return text, keyboard


async def show_location_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Показывает меню выбора локации.
    Вызывается из handlers/menu.py при нажатии на кнопку «Локации».
    ЕСЛИ тариф не premium — блокируем и предлагаем выбрать тариф.
    """
    user = update.effective_user
    if not user or not update.message:
        return

    tg_id = user.id

    # Проверяем тариф пользователя
    info = db.get_user(tg_id) or {}
    tariff = (info.get("tariff") or "").lower()

    if tariff != "premium":
        # Нет полного тарифа — показываем заглушку + кнопку "Выбрать тариф"
        text = (
            "🌍 Выбор конкретной локации доступен только для тарифа "
            "<b>💎 Premium</b>.\n\n"
            "С полным тарифом ты сможешь переключаться между серверами:\n"
            "• 🇱🇹 Литва\n"
            "• 🇳🇱 Нидерланды\n"
            "• 🤖 Автовыбор\n\n"
            "Подключи Premium тариф, чтобы разблокировать выбор локации."
        )

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="💎 Выбрать тариф",
                        callback_data="tariff_menu",  # обработает TariffService.handle_tariff_callback
                    )
                ]
            ]
        )

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # Есть Premium тариф — показываем обычное меню локаций
    current = db.get_location_pref(tg_id)
    text, keyboard = _build_location_text_and_keyboard(current)

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


async def handle_location_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Обработчик inline-кнопок локаций: loc_lt / loc_nl / loc_auto.
    Вызывается из _dispatch_callback в handlers/__init__.py.
    """
    query = update.callback_query
    if not query:
        return

    user = query.from_user
    if not user:
        await query.answer()
        return

    tg_id = user.id
    data = (query.data or "").strip()

    # На всякий случай: если вдруг тариф уже не premium, блокируем изменение
    info = db.get_user(tg_id) or {}
    tariff = (info.get("tariff") or "").lower()
    if tariff != "premium":
        await query.answer("Выбор локации доступен только для тарифа 💎 Premium", show_alert=True)
        return

    if data == "loc_lt":
        new_loc = LOCATION_LT
        toast = "Локация изменена: 🇱🇹 Литва"
    elif data == "loc_nl":
        new_loc = LOCATION_NL
        toast = "Локация изменена: 🇳🇱 Нидерланды"
    else:
        new_loc = LOCATION_AUTO
        toast = "Локация изменена: 🤖 Автовыбор"

    # Сохраняем в БД
    try:
        db.set_location_pref(tg_id, new_loc)
    except Exception:
        await query.answer("Ошибка при сохранении локации 😔", show_alert=True)
        return

    # Перестраиваем текст + клавиатуру с новой галочкой
    text, keyboard = _build_location_text_and_keyboard(new_loc)

    if query.message:
        await query.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )

    await query.answer(toast)
