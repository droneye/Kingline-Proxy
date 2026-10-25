"""Payment reminders, without sending anything to Telegram."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

fixture=tempfile.TemporaryDirectory()
os.environ['DB_PATH']=str(Path(fixture.name)/'test.db')
os.environ['BOT_TOKEN']='0:fixture-token'
os.environ['ADMIN_ID']='42'
import node_billing
from telegram.ext import ApplicationHandlerStop


class BillingBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_is_owner_only_and_ack_follows_successful_send(self):
        context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=7))))
        items=[{'id':1,'chat_id':43,'text':'must not send'},
            {'id':2,'chat_id':42,'text':'Оплатите сервер','keyboard':[[{'text':'Оплачено','callback_data':'kb:'+'a'*24}]]}]
        call=AsyncMock(side_effect=[{'items':items},{'ok':True}])
        with patch.object(node_billing,'call',call):await node_billing.deliver(context)
        context.bot.send_message.assert_awaited_once()
        self.assertEqual(context.bot.send_message.call_args.kwargs['chat_id'],42)
        self.assertEqual(call.await_args_list[-1].args,('billing/ack',{'id':2,'message_id':7}))

    async def test_failed_telegram_delivery_does_not_acknowledge_notice(self):
        context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError('offline'))))
        call=AsyncMock(return_value={'items':[{'id':1,'chat_id':42,'text':'Оплатите сервер'}]})
        with patch.object(node_billing,'call',call):await node_billing.deliver(context)
        self.assertEqual(call.await_count,1)

    async def test_other_user_cannot_confirm_even_with_valid_token(self):
        query=SimpleNamespace(answer=AsyncMock(),data='kb:'+'a'*24,edit_message_text=AsyncMock())
        update=SimpleNamespace(effective_user=SimpleNamespace(id=43),callback_query=query)
        with patch.object(node_billing,'call',AsyncMock()) as call:
            with self.assertRaises(ApplicationHandlerStop):await node_billing.callback(update,None)
            call.assert_not_awaited()

    async def test_confirmation_edits_message_and_removes_button(self):
        query=SimpleNamespace(answer=AsyncMock(),data='kb:'+'a'*24,edit_message_text=AsyncMock())
        update=SimpleNamespace(effective_user=SimpleNamespace(id=42),callback_query=query)
        context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(node_billing,'call',AsyncMock(return_value={'message':'✅ Оплата отмечена'})) as call:
            with self.assertRaises(ApplicationHandlerStop):await node_billing.callback(update,context)
            call.assert_awaited_once_with('billing/callback',{'data':query.data})
        query.edit_message_text.assert_awaited_once_with(text='✅ Оплата отмечена',parse_mode='HTML',reply_markup=None)
        context.bot.send_message.assert_not_awaited()

    async def test_uneditable_old_message_does_not_claim_confirmation_failed(self):
        query=SimpleNamespace(answer=AsyncMock(),data='kb:'+'a'*24,edit_message_text=AsyncMock(side_effect=RuntimeError('cannot edit')))
        update=SimpleNamespace(effective_user=SimpleNamespace(id=42),callback_query=query)
        context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(node_billing,'call',AsyncMock(return_value={'message':'✅ Оплата отмечена'})):
            with self.assertRaises(ApplicationHandlerStop):await node_billing.callback(update,context)
        context.bot.send_message.assert_awaited_once_with(chat_id=42,text='✅ Оплата отмечена',parse_mode='HTML')

if __name__=='__main__':unittest.main()
