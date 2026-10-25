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

        # 🔒 Анти-абуз: запрет взаимных рефералов A<->B
        # Нельзя использовать код пользователя, которого ты уже приглашал.
        if self.storage.has_invited(invited_tg_id, inviter_tg_id):
            return (
                False,
                "⚠️ Нельзя использовать реферальный код пользователя, которого ты уже приглашал.\n"
                "Это защита от взаимного обмена кодами.",
            )

        # Уже использовал какой-то код ранее (или бонус уже pending/выдан)
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

        Фиксы анти-абуза:
        - бонус строго 1 раз на пару inviter->invited
        - защита от повторных вызовов/гонок за счёт "резерва" (rewarded=2) ДО sleep
        """
        info = self.storage.get_invitation_for_invited(invited_tg_id)
        if not info:
            return

        inviter_tg_id, rewarded_already = info
        if rewarded_already:
            return

        # 🔒 Резервируем бонус сразу (rewarded: 0 -> 2), чтобы:
        # - повторные renewals invited не увеличивали срок inviter снова
        # - повторные вызовы handle_premium_activated не создали вторую задачу
        reserved = self.storage.try_reserve_reward(inviter_tg_id, invited_tg_id)
        if not reserved:
            return

        async def delayed_reward():
            try:
                await asyncio.sleep(30)

                await self._extend_inviter_subscription(inviter_tg_id, context)

                # Помечаем, что бонус выдан
                self.storage.mark_rewarded(inviter_tg_id, invited_tg_id)

                # Уведомления
                await self._send_reward_notifications(
                    inviter_tg_id, invited_tg_id, invited_chat_id, context
                )

            except Exception:
                # Если не удалось продлить — откатываем reserve,
                # чтобы можно было корректно повторить потом
                self.storage.unreserve_reward(inviter_tg_id, invited_tg_id)
                logger.exception("Ошибка при продлении подписки по рефералке")

        asyncio.create_task(delayed_reward())

    async def _send_reward_notifications(
        self,
        inviter_tg_id: int,
        invited_tg_id: int,
        invited_chat_id: Optional[int],
        context: ContextTypes.DEFAULT_TYPE,
    ) -> None:
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
        from handlers.subscription_service import activate_subscription
        from handlers.subscription_delivery import deliver_receipt

        existing = db.get_user(inviter_tg_id) or {}
        result = await activate_subscription(
            tg_id=inviter_tg_id,
            tg_name=existing.get("tg_name") or "",
            username=existing.get("username") or f"user{inviter_tg_id}",
            kind="referral",
        )
        await deliver_receipt(context.bot, result["receipt_id"])
        asyncio.create_task(BillingDB.restart_xray_safe())
        logger.info("Referral subscription extended")
