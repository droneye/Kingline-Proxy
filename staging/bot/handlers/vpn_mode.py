"""Authenticated Telegram transport preference, shared with the website."""
import asyncio
from telegram import InlineKeyboardButton,InlineKeyboardMarkup
import vpn_preferences


def screen(actor):
    pref=vpn_preferences.read(actor);mode=pref['transport']
    text=('⚙️ <b>Способ подключения</b>\n\n'
          '🍎 <b>VLESS · TCP/RAW</b> — рекомендуем для iOS.\n'
          '🤖 <b>VLESS · XHTTP</b> — рекомендуем попробовать на Android: трафик передаётся через HTTPS.\n\n'
          'Оба варианта зашифрованы. Если есть обрывы — выберите TCP/RAW.\n'
          'После смены обновите прежнюю подписку в VPN-приложении на своих устройствах.')
    rows=[[InlineKeyboardButton(('✓ ' if mode=='raw' else '')+'TCP/RAW · для iOS',callback_data='vpn_mode_raw')]]
    if pref['xhttpAvailable']:rows.append([InlineKeyboardButton(('✓ ' if mode=='xhttp' else '')+'XHTTP · для Android',callback_data='vpn_mode_xhttp')])
    return text,InlineKeyboardMarkup(rows)


async def callback(update,context):
    query=update.callback_query;actor=update.effective_user
    if not query or not actor:return
    data=query.data or ''
    if data not in ('vpn_mode_menu','vpn_mode_raw','vpn_mode_xhttp'):return
    from config import db
    if not db.get_user(actor.id) or db.is_blocked(actor.id):return
    if data!='vpn_mode_menu':
        try:await asyncio.to_thread(vpn_preferences.save,actor.id,data.removeprefix('vpn_mode_'))
        except ValueError:
            await query.message.reply_text('Этот вариант пока недоступен. Попробуйте TCP/RAW.');return
    text,markup=await asyncio.to_thread(screen,actor.id)
    if data=='vpn_mode_menu':await query.message.reply_text(text,parse_mode='HTML',reply_markup=markup)
    else:
        # Avoid Telegram's "message is not modified" error on a repeated choice.
        if query.message.text_html!=text or query.message.reply_markup!=markup:
            await query.edit_message_text(text,parse_mode='HTML',reply_markup=markup)
        await query.message.reply_text('Сохранено. Обновите подписку в VPN-приложении — ссылка та же.')
