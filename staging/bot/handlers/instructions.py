"""Editable onboarding with a shared, platform-specific catalogue of clients."""
import json
from pathlib import Path
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

CATALOGUE = json.loads((Path(__file__).resolve().parents[1] / 'client_apps.json').read_text())
APPS = CATALOGUE['apps']
PLATFORMS = {d['id']: d for d in CATALOGUE['devices']}
DEVICES = {key: (d['label'], APPS[d['apps'][0]]['name'], APPS[d['apps'][0]]['downloads'][key]) for key, d in PLATFORMS.items()}
MENU_TEXT = '📱 <b>Подключим VPN за три шага</b>\n\nВыберите своё устройство.'


def _menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton('iPhone / iPad', callback_data='guide_ios'), InlineKeyboardButton('Android', callback_data='guide_android')],
        [InlineKeyboardButton('Windows', callback_data='guide_windows'), InlineKeyboardButton('Mac', callback_data='guide_mac')],
        [InlineKeyboardButton('Android TV', callback_data='guide_tv')],
        [InlineKeyboardButton('Служба поддержки', callback_data='guide_support')],
    ])


async def show_instructions_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    options = dict(parse_mode='HTML', reply_markup=_menu_keyboard(), disable_web_page_preview=True)
    if update.callback_query:
        await update.callback_query.edit_message_text(MENU_TEXT, **options)
    elif update.message:
        await update.message.reply_text(MENU_TEXT, **options)


def guide_screen(device, step, client=None):
    platform = PLATFORMS[device]
    client = client if client in platform['apps'] else platform['apps'][0]
    app = APPS[client]
    app_name = app['name']
    paragraphs = [
        f'Установите <b>{app_name}</b> по кнопке ниже. Затем вернитесь сюда.',
        f'В меню бота откройте <b>«Ключ»</b> и скопируйте ссылку.\n\nВ <b>{app_name}</b>: {app["import"]}\n\nОдна подписка — до 3 устройств.',
        'Выберите <b>«Умный выбор»</b> и включите подключение. Разрешите добавление VPN, если устройство попросит.\n\nГотово! Страну можно сменить в приложении.',
    ]
    if 'v2raytun' in platform['apps']:
        alternatives = 'Happ'
        paragraphs[0] += f'\n\nНет v2RayTun в вашем регионе? Выберите {alternatives}. Ссылка подписки подойдёт та же.'
    if client == 'happ' and device in ('ios', 'mac'):
        paragraphs[0] += '\nНа сайте Happ выберите своё устройство и регион App Store.'
    if device == 'tv':
        paragraphs[0] = f'Найдите <b>{app_name}</b> в Google Play на телевизоре. Нет v2RayTun? Выберите Happ ниже.'
        paragraphs[1] = 'Скопируйте ссылку из <b>«Ключ»</b>. На телевизоре откройте добавление подписки и следуйте подсказкам для QR-кода или введите ссылку вручную.'
    if client == 'v2rayn':
        paragraphs[2] = 'Выберите <b>«Умный выбор»</b> активным сервером и включите <b>режим TUN</b> для всего компьютера. При запросе разрешите запуск с правами администратора.\n\nГотово! Страну можно сменить в приложении.'
    names = ['Установить приложение', 'Добавить подписку', 'Включить VPN']
    text = f'<b>{platform["label"]} · {step + 1} из 3 · {app_name}</b>\n\n<b>{names[step]}</b>\n{paragraphs[step]}'
    buttons = []
    callback = lambda number: f'guide_{device}_{number}_{client}'
    if step == 0:
        buttons.append([InlineKeyboardButton(('✓ ' if key == client else '') + APPS[key]['name'], callback_data='guide_noop' if key == client else f'guide_{device}_0_{key}') for key in platform['apps']])
        buttons += [[InlineKeyboardButton(f'Скачать {app_name} ↗', url=app['downloads'][device])]]
        if client == 'happ' and device == 'android':
            buttons.append([InlineKeyboardButton('Нет в Google Play? Скачать APK', url='https://www.happ.su/main/ru')])
        buttons.append([InlineKeyboardButton('Установлено →', callback_data=callback(1))])
    elif step == 1:
        key_button=InlineKeyboardButton('Открыть мой ключ',callback_data='guide_key')
        buttons += [[key_button], [InlineKeyboardButton('Подписка добавлена →', callback_data=callback(2))]]
    else:
        text += '\n\nНе подключается? Обновите подписку в приложении и попробуйте другую страну.'
    if step > 0:
        buttons.append([InlineKeyboardButton('← Назад', callback_data=callback(step - 1))])
    buttons += [[InlineKeyboardButton('Устройства', callback_data='guide_menu'), InlineKeyboardButton('Служба поддержки', callback_data='guide_support')]]
    return text, InlineKeyboardMarkup(buttons)


async def handle_instructions_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = (query.data or '').split('_')
    if query.data == 'guide_noop':
        return
    if query.data == 'guide_menu':
        return await show_instructions_menu(update, context)
    if query.data == 'guide_support':
        from .support import ACTIVE_SUPPORT_USERS
        ACTIVE_SUPPORT_USERS.add(query.from_user.id)
        return await query.edit_message_text('💬 <b>Служба поддержки</b>\n\nНапишите сюда, что не работает. Можно приложить скриншот. Ответ придёт в этот чат.\n\nВернуться к меню: /end', parse_mode='HTML')
    if query.data == 'guide_key':
        from .access_key import show_access_key
        await show_access_key(update, context)
        return
    if len(data) not in (2, 3, 4) or data[1] not in PLATFORMS:
        return
    try:
        step = int(data[2]) if len(data) >= 3 else 0
    except ValueError:
        return
    client = data[3] if len(data) == 4 else None
    if step not in (0, 1, 2) or (client is not None and client not in PLATFORMS[data[1]]['apps']):
        return
    text, keyboard = guide_screen(data[1], step, client)
    await query.edit_message_text(text, parse_mode='HTML', reply_markup=keyboard, disable_web_page_preview=True)
