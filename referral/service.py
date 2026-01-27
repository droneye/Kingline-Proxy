# refferal/service.py

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple

from telegram.ext import ContextTypes

from config import db, logger
from utils import now_iso
from handlers.billing_db import BillingDB
from .ref_db import ReferralDB


class ReferralService:
    """
    Основная логика реферальной системы.
    """

    def __init__(self, storage: ReferralDB):
        self.storage = storage

    # ===== ПУБЛИЧНЫЕ МЕТОДЫ ДЛЯ ХЕНДЛЕРОВ =====

    def get_or_create_user_code(self, tg_id: int) -> str:
        return self.storage.get_or_create_code(tg_id)

    def get_invitation_info_for_user(self, invited_tg_id: int) -> Optional[Tuple[int, bool]]:
        return self.storage.get_invitation_for_invited(invited_tg_id)

    def get_inviter_by_code(self, code: str) -> Optional[int]:
        row = self.storage.get_by_code(code)
        if not row:
            return None
        return int(row["inviter_tg_id"])

    def register_code_usage(
        self,
        invited_tg_id: int,
        code_raw: str,
    ) -> Tuple[bool, str]:
        code = (code_raw or "").strip().upper().replace(" ", "")
        if not code:
            return False, "❌ Введён пустой код."

        row = self.storage.get_by_code(code)
        if not row:
            return False, "❌ Такой реферальный код не найден."

        inviter_tg_id = int(row["inviter_tg_id"])

        if inviter_tg_id == invited_tg_id:
            return False, "😅 Нельзя использовать собственный реферальный код."

        invitation = self.storage.get_invitation_for_invited(invited_tg_id)
        if invitation is not None:
            return False, "⚠️ Ты уже использовал реферальный код ранее."

        self.storage.add_invited_user(inviter_tg_id, invited_tg_id)

        return (
            True,
            "✅ Код принят!\n\n"
            "Как только ты активируешь полный тариф, "
            "твоему другу автоматически продлят подписку на 30 дней 💎",
        )

    async def handle_premium_activated(
        self,
        invited_tg_id: int,
        context: ContextTypes.DEFAULT_TYPE,
        invited_chat_id: Optional[int] = None,
    ) -> None:
        """
        Вызывается когда invited_tg_id оформил полный тариф.
        Добавляем задержку 30 секунд перед продлением подписки пригласившему.
        """
        info = self.storage.get_invitation_for_invited(invited_tg_id)
        if not info:
            return

        inviter_tg_id, rewarded_already = info
        if rewarded_already:
            return

        # Создаем отложенную задачу с задержкой 30 секунд
        async def delayed_reward():
            try:
                # Ждем 30 секунд
                await asyncio.sleep(30)
                
                # Продлеваем подписку пригласившему
                await self._extend_inviter_subscription(inviter_tg_id, context)

                # Помечаем, что бонус выдан
                self.storage.mark_rewarded(inviter_tg_id, invited_tg_id)

                # Уведомления
                await self._send_reward_notifications(
                    inviter_tg_id, invited_tg_id, invited_chat_id, context
                )

            except Exception:
                logger.exception("Ошибка при продлении подписки по рефералке")

        # Запускаем отложенную задачу
        asyncio.create_task(delayed_reward())

    async def _send_reward_notifications(
        self,
        inviter_tg_id: int,
        invited_tg_id: int,
        invited_chat_id: Optional[int],
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """Отправка уведомлений о бонусе с задержкой"""
        try:
            await context.bot.send_message(
                chat_id=inviter_tg_id,
                text=(
                    "🎁 <b>Реферальный бонус!</b>\n\n"
                    "Твой друг активировал полный тариф по твоему коду.\n"
                    "Мы продлили твою подписку на <b>30 дней</b> 💎\n"
                    "<i>Пожалуйста, не забудь обновить подписку в приложении, нажав 🔄.</i>"
                ),
                parse_mode="HTML",
            )
        except Exception as e:
            logger.warning("Не удалось отправить сообщение пригласившему: %s", e)

        if invited_chat_id:
            try:
                await context.bot.send_message(
                    chat_id=invited_chat_id,
                    text=(
                        "🙏 Спасибо, что оформил подписку по реферальному коду.\n"
                        "Твоему другу только что продлили подписку на 30 дней 💌"
                    ),
                )
            except Exception as e:
                logger.warning("Не удалось отправить сообщение приглашённому: %s", e)

    # ===== ОБНОВЛЕННАЯ ЛОГИКА ПРОДЛЕНИЯ =====

    async def _extend_inviter_subscription(
        self,
        inviter_tg_id: int,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
        """
        Продлевает подписку пригласившему на 30 дней.
        Использует ту же логику, что и основная система тарифов.
        """
        existing = db.get_user(inviter_tg_id) or {}
        
        tg_name = existing.get("tg_name") or ""
        username_clean = (existing.get("username") or f"user{inviter_tg_id}").replace(" ", "")

        # Убеждаемся, что пользователь есть в базе
        BillingDB.ensure_user_row(inviter_tg_id, tg_name, username_clean)
        existing = db.get_user(inviter_tg_id) or {}  # Обновляем данные

        sub_id = existing.get("sub_id")
        if not sub_id:
            # Создаем новый sub_id, если его нет
            sub_id = BillingDB.generate_sub_id(inviter_tg_id, tg_name, username_clean)

        prev_free_used = int(existing.get("free_used") or 0)
        had_link_before = bool(existing.get("sub_id"))

        # Определяем базовую дату для продления (максимум из текущей даты и даты окончания текущей подписки)
        base_exp = datetime.now(timezone.utc)
        try:
            ke = existing.get("key_expires_at")
            if ke:
                db_exp = datetime.strptime(ke, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=timezone.utc
                )
                if db_exp > base_exp:
                    base_exp = db_exp
        except Exception:
            pass

        try:
            # Удаляем старых клиентов пользователя
            await BillingDB.delete_all_user_clients(
                sub_id=sub_id,
                username_clean=username_clean,
            )

            # Новый срок: +30 дней от базовой даты
            expire_dt = base_exp + timedelta(days=30)
            expiry_time_ms = int(expire_dt.timestamp() * 1000)

            # Создаем новых клиентов
            client_ids, expiry_ts = await BillingDB.create_clients_for_user(
                sub_id=sub_id,
                username_clean=username_clean,
                expiry_time_ms=expiry_time_ms,
            )

            key_created_at = now_iso()

            # Определяем дату окончания
            if expiry_ts:
                dt = datetime.fromtimestamp(
                    (expiry_ts / 1000) if expiry_ts > 10_000_000_000 else expiry_ts,
                    tz=timezone.utc,
                )
                key_expires_at = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                key_expires_at = expire_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            # Обновляем запись в основной БД
            db.upsert_after_create(
                tg_id=inviter_tg_id,
                tg_name=tg_name,
                username=username_clean,
                sub_id=sub_id,
                sub_name=sub_id,
                key_created_at=key_created_at,
                key_expires_at=key_expires_at,
                client_ids=client_ids,
                tariff="premium",  # Пригласивший получает полный тариф
                free_used=prev_free_used,
            )

            # Обновляем таймстампы подписки
            BillingDB.update_subscription_timestamps(
                tg_id=inviter_tg_id,
                started_iso=key_created_at,
                expires_iso=key_expires_at,
            )

            # Перезапуск Xray в фоне
            asyncio.create_task(BillingDB.restart_xray_safe())

            logger.info(f"Успешно продлена подписка по рефералке для пользователя {inviter_tg_id}")

        except Exception as e:
            logger.exception(f"Ошибка при продлении подписки по рефералке для {inviter_tg_id}")
            raise