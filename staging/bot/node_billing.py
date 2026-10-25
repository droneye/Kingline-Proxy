"""Owner-only server payment notices over the existing narrow Unix socket."""
from telegram.ext import CallbackQueryHandler, ApplicationHandlerStop
from config import ADMIN_ID
from diagnostics import event
from node_reviews import call, markup


async def deliver(context):
    try:
        result=await call('billing/claim',{})
        for notice in result.get('items',[]):
            if notice.get('chat_id')!=ADMIN_ID:continue
            message=await context.bot.send_message(chat_id=ADMIN_ID,text=notice['text'],parse_mode='HTML',
                reply_markup=markup(notice.get('keyboard')),disable_web_page_preview=True)
            await call('billing/ack',{'id':notice['id'],'message_id':message.message_id})
    except Exception as error:
        event('node_billing_delivery_failed','WARNING',error=type(error).__name__)


async def callback(update,context):
    query=update.callback_query
    if not query:return
    if not update.effective_user or update.effective_user.id!=ADMIN_ID:
        await query.answer('Только для владельца',show_alert=True)
        raise ApplicationHandlerStop
    await query.answer('Сохраняем…')
    try:
        result=await call('billing/callback',{'data':query.data})
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) else ''
        text=('Это напоминание устарело. Используйте кнопку в новом сообщении.' if code=='billing_button_expired'
            else 'Не удалось подтвердить оплату. Попробуйте ещё раз.')
        await context.bot.send_message(chat_id=ADMIN_ID,text=text)
        event('node_billing_callback_failed','WARNING',error=type(error).__name__)
    else:
        try:
            await query.edit_message_text(text=result['message'],parse_mode='HTML',reply_markup=None)
        except Exception:
            # The payment was already confirmed even if an old message cannot
            # be edited. Never tell the owner that the confirmation failed.
            await context.bot.send_message(chat_id=ADMIN_ID,text=result['message'],parse_mode='HTML')
    raise ApplicationHandlerStop


def register(app):
    app.add_handler(CallbackQueryHandler(callback,pattern=r'^kb:'),group=-1)
