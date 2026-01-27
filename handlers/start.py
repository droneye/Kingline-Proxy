# handlers/start.py
from telegram import Update
from telegram.ext import ContextTypes

from .menu import MAIN_MENU_KEYBOARD


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user = update.effective_user
    name = user.first_name if user and user.first_name else "друг"

    text = (
    f"Привет, {name}! 👋\n\n"
    "<b>Добро пожаловать в Kingline Proxy</b> — сервис с максимально стабильным и быстрым доступом к интернету.\n\n"
    "⚡ Мы используем собственные гигабитные серверы, обеспечивая высокую скорость подключения.\n"
    "🛡 Каждый сервер проходит многоуровневую защиту для безопасной и анонимной работы.\n\n"
    "Чтобы начать пользоваться прокси, выбери подходящий тариф в разделе <b>«Тарифы»</b> и подключись к выбранной локации.\n\n"
    "<b>Нажми кнопку ниже — подключение займет меньше минуты 🚀</b>"
)


    await update.message.reply_text(
        text,
        reply_markup=MAIN_MENU_KEYBOARD,
        parse_mode="HTML",
    )
