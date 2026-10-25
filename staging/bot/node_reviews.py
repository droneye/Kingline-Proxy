"""Owner-only monitoring notices and narrowly scoped server-side action tokens."""
import os
import httpx
from telegram import InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler,ApplicationHandlerStop
from config import ADMIN_ID
from diagnostics import event

ERRORS={'review_route_check_pending':'Сначала дождитесь проверки нового входа.','review_button_expired':'Кнопка устарела. Откройте «Ноды» на сайте.','review_node_changed':'Состав нод изменился. Обновите раздел «Ноды».','review_configuration_changed':'Маршрут уже изменился. Запустите новую проверку в «Нодах».','review_parent_not_healthy':'Этот вход больше не проходит свежую проверку из РФ. Выберите другой.','vpn_operation_busy':'Дождитесь завершения текущей настройки VPN.','ru_installation_busy':'Дождитесь замены российского выхода.','visibility_recovery_incomplete':'Нужно проверить восстановление в технических логах.','review_owner_only':'Действие доступно только владельцу.'}
def markup(rows):return InlineKeyboardMarkup([[InlineKeyboardButton(**button) for button in row] for row in rows]) if rows else None
async def call(path,body):
    transport=httpx.AsyncHTTPTransport(uds=os.environ.get('KINGLINE_REVIEW_SOCKET','/run/node-review/bot.sock'))
    async with httpx.AsyncClient(transport=transport,timeout=15) as client:response=await client.post('http://localhost/'+path,json={'actor_id':ADMIN_ID,**body})
    data=response.json()
    if response.status_code>=400:raise ValueError(data.get('error') or data.get('detail') or 'review_unavailable')
    return data
async def deliver(context):
    try:
        result=await call('claim',{})
        for notice in result.get('items',[]):
            if notice.get('chat_id')!=ADMIN_ID:continue
            message=await context.bot.send_message(chat_id=ADMIN_ID,text=notice['text'],parse_mode='HTML',reply_markup=markup(notice.get('keyboard')),disable_web_page_preview=True)
            await call('ack',{'id':notice['id'],'message_id':message.message_id})
    except Exception as error:event('node_review_delivery_failed','WARNING',error=type(error).__name__)
async def callback(update,context):
    query=update.callback_query
    if not query:return
    if not update.effective_user or update.effective_user.id!=ADMIN_ID:
        await query.answer('Только для владельца',show_alert=True);raise ApplicationHandlerStop
    await query.answer('Обрабатываем…')
    try:
        result=await call('callback',{'data':query.data})
        await context.bot.send_message(chat_id=ADMIN_ID,text=result.get('message','Готово.'),reply_markup=markup(result.get('keyboard')))
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) else 'review_unavailable'
        await context.bot.send_message(chat_id=ADMIN_ID,text=ERRORS.get(code,'Не удалось выполнить действие. Проверьте «Ноды» и технические логи.'))
        event('node_review_callback_failed','WARNING',error=type(error).__name__)
    raise ApplicationHandlerStop

def register(app):app.add_handler(CallbackQueryHandler(callback,pattern=r'^kr:'),group=-1)
