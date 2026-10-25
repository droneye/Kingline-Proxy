from telegram import Update
from telegram.ext import ContextTypes

from config import db


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    u = update.effective_user
    lines = [
        f"id={u.id}",
        f"username={u.username}",
        f"full_name={u.full_name}",
    ]
    dbu = db.get_user(u.id)
    if dbu:
        lines.append("--- from db ---")
        for k in (
            "id",
            "tg_name",
            "username",
            "tariff",
            "sub_id",
            "sub_name",
            "key_created_at",
            "key_expires_at",
            "client_id1",
            "client_id2",
            "client_id3",
            "balance_rub",
            "free_used",
        ):
            lines.append(f"{k}={dbu.get(k)}")
    await update.message.reply_text("\n".join(lines))
