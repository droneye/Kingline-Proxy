"""Locations are native profiles in the shared subscription, selected in the VPN app."""
async def show_location_menu(update, context):
    message = update.effective_message
    if message:
        await message.reply_text("В твоей подписке есть ⚡ Умный выбор, 🇩🇪 Германия и 🇩🇰 Дания. Выбирай профиль прямо в v2rayTun. Умный выбор распределяет соединения между Германией и Данией с учётом остатка трафика. Турция временно исключена из автоматического выбора.")

async def handle_location_callback(update, context):
    if update.callback_query:
        await update.callback_query.answer("Выбери локацию в VPN-приложении", show_alert=True)
