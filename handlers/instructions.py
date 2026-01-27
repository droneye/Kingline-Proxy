# handlers/instructions.py
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes


MENU_TEXT = (
    "📘 <b>Как подключить Proxy</b>\n\n"
    "Выбери устройство, для которого нужна инструкция 👇"
)


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📱 Android", callback_data="guide_android"),
                InlineKeyboardButton("🍎 iPhone", callback_data="guide_ios"),
            ],
            [
                InlineKeyboardButton("💻 Windows", callback_data="guide_windows"),
                InlineKeyboardButton("🖥 Mac", callback_data="guide_mac"),
            ],
            [
                InlineKeyboardButton("📺 TV / приставка", callback_data="guide_tv"),
            ],
            [
                InlineKeyboardButton("🆘 Поддержка", callback_data="guide_support"),
            ],
        ]
    )


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅ Вернуться к выбору", callback_data="guide_menu")]]
    )


# ==============================
# Показ главного меню инструкций
# ==============================
async def show_instructions_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Может быть вызвано:
      • из меню бота (update.message)
      • по callback "guide_menu" (update.callback_query)
    В обоих случаях стараемся работать в одном сообщении.
    """
    if update.callback_query:
        q = update.callback_query
        await q.edit_message_text(
            MENU_TEXT,
            parse_mode="HTML",
            reply_markup=_menu_keyboard(),
            disable_web_page_preview=True,
        )
        return

    # вариант через обычную кнопку "Инструкция"
    message = update.message
    if not message:
        return

    await message.reply_text(
        MENU_TEXT,
        parse_mode="HTML",
        reply_markup=_menu_keyboard(),
        disable_web_page_preview=True,
    )


# ==============================
# Обработчик callback'ов
# ==============================
async def handle_instructions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    data = (query.data or "").strip()

    if data == "guide_menu":
        # вернуться к выбору устройств
        return await show_instructions_menu(update, context)
    if data == "guide_android":
        return await _guide_android(query)
    if data == "guide_ios":
        return await _guide_ios(query)
    if data == "guide_windows":
        return await _guide_windows(query)
    if data == "guide_mac":
        return await _guide_mac(query)
    if data == "guide_tv":
        return await _guide_tv(query)
    if data == "guide_support":
        return await _guide_support(query)


# ==============================
# 📱 ANDROID
# ==============================
async def _guide_android(query):
    text = (
        "📱 <b>Android — инструкция по подключению</b>\n\n"
        "1️⃣ Установи <a href=\"https://play.google.com/store/apps/"
        "details?id=com.v2raytun.android&hl=ru\"><b>V2RayTun</b></a> из Google Play.\n"
        "2️⃣ Открой приложение.\n"
        "3️⃣ Нажми <b>“+” → Import from URL</b>.\n"
        "4️⃣ Вставь свою <b>subscription-ссылку</b> из раздела «🔑 Ключ».\n"
        "5️⃣ Сохрани и включи профиль.\n\n"
        "⚠️ Если Proxy перестал работать — снова получи ссылку через «🔑 Ключ» и импортируй заново."
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
        disable_web_page_preview=True,
    )


# ==============================
# 🍎 IOS
# ==============================
async def _guide_ios(query):
    text = (
        "🍎 <b>iPhone — подключение Proxy</b>\n\n"
        "1️⃣ Установи <a href=\"https://apps.apple.com/ru/app/v2raytun/id6476628951\">"
        "<b>V2RayTun</b></a> из App Store.\n"
        "2️⃣ Запусти приложение.\n"
        "3️⃣ Нажми <b>“+ Profile” → Import from URL</b>.\n"
        "4️⃣ Вставь свою <b>subscription-ссылку</b> из «🔑 Ключ».\n"
        "5️⃣ Разреши создание Proxy-профиля и включи его.\n\n"
        "💡 Если соединение пропало — получи новую ссылку через «🔑 Ключ» и импортируй её заново."
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
        disable_web_page_preview=True,
    )


# ==============================
# 💻 WINDOWS
# ==============================
async def _guide_windows(query):
    text = (
        "💻 <b>Windows — как подключить Proxy</b>\n\n"
        "Рекомендуемые приложения:\n"
        "• <a href=\"https://github.com/Happ-proxy/happ-desktop/releases/latest/download/setup-Happ.x86.exe\">"
        "<b>Happ</b></a> — минималистичное и простое.\n"
        "• <a href=\"https://github.com/2dust/v2rayN/releases/download/7.15.6/v2rayN-windows-arm64-desktop.zip\">"
        "<b>V2RayN</b></a> — более продвинутое.\n\n"
        "Общая схема:\n"
        "1️⃣ Установи и открой приложение.\n"
        "2️⃣ Найди пункт <b>Import / Import URL</b>.\n"
        "3️⃣ Вставь свою <b>subscription-ссылку</b> из «🔑 Ключ».\n"
        "4️⃣ Сохрани профиль и включи Proxy.\n\n"
        "⚠️ Если доступ пропал — обнови ссылку через «🔑 Ключ» и снова импортируй её."
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
        disable_web_page_preview=True,
    )


# ==============================
# 🖥 MACOS
# ==============================
async def _guide_mac(query):
    text = (
        "🖥 <b>Mac — подключение Proxy</b>\n\n"
        "Подойдут такие клиенты:\n"
        "• <a href=\"https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973\">"
        "<b>Happ (macOS)</b></a>\n"
        "• <a href=\"https://github.com/2dust/v2rayN/releases/download/7.15.6/v2rayN-macos-arm64.zip\">"
        "<b>V2RayN (macOS)</b></a>\n\n"
        "Шаги:\n"
        "1️⃣ Установи и открой приложение.\n"
        "2️⃣ Выбери <b>Import from URL</b> или похожий пункт.\n"
        "3️⃣ Вставь свою <b>subscription-ссылку</b>.\n"
        "4️⃣ Сохрани и включи профиль.\n\n"
        "💡 Если что-то перестало работать — получи свежую ссылку через «🔑 Ключ» и импортируй её заново."
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
        disable_web_page_preview=True,
    )


# ==============================
# 📺 TV / ANDROID TV / ПРИСТАВКА
# ==============================
async def _guide_tv(query):
    text = (
        "📺 <b>TV / Android TV / приставка</b>\n\n"
        "1️⃣ Открой магазин приложений на приставке или телевизоре.\n"
        "2️⃣ Найди клиент для V2Ray (часто под тем же названием, что и на Android).\n"
        "3️⃣ Установи и открой приложение.\n"
        "4️⃣ Найди пункт <b>Import from URL</b> / «Импорт по ссылке».\n"
        "5️⃣ Вставь свою <b>subscription-ссылку</b> из раздела «🔑 Ключ».\n"
        "6️⃣ Сохрани профиль и включи Proxy.\n\n"
        "Работает на большинстве приставок и Android TV (Mi Box, NVIDIA Shield, Ugoos и др.)."
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
        disable_web_page_preview=True,
    )


# ==============================
# 🆘 ПОДДЕРЖКА
# ==============================
async def _guide_support(query):
    text = (
        "🆘 <b>Поддержка</b>\n\n"
        "Готов помочь 👋\n\n"
        "• Нажми <b>/begin</b> — откроется приватный чат с поддержкой.\n"
        "• Пиши туда сообщения, прикладывай скрины/видео — их увидит специалист.\n"
        "• Когда закончим — нажми <b>/end</b>.\n\n"
        "Ответы придут в тот же чат 🙂"
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=_back_keyboard(),
        disable_web_page_preview=True,
    )
