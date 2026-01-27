# handlers/billing_db.py

import os
import secrets
from typing import List, Set, Optional, Tuple, Dict, Any

from config import (
    db,
    INBOUND_IDS,
    XUI_PANELS,  # может быть []
    logger,
)

from utils import extract_clients_from_inbound
from xui import XUIClient


class BillingDB:
    """
    Низкоуровневые операции:
      - таблица users (ensure_user_row, баланс, даты подписки)
      - генерация sub_id
      - работа с XUI (создание/удаление клиентов, рестарт Xray)

    Также используется реферальным сервисом для бесплатного продления
    подписки пригласившему пользователю.
    """

    # ===== служебка по списку панелей =====

    @staticmethod
    def _env_server(tag: str) -> Optional[Dict[str, str]]:
        """
        Читает сервер из ENV по префиксу XUI_{TAG}_*.
        Пример:
          XUI_LT_URL, XUI_LT_USERNAME, XUI_LT_PASSWORD, (опц) XUI_LT_WEBBASEPATH
        """
        url = (os.getenv(f"XUI_{tag}_URL") or "").strip().rstrip("/")
        username = (os.getenv(f"XUI_{tag}_USERNAME") or "").strip()
        password = (os.getenv(f"XUI_{tag}_PASSWORD") or "").strip()
        webbasepath = (os.getenv(f"XUI_{tag}_WEBBASEPATH") or "").strip()

        if not url or not username or not password:
            return None

        return {
            "tag": tag,
            "url": url,
            "username": username,
            "password": password,
            "webbasepath": webbasepath,
        }

    @staticmethod
    def _get_panels() -> List[Dict[str, str]]:
        """
        Возвращает список панелей вида:
          [{url, username, password, webbasepath, tag}, ...]

        Источники:
          1) config.XUI_PANELS (если задан)
          2) ENV: XUI_LT_*, XUI_NL_* (fallback)
        """
        panels: List[Dict[str, str]] = []

        # 1) основной путь — XUI_PANELS из config.py
        if XUI_PANELS:
            for p in XUI_PANELS:
                if not isinstance(p, dict):
                    continue

                url = (p.get("url") or p.get("root_url") or p.get("base") or "").strip().rstrip("/")
                username = (p.get("username") or "").strip()
                password = (p.get("password") or "").strip()
                webbasepath = (p.get("webbasepath") or p.get("web_basepath") or "").strip()
                tag = (p.get("tag") or "").strip() or "XUI"

                if url and username and password:
                    panels.append(
                        {
                            "tag": tag,
                            "url": url,
                            "username": username,
                            "password": password,
                            "webbasepath": webbasepath,
                        }
                    )

        # 2) fallback — читаем из ENV (LT/NL)
        if not panels:
            for tag in ("LT", "NL"):
                cfg = BillingDB._env_server(tag)
                if cfg:
                    panels.append(cfg)

        return panels

    # ===== users: базовые операции =====

    @staticmethod
    def ensure_user_row(tg_id: int, tg_name: str, username: str) -> None:
        """
        Гарантирует, что запись в таблице users существует.
        """
        try:
            if db.get_user(tg_id):
                return
            with db._connect() as conn:  # type: ignore[attr-defined]
                conn.execute(
                    "INSERT OR IGNORE INTO users (tg_id, tg_name, username) "
                    "VALUES (?, ?, ?)",
                    (tg_id, tg_name, username),
                )
                conn.commit()
        except Exception as e:
            logger.exception("ensure_user_row failed: %s", e)

    @staticmethod
    def get_balance(tg_id: int) -> int:
        """
        Текущий баланс пользователя (или 0, если записи нет).
        """
        try:
            u = db.get_user(tg_id)
            return int(u.get("balance_rub") or 0) if u else 0
        except Exception:
            return 0

    @staticmethod
    def change_balance(tg_id: int, delta: int) -> int:
        """
        Изменить баланс на delta. Возвращает новый баланс.
        """
        with db._connect() as conn:  # type: ignore[attr-defined]
            cur = conn.cursor()
            cur.execute("SELECT balance_rub FROM users WHERE tg_id = ?", (tg_id,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO users (tg_id, balance_rub) VALUES (?, ?)",
                    (tg_id, delta),
                )
                conn.commit()
                return delta

            current = int(row[0] or 0)
            new_val = current + int(delta)
            cur.execute(
                """
                UPDATE users
                SET balance_rub = ?, updated_at = CURRENT_TIMESTAMP
                WHERE tg_id = ?
                """,
                (new_val, tg_id),
            )
            conn.commit()
            return new_val

    @staticmethod
    def update_subscription_timestamps(
        tg_id: int,
        started_iso: str,
        expires_iso: Optional[str],
    ) -> None:
        """
        Обновляет поля subscription_started_at и subscription_expires_at в users.
        started_iso / expires_iso — строки формата '%Y-%m-%dT%H:%M:%SZ' (UTC).
        """
        if not expires_iso:
            return

        try:
            with db._connect() as conn:  # type: ignore[attr-defined]
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE users
                    SET subscription_started_at = ?,
                        subscription_expires_at = ?
                    WHERE tg_id = ?
                    """,
                    (started_iso, expires_iso, tg_id),
                )
                conn.commit()
        except Exception as e:
            logger.exception(
                "Не удалось записать даты подписки в БД (tg_id=%s): %s", tg_id, e
            )

    @staticmethod
    def generate_sub_id(tg_id: int, tg_name: str, username_clean: str) -> str:
        """
        Генерирует защищённый sub_id вида:
          namepart-randomcode
        """
        base = username_clean or tg_name or f"user{tg_id}"
        base = base.strip().lower()

        name_part = "".join(ch for ch in base if ch.isalnum())
        if not name_part:
            name_part = f"user{tg_id}"

        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
        code = "".join(secrets.choice(alphabet) for _ in range(16))

        return f"{name_part}-{code}"

    # ===== XUI: создание/удаление клиентов =====

    @staticmethod
    async def create_clients_for_user(
        sub_id: str,
        username_clean: str,
        expiry_time_ms: int,
    ) -> Tuple[List[str], Optional[int]]:
        """
        Создаёт до 3 клиентов в указанных инбаундах INBOUND_IDS
        на КАЖДОЙ панели из XUI_PANELS (или ENV LT/NL fallback).

        На всех серверах для одного и того же "слота" (Mobile/PC/TV)
        используется один и тот же UUID.

        Возвращает (список client_id'ов (первые 3 найденных), минимальный expiryTime).
        """
        from uuid import uuid4

        panels = BillingDB._get_panels()
        if not panels:
            raise RuntimeError(
                "Не настроены панели XUI. Заполни XUI_LT_URL/XUI_LT_USERNAME/XUI_LT_PASSWORD "
                "и/или XUI_NL_URL/XUI_NL_USERNAME/XUI_NL_PASSWORD (или настрой XUI_PANELS в config.py)."
            )

        max_clients = 3
        shared_uuids = [str(uuid4()) for _ in range(max_clients)]

        all_client_ids: List[str] = []
        global_expiry_ts: Optional[int] = None

        wanted_ids: Set[str] = {str(i) for i in INBOUND_IDS}
        username_norm = username_clean.lower().replace(" ", "")
        sub_norm = sub_id.lower()

        for p in panels:
            base_url = p["url"]
            user = p["username"]
            pwd = p["password"]
            webbasepath = p.get("webbasepath", "") or ""
            tag = p.get("tag", "XUI")

            async with XUIClient(
                root_url=base_url,
                username=user,
                password=pwd,
                webbasepath=webbasepath,
                logger_=logger,
            ) as xc:
                # 1) получаем инбаунды на этой панели
                all_inbounds = await xc.list_inbounds()
                targets = [ib for ib in all_inbounds if str(ib.get("id")) in wanted_ids]
                if not targets:
                    raise RuntimeError(
                        f"На панели {tag} ({base_url}) не найдено инбаундов с ID {INBOUND_IDS}"
                    )

                # 2) создаём до 3 клиентов с заранее заданными UUID
                created = 0
                for idx, ib in enumerate(targets, start=1):
                    if created >= max_clients:
                        break
                    inbound_id = ib.get("id")
                    if not inbound_id:
                        continue

                    email = f"{username_clean} #{idx}"
                    client_uuid = shared_uuids[created]

                    await xc.add_client_append(
                        inbound_id=inbound_id,
                        email=email,
                        flow="xtls-rprx-vision-udp443",
                        sub_id=sub_id,
                        expiry_time=expiry_time_ms,
                        uuid=client_uuid,
                    )
                    created += 1

                if created == 0:
                    raise RuntimeError(
                        f"На панели {tag} ({base_url}) не удалось создать ни одного клиента. "
                        "Сначала сделай очистку, затем попробуй ещё раз."
                    )

                # 3) перечитываем инбаунды на этой панели и собираем info
                all_inbounds = await xc.list_inbounds()

            # разбор клиентов (вне контекста — сессия уже закрыта, но данные у нас есть)
            for ib in all_inbounds:
                inbound_id = ib.get("id") or (
                    isinstance(ib.get("obj"), dict) and ib["obj"].get("id")
                )
                if inbound_id is None or str(inbound_id) not in wanted_ids:
                    continue

                clients = extract_clients_from_inbound(ib)
                for c in clients:
                    if not isinstance(c, dict):
                        continue

                    cid = str(c.get("id") or c.get("uuid") or c.get("clientId") or "").strip()
                    if not cid:
                        continue

                    email = str(c.get("email") or "")
                    email_norm = email.lower().replace(" ", "")
                    c_sub = str(c.get("subId") or c.get("subscription") or "").lower().strip()

                    if (
                        (username_norm and email_norm.startswith(username_norm))
                        or (sub_norm and c_sub == sub_norm)
                    ):
                        if cid not in all_client_ids:
                            all_client_ids.append(cid)

                        c_exp = int(c.get("expiryTime") or 0)
                        if c_exp > 0:
                            global_expiry_ts = c_exp if global_expiry_ts is None else min(global_expiry_ts, c_exp)

        return all_client_ids[:max_clients], global_expiry_ts

    @staticmethod
    async def delete_all_user_clients(sub_id: str, username_clean: str) -> int:
        """
        Удаляет всех клиентов пользователя (по username/sub_id) из ВСЕХ инбаундов
        на КАЖДОЙ панели.

        Возвращает суммарное число удалённых клиентов.
        """
        panels = BillingDB._get_panels()
        if not panels:
            return 0

        total_deleted = 0
        for p in panels:
            base_url = p["url"]
            user = p["username"]
            pwd = p["password"]
            webbasepath = p.get("webbasepath", "") or ""

            async with XUIClient(
                root_url=base_url,
                username=user,
                password=pwd,
                webbasepath=webbasepath,
                logger_=logger,
            ) as xc:
                all_inbounds = await xc.list_inbounds()
                if not all_inbounds:
                    continue

                for ib in all_inbounds:
                    deleted = await xc.delete_user_clients(
                        inbound=ib,
                        username_clean=username_clean,
                        sub_id=sub_id,
                    )
                    total_deleted += int(deleted or 0)

        return total_deleted

    # ===== РЕСТАРТ XRAY (простой, быстрый) =====

    @staticmethod
    async def restart_xray_safe():
        """
        Быстрый перезапуск Xray через API 3x-ui на всех панелях.
        """
        try:
            panels = BillingDB._get_panels()
            if not panels:
                logger.warning("restart_xray_safe: нет ни одной панели XUI")
                return

            for p in panels:
                base_url = p["url"]
                user = p["username"]
                pwd = p["password"]
                webbasepath = p.get("webbasepath", "") or ""
                tag = p.get("tag", "XUI")

                try:
                    async with XUIClient(
                        root_url=base_url,
                        username=user,
                        password=pwd,
                        webbasepath=webbasepath,
                        logger_=logger,
                    ) as xc:
                        status = await xc.restart_xray()
                        logger.info(
                            "Xray restart via API успешно на панели %s (%s): %r",
                            tag,
                            base_url,
                            status,
                        )
                except Exception as e_panel:
                    logger.exception(
                        "Не удалось перезапустить Xray через API на панели %s (%s): %s",
                        tag,
                        base_url,
                        e_panel,
                    )

        except Exception as e:
            logger.exception("restart_xray_safe: общая ошибка: %s", e)
