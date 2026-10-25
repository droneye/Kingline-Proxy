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
from .subscription_service import activate_subscription, SubscriptionPending
from .subscription_delivery import deliver_receipt
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
            expires_dt = datetime.strptime(
                key_expires_at, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
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
            expires_dt = datetime.strptime(
                key_expires_at, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if expires_dt <= now:
                return 0
            remaining = expires_dt - now
            return max(1, remaining.days)
        except Exception:
            return None

    @staticmethod
    def _tariffs_text() -> str:
        return (
            "📚 <b>Доступные тарифы:</b>\n\n"
            "🧪 <b>Trial</b> — бесплатно.\n"
            "Попробуйте VPN на 24 часа. Доступен один раз.\n\n"
            f"💎 <b>Premium</b> — {FULL_TARIFF_PRICE} ₽.\n"
            "30 дней доступа для телефона и компьютера. Умный выбор сервера и ручной выбор страны — в одной подписке.\n\n"
            "Выберите тариф ниже 👇\n"
            "Ссылка появится в «Ключ», а подключение — в «Инструкция»."
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
    async def _send_instruction_cta(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📘 Как подключить VPN", callback_data="guide_menu")]]
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text="Подключим VPN за три шага — выберите своё устройство.",
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

        await update.message.reply_text(
            text,
            parse_mode="HTML",
            reply_markup=kb,
        )

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
                    logger.warning(
                        "Не удалось отредактировать сообщение в меню тарифов: %s",
                        e,
                    )
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
                            "твою текущую подписку и сократит срок до 24 часов.\n\n"
                            "Если хочешь продлить подписку — нажми «💎 Premium»"
                        ),
                    )
                    return

            if free_used == 1:
                await context.bot.send_message(
                    chat_id=chat_id,
                    parse_mode="HTML",
                    text=(
                        "⚠️ Ты уже использовал бесплатный пробный доступ на 24 часа.\n"
                        "Для дальнейшего использования подключи тариф <b>Premium</b>."
                    ),
                )
                return

            # Меняем текущее сообщение с тарифами на подтверждающее
            text = (
                "🧪 <b>Пробный доступ</b>\n\n"
                "Ты получишь <b>24 часа</b> бесплатного пробного доступа к серверу.\n\n"
                "Активировать пробный период сейчас?"
            )

            kb = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "✅ Да, хочу протестировать",
                            callback_data="tariff_test_confirm",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ Вернуться к выбору тарифов",
                            callback_data="tariff_back",
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
                logger.warning(
                    "Не удалось отредактировать сообщение с тарифами: %s",
                    e,
                )
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
                            "Активация теста сократит срок до суток.\n\n"
                            "Если хочешь продлить — нажми «💎 Premium»"
                        ),
                    )
                    return

            if free_used == 1:
                await context.bot.send_message(
                    chat_id=chat_id,
                    parse_mode="HTML",
                    text=(
                        "⚠️ Ты уже использовал бесплатный пробный доступ на 24 часа.\n"
                        "Для дальнейшего использования подключи тариф <b>Premium</b>."
                    ),
                )
                return

            try:
                result = await activate_subscription(
                    tg_id=tg_id, tg_name=tg_name, username=username_clean,
                    kind="trial", chat_id=chat_id,
                )
            except SubscriptionPending:
                await context.bot.send_message(chat_id=chat_id, text="Доступ создаётся. При временном сбое повторим автоматически, без повторного списания.")
                return
            except ValueError as exc:
                text = (
                    "У тебя уже активен Premium. Пробный тариф недоступен."
                    if str(exc) == "premium_active"
                    else "Пробный доступ уже использован."
                )
                await context.bot.send_message(chat_id=chat_id, text=text)
                return
            except Exception:
                logger.exception("Trial provisioning failed")
                await context.bot.send_message(chat_id=chat_id, text="Не удалось выдать пробный доступ. Попробуй позже.")
                return

            await deliver_receipt(context.bot, result["receipt_id"])
            asyncio.create_task(BillingDB.restart_xray_safe())
            try:
                SubscriptionNotifier.schedule_trial_warning(
                    context=context, tg_id=tg_id,
                    expires_iso=result["user"]["key_expires_at"],
                )
                if result["first_sub_ever"]:
                    await TariffService._send_instruction_cta(chat_id, context)
            except Exception:
                logger.warning("Trial follow-up failed after successful activation", exc_info=True)
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
        chat_id: int, tg_id: int, tg_name: str, username_clean: str,
        existing: dict, is_extension: bool, first_sub_ever: bool,
        context: ContextTypes.DEFAULT_TYPE,
    ):
        try:
            result = await activate_subscription(
                tg_id=tg_id, tg_name=tg_name, username=username_clean,
                kind="premium", chat_id=chat_id,
            )
        except SubscriptionPending:
            await context.bot.send_message(chat_id=chat_id, text="Оплата проверена. Доступ создаётся; повторим автоматически без повторного списания.")
            return
        except ValueError as exc:
            if str(exc) != "insufficient_balance":
                raise
            balance = BillingDB.get_balance(tg_id)
            await context.bot.send_message(
                chat_id=chat_id, parse_mode="HTML",
                text=f"Premium стоит {FULL_TARIFF_PRICE} ₽. На балансе: {balance} ₽.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Пополнить", callback_data="topup_menu")]]
                ),
            )
            return
        except Exception:
            logger.exception("Premium provisioning failed before local commit")
            await context.bot.send_message(
                chat_id=chat_id,
                text="Не удалось обновить Premium. Проверь статус в кабинете или попробуй позже.",
            )
            return

        # Delivery is retried from the outbox and never rolls back a paid subscription.
        delivered = await deliver_receipt(context.bot, result["receipt_id"])
        asyncio.create_task(BillingDB.restart_xray_safe())
        if delivered and result["first_sub_ever"]:
            try:
                await TariffService._send_instruction_cta(chat_id, context)
            except Exception:
                logger.warning("Subscription instructions could not be sent", exc_info=True)
        try:
            await referral_service.handle_premium_activated(
                invited_tg_id=tg_id, context=context, invited_chat_id=chat_id,
            )
        except Exception:
            logger.exception("Referral handling failed after successful Premium activation")

# ===== ПУБЛИЧНЫЕ ФУНКЦИИ =====

async def show_tariffs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await TariffService.show_tariffs(update, context)


async def handle_tariff_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await TariffService.handle_tariff_callback(update, context)
