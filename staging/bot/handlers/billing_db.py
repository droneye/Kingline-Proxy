import os
import secrets
import asyncio
import fcntl
import time
from contextlib import AsyncExitStack, asynccontextmanager
from typing import List, Set, Optional, Tuple, Dict, Any

from config import (
    db,
    INBOUND_IDS,
    XUI_PANELS,
    logger,
)

from utils import extract_clients_from_inbound
from xui import XUIClient
from diagnostics import observe, timed_async


MONTHLY_LIMIT_GB = 10
MONTHLY_LIMIT_BYTES = MONTHLY_LIMIT_GB * 1024 ** 3

NEW_PANEL_TAGS = {"NEW", "MOST"}
FR_PANEL_TAGS = {"FR"}
OPTIONAL_PANEL_TAGS = set(NEW_PANEL_TAGS) | set(FR_PANEL_TAGS)
SUBSCRIPTION_LOCK_DIR = os.getenv("KINGLINE_SUBSCRIPTION_LOCK_DIR", "/run/kingline")
SUBSCRIPTION_FLOCK_PATH = os.path.join(SUBSCRIPTION_LOCK_DIR, "subscription_update.flock")
SUBSCRIPTION_MARKER_PATH = os.path.join(SUBSCRIPTION_LOCK_DIR, "subscription_update.lock")


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float((os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        return default


PANEL_MIN_FREE_BYTES = max(1, _env_int("XUI_PANEL_MIN_FREE_MB", 128)) * 1024 * 1024
PANEL_MAX_USED_RATIO = min(0.999, max(0.5, _env_float("XUI_PANEL_MAX_USED_RATIO", 0.995)))
PANEL_TIMEOUT_SECONDS = max(1, _env_int("XUI_PANEL_TIMEOUT_SECONDS", 20))
OPTIONAL_PANEL_TIMEOUT_SECONDS = max(1, _env_int("XUI_OPTIONAL_PANEL_TIMEOUT_SECONDS", 5))


class BillingDB:
    """
    Низкоуровневые операции:
      - таблица users (ensure_user_row, баланс, даты подписки)
      - генерация sub_id
      - работа с XUI (создание/удаление клиентов, рестарт Xray)

    Также используется реферальным сервисом для бесплатного продления
    подписки пригласившему пользователю.

    Раскладка слотов:
      LT/NL/прочие старые панели:
        #1/#2/#3  -> INBOUND_IDS из config.py

      FR:
        #4        -> inbound 1

      NEW/MOST (RU / белый список):
        #5/#6     -> inbounds 1/2
    """

    # ===== служебка по списку панелей =====

    @staticmethod
    def _env_bool(name: str, default: bool = True) -> bool:
        raw = (os.getenv(name) or "").strip().lower()
        if not raw:
            return default
        return raw not in ("0", "false", "no")

    @staticmethod
    def _env_server(tag: str) -> Optional[Dict[str, Any]]:
        """
        Читает сервер из ENV по префиксу XUI_{TAG}_*.
        Пример:
          XUI_LT_URL, XUI_LT_USERNAME, XUI_LT_PASSWORD, (опц) XUI_LT_WEBBASEPATH
          XUI_LT_VERIFY_SSL=false
        """
        tag = (tag or "").strip().upper()
        url = (os.getenv(f"XUI_{tag}_URL") or "").strip().rstrip("/")
        username = (os.getenv(f"XUI_{tag}_USERNAME") or "").strip()
        password = (os.getenv(f"XUI_{tag}_PASSWORD") or "").strip()
        webbasepath = (os.getenv(f"XUI_{tag}_WEBBASEPATH") or "").strip()
        verify_ssl = BillingDB._env_bool(f"XUI_{tag}_VERIFY_SSL", True)

        if not url or not username or not password:
            return None

        return {
            "tag": tag,
            "url": url,
            "username": username,
            "password": password,
            "webbasepath": webbasepath,
            "verify_ssl": verify_ssl,
        }

    @staticmethod
    def _get_panels() -> List[Dict[str, Any]]:
        """
        Возвращает список панелей вида:
          [{url, username, password, webbasepath, verify_ssl, tag}, ...]

        Источники:
          1) config.XUI_PANELS (если задан)
          2) ENV: XUI_LT_*, XUI_NL_*, XUI_NEW_*, XUI_MOST_*, XUI_FR_* (fallback)

        Важно:
          - если config.XUI_PANELS не содержит FR,
            но XUI_FR_* есть в ENV — FR всё равно будет участвовать.
        """
        panels: List[Dict[str, Any]] = []
        seen_tags: Set[str] = set()

        # 1) основной путь — XUI_PANELS из config.py
        if XUI_PANELS:
            for p in XUI_PANELS:
                if not isinstance(p, dict):
                    continue

                tag = (p.get("tag") or "").strip().upper() or "XUI"
                url = (p.get("url") or p.get("root_url") or p.get("base") or "").strip().rstrip("/")
                username = (p.get("username") or "").strip()
                password = (p.get("password") or "").strip()
                webbasepath = (p.get("webbasepath") or p.get("webBasePath") or "").strip()
                verify_ssl = p.get("verify_ssl")
                if verify_ssl is None:
                    verify_ssl = BillingDB._env_bool(f"XUI_{tag}_VERIFY_SSL", True)
                else:
                    verify_ssl = bool(verify_ssl)

                if url and username and password:
                    panels.append(
                        {
                            "tag": tag,
                            "url": url,
                            "username": username,
                            "password": password,
                            "webbasepath": webbasepath,
                            "verify_ssl": verify_ssl,
                        }
                    )
                    seen_tags.add(tag)

        # 2) добираем отсутствующие панели из ENV
        for tag in ("LT", "NL", "NEW", "MOST", "FR"):
            if tag in seen_tags:
                continue
            cfg = BillingDB._env_server(tag)
            if cfg:
                panels.append(cfg)
                seen_tags.add(tag)

        return panels

    @staticmethod
    def _is_optional_panel(tag: str) -> bool:
        return (tag or "").strip().upper() in OPTIONAL_PANEL_TAGS

    @staticmethod
    def _storage_problem(status: Dict[str, Any]) -> Optional[str]:
        disk = status.get("disk") if isinstance(status, dict) else None
        if not isinstance(disk, dict):
            return None

        try:
            used = int(disk.get("current") or 0)
            total = int(disk.get("total") or 0)
        except Exception:
            return None

        if total <= 0:
            return None

        free = max(total - used, 0)
        used_ratio = used / total
        if free < PANEL_MIN_FREE_BYTES or used_ratio >= PANEL_MAX_USED_RATIO:
            return (
                f"disk is almost full: free={free / 1024 / 1024:.1f} MB, "
                f"used={used_ratio * 100:.1f}%"
            )

        return None

    @staticmethod
    def _is_disk_full_error(e: Exception) -> bool:
        text = str(e).lower()
        return (
            "database or disk is full" in text
            or "no space left" in text
            or "disk is almost full" in text
        )

    @staticmethod
    @timed_async("subscription.preflight", slow_ms=2000)
    async def _preflight_panel_health(panels: List[Dict[str, Any]]) -> Set[str]:
        async def check_panel(p):
            tag = (p.get("tag") or "XUI").strip().upper()
            base_url = p["url"]
            try:
                async with XUIClient(
                    root_url=base_url,
                    username=p["username"],
                    password=p["password"],
                    webbasepath=p.get("webbasepath", "") or "",
                    timeout=(
                        OPTIONAL_PANEL_TIMEOUT_SECONDS
                        if BillingDB._is_optional_panel(tag)
                        else PANEL_TIMEOUT_SECONDS
                    ),
                    verify_ssl=bool(p.get("verify_ssl", True)),
                    logger_=logger,
                ) as xc:
                    problem = BillingDB._storage_problem(await xc.server_status())
            except Exception as e:
                if BillingDB._is_optional_panel(tag):
                    logger.warning(
                        "Пропускаю optional XUI-панель %s (%s): preflight недоступен: %s",
                        tag,
                        base_url,
                        e,
                    )
                    return tag

                logger.warning(
                    "Не удалось проверить состояние XUI-панели %s (%s), продолжаю без preflight: %s",
                    tag,
                    base_url,
                    e,
                )
                return None

            if not problem:
                return None

            if BillingDB._is_optional_panel(tag):
                logger.warning(
                    "Пропускаю optional XUI-панель %s (%s): %s",
                    tag,
                    base_url,
                    problem,
                )
                return tag

            raise RuntimeError(
                f"XUI-панель {tag} ({base_url}) недоступна для записи: {problem}"
            )

        results = await asyncio.gather(*(check_panel(p) for p in panels), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return {tag for tag in results if tag}

    @staticmethod
    def _panel_slot_plan(tag: str) -> List[Dict[str, Any]]:
        """
        Возвращает план создания клиентов для панели.

        Старые панели (LT/NL/...):
          - INBOUND_IDS из config.py
          - email: #1/#2/#3
          - flow: xtls-rprx-vision
          - total_gb: 0 (без отдельного месячного лимита)

        Французская панель (FR):
          - inbound: 1
          - email: #4
          - flow: xtls-rprx-vision
          - total_gb: 0

        Новая панель (tag=NEW / MOST):
          - inbounds: 1/2
          - email: #5/#6
          - flow:
              1 -> xtls-rprx-vision
              2 -> xtls-rprx-vision
          - total_gb: 10 GiB на ключ

        UUID одного и того же слота остаётся одинаковым между серверами,
        где этот слот используется.
        """
        tag = (tag or "").strip().upper()

        if tag in FR_PANEL_TAGS:
            return [
                {
                    "inbound_id": 1,
                    "email_num": 4,
                    "flow": "xtls-rprx-vision",
                    "uuid_index": 3,
                    "total_gb": 0,
                }
            ]

        if tag in NEW_PANEL_TAGS:
            return [
                {
                    "inbound_id": 1,
                    "email_num": 5,
                    "flow": "xtls-rprx-vision",
                    "uuid_index": 4,
                    "total_gb": MONTHLY_LIMIT_BYTES,
                },
                {
                    "inbound_id": 2,
                    "email_num": 6,
                    "flow": "xtls-rprx-vision",
                    "uuid_index": 5,
                    "total_gb": MONTHLY_LIMIT_BYTES,
                },
            ]

        plan: List[Dict[str, Any]] = []
        for idx, inbound_id in enumerate(INBOUND_IDS, start=1):
            plan.append(
                {
                    "inbound_id": int(inbound_id),
                    "email_num": idx,
                    "flow": "xtls-rprx-vision",
                    "uuid_index": idx - 1,
                    "total_gb": 0,
                }
            )
        return plan

    @staticmethod
    def _shared_uuid_count(panels: List[Dict[str, Any]]) -> int:
        max_idx = -1
        for p in panels:
            for spec in BillingDB._panel_slot_plan(p.get("tag", "")):
                max_idx = max(max_idx, int(spec["uuid_index"]))
        return max_idx + 1 if max_idx >= 0 else 0

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
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                """
                UPDATE users
                SET balance_rub = COALESCE(balance_rub, 0) + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tg_id = ?
                """,
                (int(delta), tg_id),
            )
            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO users (tg_id, balance_rub) VALUES (?, ?)",
                    (tg_id, int(delta)),
                )

            cur.execute("SELECT balance_rub FROM users WHERE tg_id = ?", (tg_id,))
            row = cur.fetchone()
            new_val = int(row[0] or 0) if row else 0
            conn.commit()
            return new_val

    @staticmethod
    def claim_balance(tg_id: int, amount: int) -> Tuple[bool, int]:
        """
        Атомарно списывает amount, только если денег достаточно.
        Возвращает (claimed, balance_after_or_current).
        """
        amount = int(amount)
        if amount <= 0:
            raise ValueError("amount must be positive")

        with db._connect() as conn:  # type: ignore[attr-defined]
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                """
                UPDATE users
                SET balance_rub = COALESCE(balance_rub, 0) - ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tg_id = ?
                  AND COALESCE(balance_rub, 0) >= ?
                """,
                (amount, tg_id, amount),
            )

            if cur.rowcount == 0:
                cur.execute("SELECT balance_rub FROM users WHERE tg_id = ?", (tg_id,))
                row = cur.fetchone()
                current = int(row[0] or 0) if row else 0
                conn.commit()
                return False, current

            cur.execute("SELECT balance_rub FROM users WHERE tg_id = ?", (tg_id,))
            row = cur.fetchone()
            new_balance = int(row[0] or 0) if row else 0
            conn.commit()
            return True, new_balance

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

    @staticmethod
    def _normalized_email_owner(email: Any) -> str:
        owner = str(email or "").strip().lower()
        if "#" in owner:
            owner = owner.split("#", 1)[0].strip()
        return owner.replace("@", "").replace(" ", "")

    @staticmethod
    def _client_matches_user(c: Dict[str, Any], username_clean: str, sub_id: str) -> bool:
        username_norm = (username_clean or "").lower().replace("@", "").replace(" ", "")
        sub_norm = (sub_id or "").lower().strip()
        c_sub = str(c.get("subId") or c.get("subscription") or "").lower().strip()

        if c_sub:
            return bool(sub_norm and c_sub == sub_norm)

        return bool(
            username_norm
            and BillingDB._normalized_email_owner(c.get("email")) == username_norm
        )

    @staticmethod
    def _client_id(c: Dict[str, Any]) -> str:
        return str(c.get("id") or c.get("uuid") or c.get("clientId") or "").strip()

    @staticmethod
    def _find_slot_client(
        clients: List[Dict[str, Any]],
        *,
        expected_email: str,
        username_clean: str,
        sub_id: str,
    ) -> Optional[Dict[str, Any]]:
        expected_norm = (expected_email or "").strip().lower()

        exact_matches: List[Dict[str, Any]] = []
        fallback_matches: List[Dict[str, Any]] = []
        for c in clients:
            if not isinstance(c, dict) or not BillingDB._client_id(c):
                continue

            email_norm = str(c.get("email") or "").strip().lower()
            if not BillingDB._client_matches_user(c, username_clean, sub_id):
                continue
            if expected_norm and email_norm == expected_norm:
                exact_matches.append(c)
                continue

            if BillingDB._client_matches_user(c, username_clean, sub_id):
                fallback_matches.append(c)

        return (exact_matches or fallback_matches or [None])[0]

    @staticmethod
    def _updated_client_payload(
        xc: XUIClient,
        existing: Dict[str, Any],
        *,
        email: str,
        flow: str,
        sub_id: str,
        expiry_time_ms: int,
        total_gb: int,
    ) -> Dict[str, Any]:
        client_obj = xc._normalize_client_for_update(existing, enabled=True)
        client_obj.update(
            {
                "id": BillingDB._client_id(existing),
                "email": email,
                "flow": flow or "",
                "totalGB": int(total_gb or 0),
                "expiryTime": int(expiry_time_ms) if expiry_time_ms else 0,
                "enable": True,
                "subId": sub_id,
            }
        )
        return client_obj

    @staticmethod
    @asynccontextmanager
    async def subscription_update_lock(reason: str = "subscription_update"):
        """
        Межпроцессный lock для критической зоны upsert клиентов в 3x-ui.

        Systemd ExecStop бота ждёт этот же flock, поэтому обычный restart не
        сможет остановить процесс посреди выдачи/продления подписки.
        """
        os.makedirs(SUBSCRIPTION_LOCK_DIR, exist_ok=True)
        fd = open(SUBSCRIPTION_FLOCK_PATH, "a+", encoding="utf-8")
        acquired = False
        started = time.monotonic()
        acquired_at = None
        try:
            while not acquired:
                try:
                    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    await asyncio.sleep(0.05)
            acquired_at = time.monotonic()
            with open(SUBSCRIPTION_MARKER_PATH, "w", encoding="utf-8") as marker:
                marker.write(f"{os.getpid()} {reason}\n")
            yield
        finally:
            try:
                if acquired and os.path.exists(SUBSCRIPTION_MARKER_PATH):
                    os.unlink(SUBSCRIPTION_MARKER_PATH)
            except FileNotFoundError:
                pass
            finally:
                if acquired:
                    fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                fd.close()
                observe("subscription.lock_wait", ((acquired_at or time.monotonic()) - started) * 1000,
                        error=None if acquired else "CancelledOrFailed")
                if acquired_at is not None:
                    observe("subscription.lock_held", (time.monotonic() - acquired_at) * 1000, slow_ms=5000)

    # ===== XUI: создание/удаление клиентов =====

    @staticmethod
    @timed_async("subscription.provision", slow_ms=3000)
    async def create_clients_for_user(
        sub_id: str,
        username_clean: str,
        expiry_time_ms: int,
        *,
        deferred_panels: Optional[Set[str]] = None,
        panel_tags: Optional[Set[str]] = None,
    ) -> Tuple[List[str], Optional[int]]:
        from remnawave import Remnawave
        native = Remnawave()
        user = await asyncio.to_thread(native.ensure, sub_id, username_clean, expiry_time_ms)
        expiry = int(__import__('datetime').datetime.fromisoformat(user['expireAt'].replace('Z', '+00:00')).timestamp() * 1000)
        if user['status'] != 'ACTIVE' or abs(expiry - expiry_time_ms) > 1500:
            raise RuntimeError('Remnawave did not confirm subscription')
        return [user['vlessUuid']], expiry

    @staticmethod
    async def delete_all_user_clients(sub_id: str, username_clean: str) -> int:
        from remnawave import Remnawave
        return await asyncio.to_thread(Remnawave().disable, sub_id)

    # ===== РЕСТАРТ XRAY (простой, быстрый) =====

    @staticmethod
    async def restart_xray_safe():
        # Native API applies users without interrupting any active tunnels.
        return None
