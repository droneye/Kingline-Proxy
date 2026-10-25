"""Show the native subscription without querying every legacy panel/client."""
from datetime import datetime, timezone, timedelta
from html import escape
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config import db
from utils import build_sub_link


def key_text(info):
    if info.get('is_blocked'):
        return 'Доступ приостановлен. Напишите в поддержку: /begin'
    if not info.get('sub_id'):
        return 'Сначала активируйте доступ в «Тарифы». После этого здесь появится ваша ссылка.'
    expiry = None
    try:
        expiry = datetime.fromisoformat(str(info.get('subscription_expires_at') or info.get('key_expires_at')).replace('Z', '+00:00'))
        if expiry.tzinfo is None: expiry = expiry.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError): pass
    date = expiry.astimezone(timezone(timedelta(hours=3))).strftime('%d.%m.%Y %H:%M') if expiry else None
    status = f'Доступ до <b>{date}</b> (МСК).' if expiry and expiry > datetime.now(timezone.utc) else 'Доступ закончился. Продлите его в «Тарифы».' if expiry else 'Срок доступа можно проверить в разделе «Мой кабинет».'
    return ('🔑 <b>Ваша ссылка для подключения</b>\n\n'
            + status + '\n\n<pre>' + escape(build_sub_link(str(info['sub_id']))) + '</pre>\n'
            'Нажмите на ссылку, чтобы скопировать. Как добавить её в приложение — по кнопке ниже.')


async def show_access_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or (update.callback_query.message if update.callback_query else None)
    if not message or not update.effective_user: return
    info = db.get_user(update.effective_user.id) or {}
    await message.reply_text(key_text(info), parse_mode='HTML', disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('Как подключить →', callback_data='guide_menu')],
            [InlineKeyboardButton('TCP/RAW или XHTTP', callback_data='vpn_mode_menu')]]))
