# handlers/access_key.py

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import List, Optional, Set
from urllib.parse import urlparse, urlunparse

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from telegram.ext import ContextTypes

from config import db, INBOUND_IDS, XUI_PANELS, logger
from utils import (
    get_username_clean,
    build_sub_link,
    extract_clients_from_inbound,
)
from xui import XUIClient


def _make_panel_url_from_proxied(url: str, panel_port: int = 8443) -> str:
    """
    Берём проксированную ссылку (обычно :9443) и делаем из неё
    "прямую" ссылку на панель (порт 8443).

    Пример:
      https://sub.kingline.online:9443/sub/xxx  ->  https://sub.kingline.online:8443/sub/xxx
    """
    try:
        parsed = urlparse(url)

        host = parsed.hostname or ""
        if not host:
            return url.replace(":9443", f":{panel_port}")

        netloc = host

        # если в URL внезапно была авторизация
        if parsed.username:
            auth = parsed.username
            if parsed.password:
                auth += f":{parsed.password}"
            netloc = f"{auth}@{netloc}"

        netloc = f"{netloc}:{panel_port}"
        new_parsed = parsed._replace(netloc=netloc)
        return urlunparse(new_parsed)
    except Exception:
        return url.replace(":9443", f":{panel_port}")


def _get_first_panel_cfg() -> Optional[dict]:
    """
    Берём первую панель из XUI_PANELS.
    Ожидаемый формат элемента:
      {"tag": "...", "url": "...", "username": "...", "password": "...", "webbasepath": "..."}
    """
    try:
        if not XUI_PANELS:
            return None
        p0 = XUI_PANELS[0]
        if not isinstance(p0, dict):
            return None

        url = (p0.get("url") or "").strip().rstrip("/")
        username = (p0.get("username") or "").strip()
        password = (p0.get("password") or "").strip()
        webbasepath = (p0.get("webbasepath") or "").strip()

        if not url or not username or not password:
            return None

        return {
            "url": url,
            "username": username,
            "password": password,
            "webbasepath": webbasepath,
            "tag": (p0.get("tag") or "XUI").strip() or "XUI",
        }
    except Exception:
        return None


async def show_access_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Показывает пользователю его текущий ключ доступа:

      - subscription-ссылку
      - тариф (по данным БД)
      - срок действия (читаем из XUI, без изменений на сервере)
        и отображаем в часовом поясе МСК (UTC+3)

    Ничего не создаёт и не обновляет, только читает.
    Дополнительно даёт ОДНУ кнопку «Посмотреть».
    """

    user = update.effective_user
    message = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if not user or not message:
        return

    tg_id = user.id
    username_clean = get_username_clean(update)

    # ==== 1. Достаём базовую инфу из БД ====
    info = db.get_user(tg_id)
    if not info or not info.get("sub_id"):
        await message.reply_text(
            "Похоже, у тебя ещё нет активного ключа доступа.\n\n"
            "Выбери тариф в меню, чтобы получить персональную ссылку 🚀"
        )
        return

    sub_id = str(info.get("sub_id"))
    tariff = (info.get("tariff") or "free").lower()
    tariff_h = (
        "Trial"
        if tariff == "free"
        else ("Premium" if tariff == "premium" else tariff)
    )

    # 🔗 1) Проксированная ссылка (через sub-proxy, порт 9443) — показываем в тексте
    proxied_sub_link = build_sub_link(sub_id)

    # 🔗 2) Непроксированная (прямая) ссылка — для кнопки "Посмотреть" (порт 8443)
    panel_sub_link = _make_panel_url_from_proxied(proxied_sub_link, panel_port=8443)

    # Дата из БД, если есть
    db_exp_str = info.get("key_expires_at")
    expires_from_db: Optional[datetime] = None
    if db_exp_str:
        try:
            expires_from_db = datetime.strptime(
                db_exp_str, "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
        except Exception:
            expires_from_db = None

    # ==== 2. Пробуем получить expiry из XUI (через логин/пароль) ====
    expiry_from_xui: Optional[datetime] = None

    panel_cfg = _get_first_panel_cfg()
    if not panel_cfg:
        logger.warning(
            "show_access_key: XUI_PANELS не настроен или невалиден — показываю срок только из БД"
        )
        all_inbounds = []
    else:
        try:
            async with XUIClient(
                root_url=panel_cfg["url"],
                username=panel_cfg["username"],
                password=panel_cfg["password"],
                webbasepath=panel_cfg.get("webbasepath", "") or "",
                logger_=logger,
            ) as xc:
                all_inbounds = await xc.list_inbounds()
        except Exception as e:
            logger.warning(
                "Не удалось получить inbounds из XUI в show_access_key (%s %s): %s",
                panel_cfg.get("tag"),
                panel_cfg.get("url"),
                e,
            )
            all_inbounds = []

    if all_inbounds:
        try:
            wanted: Set[str] = {str(i) for i in INBOUND_IDS}
            username_norm = username_clean.lower().replace(" ", "")
            sub_norm = sub_id.lower()

            expiry_candidates: List[int] = []

            for ib in all_inbounds:
                inbound_id = ib.get("id") or (
                    isinstance(ib.get("obj"), dict) and ib["obj"].get("id")
                )
                if inbound_id is None or str(inbound_id) not in wanted:
                    continue

                clients = extract_clients_from_inbound(ib)
                for c in clients:
                    if not isinstance(c, dict):
                        continue

                    email = str(c.get("email") or "")
                    email_norm = email.lower().replace(" ", "")
                    c_sub = str(
                        c.get("subId") or c.get("subscription") or ""
                    ).lower().strip()

                    if (username_norm and email_norm.startswith(username_norm)) or (
                        sub_norm and c_sub == sub_norm
                    ):
                        exp_raw = int(c.get("expiryTime") or 0)
                        if exp_raw > 0:
                            expiry_candidates.append(exp_raw)

            if expiry_candidates:
                exp_ts = min(expiry_candidates)
                expiry_from_xui = datetime.fromtimestamp(
                    (exp_ts / 1000) if exp_ts > 10_000_000_000 else exp_ts,
                    tz=timezone.utc,
                )

        except Exception as e:
            logger.warning("Ошибка разбора клиентов XUI в show_access_key: %s", e)

    # ==== 3. Выбираем итоговый expiry ====
    # приоритет: XUI > БД > None
    effective_exp_utc: Optional[datetime] = expiry_from_xui or expires_from_db

    # ==== 4. Формируем текст про срок действия (МСК, UTC+3) ====
    if effective_exp_utc is None:
        exp_text = "⏳ Срок действия: без ограничения или не указан."
        status = "active_or_unknown"
    else:
        exp_msk = effective_exp_utc + timedelta(hours=3)
        now_msk = datetime.now(timezone.utc) + timedelta(hours=3)

        if exp_msk < now_msk:
            exp_text = (
                f"⏳ Срок действия: истёк "
                f"<b>{exp_msk.strftime('%d.%m.%Y %H:%M')}</b> (МСК)."
            )
            status = "expired"
        else:
            exp_text = (
                "⏳ Доступ активен до: "
                f"<b>{exp_msk.strftime('%d.%m.%Y %H:%M')}</b> (МСК)."
            )
            status = "active"

    # ==== 5. Финальный ответ + одна кнопка WebApp ====
    lines = [
        "🛰 <b>Твой ключ доступа</b>",
        "",
        "🔗 <b>Subscription-ссылка:</b>",
        f"<pre>{proxied_sub_link}</pre>",
        "",
        f"📦 Тариф: <b>{tariff_h}</b>",
        exp_text,
    ]

    if status == "expired":
        lines.append(
            "\nТвоя текущая подписка больше не активна. "
            "Чтобы продлить доступ, выбери тариф или пополни баланс."
        )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("👀 Посмотреть", web_app=WebAppInfo(url=panel_sub_link))]]
    )

    await message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=False,
        reply_markup=keyboard,
    )
