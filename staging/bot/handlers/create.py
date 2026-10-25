import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Set, Optional

from telegram import Update
from telegram.ext import ContextTypes

from config import INBOUND_IDS, XUI_BASE, XUI_COOKIE, db, logger
from utils import (
    get_username_clean,
    build_sub_link,
    now_iso,
    extract_clients_from_inbound,
)
from xui import XUIClient


async def cmd_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Универсальный обработчик создания подписки.

    Работает:
      - по старому /create (если вызвать как команду),
      - по нажатию inline-кнопки (CallbackQuery).

    Логика та же:
      - Если новый пользователь: free-триал на 1 час, помечаем free_used=1.
      - Если tariff='free' и free_used=1: больше триал не выдаём.
      - Если tariff='premium': создаём без ограничения (expiryTime=0).
    """

    # Определяем, откуда пришёл вызов: кнопка или команда
    query = getattr(update, "callback_query", None)

    if query:
        # Вызов из inline-кнопки
        await query.answer()  # чтобы убрать "загрузку" на кнопке
        user = query.from_user
        if not user:
            return
        chat_id = query.message.chat.id if query.message else user.id

        async def send(text: str, **kwargs):
            return await context.bot.send_message(chat_id=chat_id, text=text, **kwargs)

    else:
        # Вызов как /create (fallback, можно потом выпилить)
        if not update.message:
            return
        user = update.effective_user
        if not user:
            return
        chat_id = update.message.chat.id

        async def send(text: str, **kwargs):
            return await update.message.reply_text(text, **kwargs)

    tg_id = user.id
    tg_name = user.full_name or ""
    # для match в XUI используем helper (по update; для callback это тоже ок)
    username_clean = get_username_clean(update)

    # Текущее состояние пользователя
    existing = db.get_user(tg_id)
    if existing:
        tariff = (existing.get("tariff") or "free").lower()
        free_used = int(existing.get("free_used") or 0)
    else:
        tariff = "free"
        free_used = 0

    # Уже использовал free-триал
    if tariff == "free" and free_used == 1:
        await send(
            "⚠️ Ты уже использовал бесплатный пробный доступ на 1 час.\n"
            "Для дальнейшего использования обратись к админу для подключения тарифа <b>premium</b>.",
            parse_mode="HTML",
        )
        return

    is_free_trial = (tariff == "free" and free_used == 0)

    # Стабильный sub_id
    sub_id: Optional[str] = None
    if existing and existing.get("sub_id"):
        sub_id = existing["sub_id"]
    if not sub_id:
        sub_id = username_clean.lower()

    try:
        # ===== 1. Создаём клиентов =====
        async with XUIClient(XUI_BASE, XUI_COOKIE, logger_=logger) as xc:
            all_inbounds = await xc.list_inbounds()
            wanted: Set[str] = {str(i) for i in INBOUND_IDS}
            targets = [ib for ib in all_inbounds if str(ib.get("id")) in wanted]
            if not targets:
                raise RuntimeError(f"Не найдено инбаундов с ID {INBOUND_IDS}")

            max_clients = 3
            created = 0

            # Для free-триала: срок на 1 час (в мс)
            if is_free_trial:
                expire_dt = datetime.now(timezone.utc) + timedelta(hours=1)
                expiry_time = int(expire_dt.timestamp() * 1000)
            else:
                expiry_time = 0  # premium — без ограничения, либо задашь сам

            for idx, ib in enumerate(targets, start=1):
                if created >= max_clients:
                    break

                inbound_id = ib.get("id")
                if not inbound_id:
                    continue

                email = f"{username_clean} #{idx}"

                await xc.add_client_append(
                    inbound_id=inbound_id,
                    email=email,
                    flow="xtls-rprx-vision",
                    sub_id=sub_id,
                    expiry_time=expiry_time,
                )
                created += 1

            if created == 0:
                raise RuntimeError(
                    "Не удалось создать ни одного клиента. "
                    "Сначала сделай /delete, затем попробуй ещё раз."
                )

            # перечитываем inbounds для client_id/expiry
            all_inbounds = await xc.list_inbounds()

        # ===== 2. Собираем client_ids и expiryTime =====
        username_norm = username_clean.lower().replace(" ", "")
        sub_norm = sub_id.lower()
        client_ids: List[str] = []
        expiry_ts: Optional[int] = None
        wanted_str: Set[str] = {str(i) for i in INBOUND_IDS}

        def email_owner_norm(email: str) -> str:
            owner = str(email or "").strip().lower()
            if "#" in owner:
                owner = owner.split("#", 1)[0].strip()
            return owner.replace("@", "").replace(" ", "")

        for ib in all_inbounds:
            inbound_id = ib.get("id") or (
                isinstance(ib.get("obj"), dict) and ib["obj"].get("id")
            )
            if inbound_id is None or str(inbound_id) not in wanted_str:
                continue

            clients = extract_clients_from_inbound(ib)
            for c in clients:
                if not isinstance(c, dict):
                    continue

                cid = str(
                    c.get("id") or c.get("uuid") or c.get("clientId") or ""
                ).strip()
                if not cid:
                    continue

                email = str(c.get("email") or "")
                email_norm = email_owner_norm(email)
                c_sub = str(
                    c.get("subId") or c.get("subscription") or ""
                ).lower().strip()

                if (
                    (username_norm and email_norm == username_norm)
                    or (sub_norm and c_sub == sub_norm)
                ):
                    if cid not in client_ids:
                        client_ids.append(cid)
                    c_exp = int(c.get("expiryTime") or 0)
                    if c_exp > 0:
                        expiry_ts = c_exp if expiry_ts is None else min(
                            expiry_ts, c_exp
                        )

        client_ids = client_ids[:3]

        key_created_at = now_iso()
        key_expires_at = None
        if expiry_ts:
            # предполагаем мс
            if expiry_ts > 10_000_000_000:
                dt = datetime.fromtimestamp(expiry_ts / 1000, tz=timezone.utc)
            else:
                dt = datetime.fromtimestamp(expiry_ts, tz=timezone.utc)
            key_expires_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        sub_name = sub_id

        # Обновляем флаги
        new_tariff = tariff
        new_free_used = free_used
        if is_free_trial:
            new_tariff = "free"
            new_free_used = 1

        db.upsert_after_create(
            tg_id=tg_id,
            tg_name=tg_name,
            username=username_clean,
            sub_id=sub_id,
            sub_name=sub_name,
            key_created_at=key_created_at,
            key_expires_at=key_expires_at,
            client_ids=client_ids,
            tariff=new_tariff,
            free_used=new_free_used,
        )

        # ===== 3. Финальный текст =====
        sub_link = build_sub_link(sub_id)
        final_text = (
            "✅ <b>Подписка создана.</b>\n\n\n\n\n"
            f"🔗 <b>Твоя персональная ссылка для подключения:</b>\n"
            f"<code>{sub_link}</code>\n\n"
            "🌍 Чтобы получить доступ к свободному интернету, "
            "скопируй эту ссылку и вставь её в приложение "
            "<b>V2rayTun</b> на <b>iOS</b> или <b>Android</b>."
        )

        # ===== 4. Анимация + перезапуск Xray (как раньше у /create) =====

        async def restart_xray_action():
            try:
                async with XUIClient(XUI_BASE, XUI_COOKIE, logger_=logger) as xc2:
                    await xc2.restart_xray()
                logger.info("Xray успешно перезапущен через API.")
            except Exception as e:
                logger.warning("Не удалось перезапустить Xray: %s", e)

        async def temp_msg(text: str, do=None):
            msg = await send(f"<i>{text}</i>", parse_mode="HTML")
            if do:
                await do()
            await asyncio.sleep(10)
            try:
                await context.bot.delete_message(
                    chat_id=msg.chat.id,
                    message_id=msg.message_id,
                )
            except Exception:
                pass

        await temp_msg("Генерирую ключи...")
        await temp_msg("Создаю туннель...")
        await temp_msg("Совсем скоро всё будет готово...")
        await temp_msg("Осталось совсем чуть-чуть...")
        await temp_msg("Получаем ответ от сервера...", do=restart_xray_action)
        await temp_msg("Ответ получен. У сервера обед...")
        await temp_msg(f"{username_clean}, не переживай — практически у цели...")

        await send(final_text, parse_mode="HTML")

    except Exception as e:
        logger.exception("Ошибка при создании подписки")
        # при callback тоже отвечаем в чат, а не молчим
        if query:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Не удалось создать подписку: {e}",
            )
        else:
            await update.message.reply_text(f"❌ Не удалось создать подписку: {e}")
