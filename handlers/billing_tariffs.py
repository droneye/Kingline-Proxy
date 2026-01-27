# handlers/billing_tariffs.py
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import ContextTypes

from config import (
    db,
    FULL_TARIFF_PRICE,
    logger,
)
from utils import (
    get_username_clean,
    build_sub_link,
    now_iso,
)
from .billing_db import BillingDB
from .subscription_notifier import SubscriptionNotifier
from referral import referral_service


class TariffService:
    """
    Логика тарифов и подписок с защитой от перезаписи premium подписки тестовой
    """

    # ===== ВНУТРЕННИЕ ПОМОЩНИКИ =====

    @staticmethod
    def _has_active_premium_subscription(existing: dict) -> bool:
        """
        Проверяет, есть ли у пользователя активная premium подписка.
        """
        if not existing:
            return False
            
        tariff = existing.get("tariff", "").lower()
        if tariff != "premium":
            return False
            
        key_expires_at = existing.get("key_expires_at")
        if not key_expires_at:
            return False
            
        try:
            expires_dt = datetime.strptime(key_expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return expires_dt > now
        except Exception:
            return False

    @staticmethod
    def _get_remaining_subscription_days(existing: dict) -> Optional[int]:
        """
        Возвращает количество оставшихся дней подписки.
        """
        if not existing:
            return None
            
        key_expires_at = existing.get("key_expires_at")
        if not key_expires_at:
            return None
            
        try:
            expires_dt = datetime.strptime(key_expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if expires_dt <= now:
                return 0
            remaining = expires_dt - now
            return max(0, remaining.days)
        except Exception:
            return None

    @staticmethod
    def _tariffs_text() -> str:
        return (
            "📚 <b>Доступные тарифы:</b>\n\n"
            "🧪<b> Trial</b> — бесплатно.\n"
            "      Доступ для тестирования сервиса на 1 час.\n\n"
            f"💎<b> Premium</b> — {FULL_TARIFF_PRICE} ₽.\n"
            "      30 дней доступа с высоким приоритетом, безграничной скоростью на 3 устройства и возможностью выбора локаций.\n\n"
            "Выбери тариф, который хочешь активировать 👇"
        )

    @staticmethod
    def _tariffs_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🧪 Trial", callback_data="tariff_test")],
                [InlineKeyboardButton("💎 Premium", callback_data="tariff_full")],
            ]
        )

    # ===== ВСПОМОГАТЕЛЬНЫЕ ЭКРАНЫ/АНИМАЦИЯ =====

    @staticmethod
    async def _animated_sequence(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        async def temp_msg(text: str):
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"<i>{text}</i>",
                parse_mode="HTML",
            )
            await asyncio.sleep(3)
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg.message_id)
            except Exception:
                pass

        await temp_msg("Генерирую ключи...")
        await temp_msg("Создаю туннель...")
        await temp_msg("Совсем скоро всё будет готово...")
        await temp_msg("Осталось совсем чуть-чуть...")

    @staticmethod
    async def _send_instruction_cta(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📘 Как подключить Proxy", callback_data="guide_menu")]]
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text="Если не знаешь, как подключиться — нажми кнопку ниже 👇",
            reply_markup=kb,
            parse_mode="HTML",
        )

    # ===== ПУБЛИЧНЫЙ ЭКРАН С ТАРИФАМИ =====

    @staticmethod
    async def show_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return

        text = TariffService._tariffs_text()
        kb = TariffService._tariffs_keyboard()

        await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)

    # ===== ОСНОВНОЙ CALLBACK ДЛЯ ТАРИФОВ =====

    @staticmethod
    async def handle_tariff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not query:
            return

        await query.answer()
        data = (query.data or "").strip()

        user = query.from_user
        chat_id = query.message.chat.id if query.message else user.id

        tg_id = user.id
        tg_name = user.full_name or ""
        username_clean = (user.username or f"user{tg_id}").replace("@", "").replace(" ", "")

        BillingDB.ensure_user_row(tg_id, tg_name, username_clean)
        existing = db.get_user(tg_id) or {}
        had_sub_before = bool(existing.get("sub_id"))

        # Для "tariff_test", "tariff_back" и "tariff_menu" мы НЕ удаляем сообщение
        delete_menu = data not in ("tariff_test", "tariff_back", "tariff_menu")

        if delete_menu and query.message:
            try:
                await query.message.delete()
            except Exception:
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass

        # ---------- ОТКРЫТЬ МЕНЮ ТАРИФОВ (tariff_menu) ----------

        if data == "tariff_menu":
            text = TariffService._tariffs_text()
            kb = TariffService._tariffs_keyboard()

            if query.message:
                try:
                    await query.message.edit_text(
                        text=text,
                        parse_mode="HTML",
                        reply_markup=kb,
                    )
                except Exception as e:
                    logger.warning("Не удалось отредактировать сообщение в меню тарифов: %s", e)
                    await context.bot.send_message(
                        chat_id=chat_id,
                        parse_mode="HTML",
                        text=text,
                        reply_markup=kb,
                    )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    parse_mode="HTML",
                    text=text,
                    reply_markup=kb,
                )
            return

        # ---------- ТЕСТОВЫЙ: ПОКАЗ ПОДТВЕРЖДЕНИЯ ----------

        if data == "tariff_test":
            free_used = int(existing.get("free_used") or 0)

            # ЗАЩИТА: проверяем, есть ли активная premium подписка
            if TariffService._has_active_premium_subscription(existing):
                remaining_days = TariffService._get_remaining_subscription_days(existing)
                if remaining_days and remaining_days > 0:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        parse_mode="HTML",
                        text=(
                            "⚠️ <b>У тебя уже есть активная Premium подписка!</b>\n\n"
                            f"⏳ Осталось дней: <b>{remaining_days}</b>\n\n"
                            "Тестовый тариф активировать <b>нельзя</b>, так как он перезапишет "
                            "твою текущую подписку и сократит срок до 1 часа.\n\n"
                            "Если хочешь продлить подписку — нажми «💎 Premium»"
                        ),
                    )
                    return

            if free_used == 1:
                await context.bot.send_message(
                    chat_id=chat_id,
                    parse_mode="HTML",
                    text=(
                        "⚠️ Ты уже использовал бесплатный пробный доступ на 1 час.\n"
                        "Для дальнейшего использования подключи тариф <b>Premium</b>."
                    ),
                )
                return

            # Меняем текущее сообщение с тарифами на подтверждающее
            text = (
                "🧪 <b>Пробный доступ</b>\n\n"
                "Ты получишь <b>1 час</b> бесплатного пробного доступа к серверу.\n\n"
                "Активировать пробный период сейчас?"
            )

            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Да, хочу протестировать", callback_data="tariff_test_confirm"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ Вернуться к выбору тарифов", callback_data="tariff_back"
                        )
                    ],
                ]
            )

            try:
                await query.edit_message_text(
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception as e:
                logger.warning("Не удалось отредактировать сообщение с тарифами: %s", e)
            return

        # ---------- ВОЗВРАТ К МЕНЮ ТАРИФОВ ----------

        if data == "tariff_back":
            text = TariffService._tariffs_text()
            kb = TariffService._tariffs_keyboard()
            try:
                await query.edit_message_text(
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception as e:
                logger.warning("Не удалось вернуть меню тарифов: %s", e)
            return

        # ---------- ТЕСТОВЫЙ: ПОДТВЕРЖДЁН (РЕАЛЬНАЯ ВЫДАЧА) ----------

        if data == "tariff_test_confirm":
            free_used = int(existing.get("free_used") or 0)

            # ЗАЩИТА: ДУБЛИРУЮЩАЯ ПРОВЕРКА - на случай если пользователь быстро перешел
            if TariffService._has_active_premium_subscription(existing):
                remaining_days = TariffService._get_remaining_subscription_days(existing)
                if remaining_days and remaining_days > 0:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        parse_mode="HTML",
                        text=(
                            "❌ <b>Нельзя активировать тестовый тариф!</b>\n\n"
                            f"У тебя активна Premium подписка на <b>{remaining_days}</b> дней.\n"
                            "Активация теста сократит срок до 1 часа.\n\n"
                            "Если хочешь продлить — нажми «💎 Premium»"
                        ),
                    )
                    return

            if free_used == 1:
                await context.bot.send_message(
                    chat_id=chat_id,
                    parse_mode="HTML",
                    text=(
                        "⚠️ Ты уже использовал бесплатный пробный доступ на 1 час.\n"
                        "Для дальнейшего использования подключи тариф <b>Premium</b>."
                    ),
                )
                return

            sub_id = existing.get("sub_id")
            if not sub_id:
                sub_id = BillingDB.generate_sub_id(tg_id, tg_name, username_clean)

            # Первый ли это вообще ключ/подписка для пользователя
            first_sub_ever = not had_sub_before

            try:
                # Визуальная анимация ожидания — ТОЛЬКО при первой в жизни выдаче ключа
                if first_sub_ever:
                    await TariffService._animated_sequence(chat_id, context)

                # Время жизни теста: 1 час
                created_dt = datetime.now(timezone.utc)
                expire_dt = created_dt + timedelta(hours=1)
                expiry_time_ms = int(expire_dt.timestamp() * 1000)

                # Удаляем старых клиентов с этим sub_id/username
                await BillingDB.delete_all_user_clients(
                    sub_id=sub_id,
                    username_clean=username_clean,
                )

                # Создаём до 3 клиентов на нужных инбаундах
                client_ids, expiry_ts = await BillingDB.create_clients_for_user(
                    sub_id=sub_id,
                    username_clean=username_clean,
                    expiry_time_ms=expiry_time_ms,
                )

                key_created_at = created_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                key_expires_at: Optional[str]

                if expiry_ts:
                    dt = datetime.fromtimestamp(
                        (expiry_ts / 1000) if expiry_ts > 10_000_000_000 else expiry_ts,
                        tz=timezone.utc,
                    )
                    key_expires_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                else:
                    key_expires_at = expire_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

                # Обновляем запись в нашей БД
                db.upsert_after_create(
                    tg_id=tg_id,
                    tg_name=tg_name,
                    username=username_clean,
                    sub_id=sub_id,
                    sub_name=sub_id,
                    key_created_at=key_created_at,
                    key_expires_at=key_expires_at,
                    client_ids=client_ids,
                    tariff="free",
                    free_used=1,
                )

                # Для уведомителя: даты начала/окончания подписки
                BillingDB.update_subscription_timestamps(
                    tg_id=tg_id,
                    started_iso=key_created_at,
                    expires_iso=key_expires_at,
                )

                # Ставим напоминание (за 5 минут до конца тестового тарифа)
                SubscriptionNotifier.schedule_trial_warning(
                    context=context,
                    tg_id=tg_id,
                    expires_iso=key_expires_at,
                )

                # ⚡ Перезапуск Xray запускаем в фоне:
                asyncio.create_task(BillingDB.restart_xray_safe())

                sub_link = build_sub_link(sub_id)

                await context.bot.send_message(
                    chat_id=chat_id,
                    parse_mode="HTML",
                    text=(
                        "✅ <b>Подписка создана (тестовый тариф).</b>\n\n"
                        "🔗 <b>Твоя персональная subscription-ссылка:</b>\n"
                        f"<pre>{sub_link}</pre>\n\n"
                        "🌍 Скопируй эту ссылку в <b>V2rayTun</b> (iOS / Android)."
                    ),
                )

                if not had_sub_before:
                    await TariffService._send_instruction_cta(chat_id, context)

            except Exception as e:
                logger.exception("Ошибка при активации тестового тарифа")
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Не удалось выдать тестовый доступ: {e}",
                )
            return

        # ---------- ПОЛНЫЙ: ПЕРВЫЙ КЛИК ----------

        if data == "tariff_full":
            current_tariff = (existing.get("tariff") or "").lower()
            if current_tariff == "premium":
                kb = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "😎 Да, продлить на 30 дней",
                                callback_data="tariff_full_extend_yes",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                "Нет, оставить как есть",
                                callback_data="tariff_full_extend_no",
                            )
                        ],
                    ]
                )
                await context.bot.send_message(
                    chat_id=chat_id,
                    parse_mode="HTML",
                    text="У тебя уже активен Premium тариф.\nПродлить подписку ещё на 30 дней?",
                    reply_markup=kb,
                )
                return

            first_sub_ever = not had_sub_before
            await TariffService._activate_full_tariff(
                chat_id=chat_id,
                tg_id=tg_id,
                tg_name=tg_name,
                username_clean=username_clean,
                existing=existing,
                is_extension=False,
                first_sub_ever=first_sub_ever,
                context=context,
            )
            return

        # ---------- ПОЛНЫЙ: ПОДТВЕРЖДЕНИЕ ПРОДЛЕНИЯ ----------

        if data == "tariff_full_extend_yes":
            await TariffService._activate_full_tariff(
                chat_id=chat_id,
                tg_id=tg_id,
                tg_name=tg_name,
                username_clean=username_clean,
                existing=existing,
                is_extension=True,
                first_sub_ever=False,
                context=context,
            )
            return

        if data == "tariff_full_extend_no":
            return

    # ===== ЛОГИКА АКТИВАЦИИ/ПРОДЛЕНИЯ ПОЛНОГО ТАРИФА =====

    @staticmethod
    async def _activate_full_tariff(
        chat_id: int,
        tg_id: int,
        tg_name: str,
        username_clean: str,
        existing: dict,
        is_extension: bool,
        first_sub_ever: bool,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        bal = BillingDB.get_balance(tg_id)
        if bal < FULL_TARIFF_PRICE:
            need = FULL_TARIFF_PRICE - bal
            text = (
                "💎 Premium тариф стоит <b>{price} ₽</b>.\n"
                "На балансе: {bal} ₽ (не хватает {need} ₽).\n\n"
                "Нажми здесь, чтобы пополнить баланс 👇"
            ).format(price=FULL_TARIFF_PRICE, bal=bal, need=need)

            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "💳 Пополнить", callback_data="topup_menu"
                        )
                    ]
                ]
            )

            await context.bot.send_message(
                chat_id=chat_id,
                parse_mode="HTML",
                text=text,
                reply_markup=kb,
            )
            return

        sub_id = existing.get("sub_id")
        if not sub_id:
            sub_id = BillingDB.generate_sub_id(tg_id, tg_name, username_clean)

        prev_free_used = int(existing.get("free_used") or 0)
        had_link_before = bool(existing.get("sub_id"))

        # Базовая точка продления — либо сейчас, либо текущее время окончания
        base_exp = datetime.now(timezone.utc)
        if is_extension:
            try:
                ke = existing.get("key_expires_at")
                if ke:
                    base_exp = max(
                        base_exp,
                        datetime.strptime(ke, "%Y-%m-%dT%H:%M:%SZ").replace(
                            tzinfo=timezone.utc
                        ),
                    )
            except Exception:
                pass

        try:
            # Анимация — только если это вообще первая подписка
            if first_sub_ever and not is_extension:
                await TariffService._animated_sequence(chat_id, context)

            # Списываем деньги
            new_balance = BillingDB.change_balance(tg_id, -FULL_TARIFF_PRICE)

            # Чистим прошлых клиентов
            await BillingDB.delete_all_user_clients(
                sub_id=sub_id,
                username_clean=username_clean,
            )

            # Новый срок: +30 дней
            expire_dt = base_exp + timedelta(days=30)
            expiry_time_ms = int(expire_dt.timestamp() * 1000)

            client_ids, expiry_ts = await BillingDB.create_clients_for_user(
                sub_id=sub_id,
                username_clean=username_clean,
                expiry_time_ms=expiry_time_ms,
            )

            key_created_at = now_iso()
            key_expires_at: Optional[str]
            if expiry_ts:
                dt = datetime.fromtimestamp(
                    (expiry_ts / 1000) if expiry_ts > 10_000_000_000 else expiry_ts,
                    tz=timezone.utc,
                )
                key_expires_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                key_expires_at = expire_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Обновляем пользователя в нашей БД
            db.upsert_after_create(
                tg_id=tg_id,
                tg_name=tg_name,
                username=username_clean,
                sub_id=sub_id,
                sub_name=sub_id,
                key_created_at=key_created_at,
                key_expires_at=key_expires_at,
                client_ids=client_ids,
                tariff="premium",
                free_used=prev_free_used,
            )

            BillingDB.update_subscription_timestamps(
                tg_id=tg_id,
                started_iso=key_created_at,
                expires_iso=key_expires_at,
            )

            # ⚡ Перезапуск Xray — в фоне
            asyncio.create_task(BillingDB.restart_xray_safe())

            sub_link = build_sub_link(sub_id)

            if is_extension or had_link_before:
                msg = (
                    "✅ <b>Premium тариф продлён на 30 дней.</b>\n"
                    f"Списано {FULL_TARIFF_PRICE} ₽. "
                    f"Текущий баланс: {new_balance} ₽.\n\n"
                    "Твоя подписка продолжает работать по той же ссылке:\n"
                    f"<pre>{sub_link}</pre>\n"
                    "<i>Пожалуйста, не забудь обновить подписку в приложении, нажав 🔄.</i>"
                )
            else:
                msg = (
                    "✅ <b>Premium тариф активирован.</b>\n"
                    "Доступ на <b>30 дней</b> создан.\n"
                    f"Списано {FULL_TARIFF_PRICE} ₽. "
                    f"Текущий баланс: {new_balance} ₽.\n\n"
                    "🔗 <b>Твоя subscription-ссылка:</b>\n"
                    f"<pre>{sub_link}</pre>"
                )

            await context.bot.send_message(
                chat_id=chat_id,
                parse_mode="HTML",
                text=msg,
            )

            if first_sub_ever and not is_extension:
                await TariffService._send_instruction_cta(chat_id, context)

            # 🔗 РЕФЕРАЛЬНАЯ ЛОГИКА
            try:
                await referral_service.handle_premium_activated(
                    invited_tg_id=tg_id,
                    context=context,
                    invited_chat_id=chat_id,
                )
            except Exception:
                logger.exception("Ошибка в реферальной логике при активации полного тарифа")

        except Exception as e:
            logger.exception(
                "Ошибка при активации/продлении полного тарифа, откатываю баланс"
            )
            try:
                BillingDB.change_balance(tg_id, FULL_TARIFF_PRICE)
            except Exception as e2:
                logger.exception("Не удалось откатить баланс после ошибки: %s", e2)

            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"❌ Не удалось завершить операцию с полным тарифом: {e}\n"
                    "Если списание прошло — свяжись с поддержкой."
                ),
            )


# ===== ПУБЛИЧНЫЕ ФУНКЦИИ =====

async def show_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await TariffService.show_tariffs(update, context)


async def handle_tariff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await TariffService.handle_tariff_callback(update, context)