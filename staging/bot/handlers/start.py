# handlers/start.py
from html import escape
from telegram import Update
from telegram.ext import ContextTypes

from .menu import MAIN_MENU_KEYBOARD


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    args = context.args or []
    payload = (args[0] if args else "").strip()
    if payload.lower().startswith("link_"):
        from .web_link import cmd_link

        context.args = [payload[5:]]
        return await cmd_link(update, context)

    user = update.effective_user
    name = user.first_name if user and user.first_name else "друг"

    text = (
        f"Привет, {escape(name)}! 👋\n\n"
        "Добро пожаловать в <b>Kingline VPN</b> — одна подписка для телефона и компьютера.\n\n"
        "<b>Начать просто:</b>\n"
        "① Откройте <b>«Тарифы»</b>: попробуйте VPN бесплатно на 24 часа или выберите Premium на 30 дней.\n"
        "② В разделе <b>«Ключ»</b> скопируйте свою ссылку.\n"
        "③ Нажмите <b>«Инструкция»</b> — поможем установить приложение и подключиться.\n\n"
        "Приложение само выберет сервер через «Умный выбор». Страну можно выбрать и вручную.\n\n"
        "В разделе <b>«Мой кабинет»</b> удобно следить за доступом и балансом. Если что-то не получается — нажмите <b>«Поддержка»</b> в меню."
    )

    await update.message.reply_text(
        text,
        reply_markup=MAIN_MENU_KEYBOARD,
        parse_mode="HTML",
    )
