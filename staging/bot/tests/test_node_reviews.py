"""Notification boundaries, without Telegram requests or business databases."""
import os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
fixture=tempfile.TemporaryDirectory()
os.environ['DB_PATH']=str(Path(fixture.name)/'test.db')
os.environ['BOT_TOKEN']='0:fixture-token'
os.environ['ADMIN_ID']='42'
import node_reviews
from telegram.ext import ApplicationHandlerStop

class ReviewBotTests(unittest.IsolatedAsyncioTestCase):
    async def test_delivery_sends_only_to_owner_and_acknowledges_after_success(self):
        context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=7))))
        items=[{'id':1,'chat_id':43,'text':'must not send'},{'id':2,'chat_id':42,'text':'ВАЖНО','keyboard':[[{'text':'Проверить','callback_data':'kr:'+'a'*24}]]}]
        call=AsyncMock(side_effect=[{'items':items},{'ok':True}])
        with patch.object(node_reviews,'call',call):await node_reviews.deliver(context)
        context.bot.send_message.assert_awaited_once();self.assertEqual(context.bot.send_message.call_args.kwargs['chat_id'],42)
        self.assertEqual(call.await_args_list[-1].args,('ack',{'id':2,'message_id':7}))
    async def test_failed_send_is_not_acknowledged(self):
        context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock(side_effect=RuntimeError('offline'))))
        call=AsyncMock(return_value={'items':[{'id':1,'chat_id':42,'text':'ВАЖНО'}]})
        with patch.object(node_reviews,'call',call):await node_reviews.deliver(context)
        self.assertEqual(call.await_count,1)
    async def test_other_telegram_user_cannot_trigger_server_action(self):
        update=SimpleNamespace(effective_user=SimpleNamespace(id=43),callback_query=SimpleNamespace(answer=AsyncMock(),data='kr:'+'a'*24))
        context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(node_reviews,'call',AsyncMock()) as call:
            with self.assertRaises(ApplicationHandlerStop):await node_reviews.callback(update,context)
            call.assert_not_awaited()
    async def test_owner_callback_forwards_opaque_token_and_stops_legacy_dispatch(self):
        update=SimpleNamespace(effective_user=SimpleNamespace(id=42),callback_query=SimpleNamespace(answer=AsyncMock(),data='kr:'+'a'*24))
        context=SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(node_reviews,'call',AsyncMock(return_value={'message':'Проверка поставлена'})) as call:
            with self.assertRaises(ApplicationHandlerStop):await node_reviews.callback(update,context)
            call.assert_awaited_once_with('callback',{'data':update.callback_query.data})
        context.bot.send_message.assert_awaited_once()

if __name__=='__main__':unittest.main()
