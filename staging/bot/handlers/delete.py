from telegram import Update
from telegram.ext import ContextTypes

from config import db, XUI_BASE, XUI_COOKIE, logger
from utils import get_username_clean
from xui import XUIClient


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Удаляет клиентов пользователя во всех инбаундах.
    free_used не сбрасываем — триал второй раз не даём.
    """
    if not update.message:
        return

    user = update.effective_user
    if not user:
        return

    tg_id = user.id
    username_clean_current = get_username_clean(update)

    stored_sub_id = None
    stored_username = None

    stored = db.get_sub(tg_id)
    if stored:
        stored_sub_id, stored_username = stored

    if not stored_sub_id:
        stored_sub_id = username_clean_current.lower()

    waiting = await update.message.reply_text(
        "🧹 Ищу и удаляю твоих клиентов во всех инбаундах..."
    )

    try:
        async with XUIClient(XUI_BASE, XUI_COOKIE, logger_=logger) as xc:
            all_inbounds = await xc.list_inbounds()
            if not all_inbounds:
                raise RuntimeError("Не найдено ни одного инбаунда в панели.")

            total_deleted = 0

            for ib in all_inbounds:
                deleted = await xc.delete_user_clients(
                    inbound=ib,
                    username_clean=username_clean_current,
                    sub_id=stored_sub_id,
                )
                total_deleted += int(deleted or 0)

            if stored_username and stored_username != username_clean_current:
                for ib in all_inbounds:
                    deleted = await xc.delete_user_clients(
                        inbound=ib,
                        username_clean=stored_username,
                        sub_id=stored_sub_id,
                    )
                    total_deleted += int(deleted or 0)

        if total_deleted > 0:
            msg = (
                f"✅ Удалено {total_deleted} клиентов, связанных с твоей подпиской.\n"
                "Теперь можешь нажать /create, если у тебя есть активный доступ."
            )
        else:
            msg = (
                "ℹ️ Клиенты с твоим SubId или именем не найдены ни в одном инбаунде.\n"
                "Можешь использовать /create, если у тебя есть активный доступ."
            )

        await waiting.edit_text(msg)

    except Exception as e:
        logger.exception("Ошибка при /delete")
        await waiting.edit_text(f"❌ Не удалось удалить клиентов: {e}")
