"""Isolated test bot: owner only, no legacy DNS or production payment workers."""
import logging
import os
from telegram import MenuButtonWebApp, WebAppInfo
from telegram.ext import Application
from telegram.request import HTTPXRequest
from telegram.error import Forbidden
from config import BOT_TOKEN, ADMIN_ID
from handlers import register_handlers
from handlers.durable_subscription import request_key, retry_panel_sync
from handlers.subscription_delivery import retry_pending
from diagnostics import configure, event, exception_fields

class OwnerRequest(HTTPXRequest):
    async def do_request(self, url, method, request_data=None, **kwargs):
        params = request_data.parameters if request_data else {}
        for name in ('chat_id', 'from_chat_id', 'user_id'):
            if name in params and str(params[name]) != str(ADMIN_ID):
                raise Forbidden('Staging delivery restricted to the owner')
        return await super().do_request(url, method, request_data=request_data, **kwargs)

class OwnerApplication(Application):
    async def process_update(self, update):
        if not update.effective_user or update.effective_user.id != ADMIN_ID:
            return
        token = request_key.set('telegram:' + str(update.update_id))
        try:
            return await super().process_update(update)
        finally:
            request_key.reset(token)

async def heartbeat(context):
    from pathlib import Path
    Path('/run/kingline/bot-heartbeat').touch()

async def initialize(app):
    await heartbeat(None)
    app.job_queue.run_repeating(heartbeat, interval=30, first=30)
    await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text='Мой кабинет', web_app=WebAppInfo(url=os.environ['KINGLINE_SITE_URL'])))
    app.job_queue.run_repeating(retry_panel_sync, interval=15, first=3)
    app.job_queue.run_repeating(retry_pending, interval=15, first=7)
    app.job_queue.run_repeating(reconcile_test_payments, interval=20, first=10)
    from node_reviews import deliver
    app.job_queue.run_repeating(deliver, interval=15, first=5,job_kwargs={'max_instances':1})
    from node_billing import deliver as deliver_billing
    app.job_queue.run_repeating(deliver_billing,interval=30,first=12,job_kwargs={'max_instances':1})

async def reconcile_test_payments(context):
    from contextlib import closing
    from config import db
    from test_payments import TestPaymentGateway
    from handlers.billing_topup import TopupService
    with closing(db._connect()) as c:
        rows = c.execute("SELECT payment_id,amount FROM payments WHERE payment_id LIKE 'stg_%' AND tg_id=? AND notified_at IS NULL AND status IN ('pending','waiting_for_capture','succeeded') LIMIT 20", (ADMIN_ID,)).fetchall()
    for pid, amount in rows:
        payment = await TestPaymentGateway().get_payment(pid)
        if payment.get('metadata',{}).get('tg_id') != ADMIN_ID or int(float(payment['amount']['value'])) != amount:
            continue
        if payment['status'] == 'succeeded':
            TopupService.credit_succeeded_payment(pid)
            await TopupService._notify_payment_success_once(context=context,payment_id=pid,
                new_balance=__import__('handlers.billing_db',fromlist=['BillingDB']).BillingDB.get_balance(ADMIN_ID))

async def error_handler(update, context):
    fields=exception_fields(context.error);fields.pop('message',None)
    event('telegram_update_failed', 'ERROR', **fields)

if __name__ == '__main__':
    configure('staging-bot')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.WARNING)
    app = Application.builder().application_class(OwnerApplication).token(BOT_TOKEN).request(OwnerRequest()).get_updates_request(OwnerRequest()).post_init(initialize).build()
    register_handlers(app)
    from node_reviews import register
    register(app)
    from node_billing import register as register_billing
    register_billing(app)
    app.add_error_handler(error_handler)
    app.run_polling(drop_pending_updates=False, allowed_updates=['message', 'callback_query'])
