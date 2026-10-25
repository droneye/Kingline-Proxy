from __future__ import annotations

import json
import logging
import os
import random
import socket
import sqlite3
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from db import SubscriptionDB

logger = logging.getLogger("dns_user_locator")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TRAFFIC_FILE = os.path.join(BASE_DIR, "traffic_data.json")

RESERVED_SUB_IDS = {
    "www",
    "api",
    "panel",
    "login",
    "mail",
    "smtp",
    "imap",
    "pop",
    "autoconfig",
    "autodiscover",
    "mta",
    "ns1",
    "ns2",
}


def _resolve_db_path(db_path: Optional[str]) -> str:
    if db_path:
        return os.path.abspath(db_path)

    env_db = os.getenv("DB_PATH", "").strip()
    if env_db:
        return os.path.abspath(env_db)

    return os.path.abspath(os.path.join(os.path.dirname(BASE_DIR), "vpn_bot.db"))


def _connect_sqlite(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=20)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _is_ip(value: str) -> bool:
    try:
        socket.inet_aton(value)
        return True
    except Exception:
        return False


def _parse_env_ips() -> List[str]:
    raw = os.getenv("KINGLINE_DNS_IPS", "")
    return [x.strip() for x in raw.replace(";", ",").split(",") if x.strip()]


def _get_lt_ip() -> str:
    env_lt = os.getenv("KINGLINE_LT_IP", "").strip()
    if env_lt:
        return env_lt

    ips = _parse_env_ips()
    return ips[0] if len(ips) >= 1 else ""


def _get_nl_ip() -> str:
    env_nl = os.getenv("KINGLINE_NL_IP", "").strip()
    if env_nl:
        return env_nl

    ips = _parse_env_ips()
    return ips[1] if len(ips) >= 2 else ""


def _unique_keep_order(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


class DNSAssignmentDB:
    """
    Источник истины для DNS:
      sub_id -> ip + location

    Схема:
      sub_id TEXT PRIMARY KEY
      ip TEXT NOT NULL
      assigned_at INTEGER NOT NULL
      last_checked INTEGER NOT NULL
      location TEXT NOT NULL DEFAULT 'auto'
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with _connect_sqlite(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS dns_assignments (
                    sub_id TEXT PRIMARY KEY,
                    ip TEXT NOT NULL,
                    assigned_at INTEGER NOT NULL,
                    last_checked INTEGER NOT NULL,
                    location TEXT NOT NULL DEFAULT 'auto'
                )
                """
            )

            columns = [row[1] for row in conn.execute("PRAGMA table_info(dns_assignments)").fetchall()]

            if "location" not in columns:
                conn.execute(
                    "ALTER TABLE dns_assignments ADD COLUMN location TEXT NOT NULL DEFAULT 'auto'"
                )

            if "assigned_at" not in columns:
                conn.execute(
                    "ALTER TABLE dns_assignments ADD COLUMN assigned_at INTEGER NOT NULL DEFAULT 0"
                )

            if "last_checked" not in columns:
                conn.execute(
                    "ALTER TABLE dns_assignments ADD COLUMN last_checked INTEGER NOT NULL DEFAULT 0"
                )

            now = int(time.time())
            conn.execute(
                """
                UPDATE dns_assignments
                SET location = COALESCE(NULLIF(trim(location), ''), 'auto')
                """
            )
            conn.execute(
                """
                UPDATE dns_assignments
                SET assigned_at = CASE WHEN assigned_at IS NULL OR assigned_at = 0 THEN ? ELSE assigned_at END,
                    last_checked = CASE WHEN last_checked IS NULL OR last_checked = 0 THEN ? ELSE last_checked END
                """,
                (now, now),
            )

    def get(self, sub_id: str) -> Optional[Dict[str, Any]]:
        try:
            with _connect_sqlite(self.db_path) as conn:
                row = conn.execute(
                    """
                    SELECT sub_id, ip, assigned_at, last_checked, COALESCE(location, 'auto')
                    FROM dns_assignments
                    WHERE sub_id = ?
                    """,
                    (sub_id,),
                ).fetchone()

            if not row:
                return None

            return {
                "sub_id": str(row[0]),
                "ip": str(row[1]),
                "assigned_at": int(row[2]),
                "last_checked": int(row[3]),
                "location": str(row[4] or "auto").strip().lower(),
            }
        except Exception as exc:
            logger.error("DNSAssignmentDB.get(%s) failed: %s", sub_id, exc)
            return None

    def upsert(self, sub_id: str, ip: str, location: str) -> None:
        if not sub_id:
            raise ValueError("sub_id is empty")
        if not ip or not _is_ip(ip):
            raise ValueError(f"invalid ip: {ip!r}")

        location = str(location or "auto").strip().lower()
        now = int(time.time())

        with _connect_sqlite(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO dns_assignments (sub_id, ip, assigned_at, last_checked, location)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(sub_id) DO UPDATE SET
                    ip = excluded.ip,
                    assigned_at = excluded.assigned_at,
                    last_checked = excluded.last_checked,
                    location = excluded.location
                """,
                (sub_id, ip, now, now, location),
            )

    def touch(self, sub_id: str) -> None:
        try:
            with _connect_sqlite(self.db_path) as conn:
                conn.execute(
                    """
                    UPDATE dns_assignments
                    SET last_checked = ?
                    WHERE sub_id = ?
                    """,
                    (int(time.time()), sub_id),
                )
        except Exception as exc:
            logger.error("DNSAssignmentDB.touch(%s) failed: %s", sub_id, exc)

    def count_auto_assignments_by_ip(self) -> Dict[str, int]:
        result: Dict[str, int] = {}
        try:
            with _connect_sqlite(self.db_path) as conn:
                rows = conn.execute(
                    """
                    SELECT ip, COUNT(*)
                    FROM dns_assignments
                    WHERE COALESCE(location, 'auto') = 'auto'
                    GROUP BY ip
                    """
                ).fetchall()

            for ip, count in rows:
                result[str(ip)] = int(count)
        except Exception as exc:
            logger.error("DNSAssignmentDB.count_auto_assignments_by_ip failed: %s", exc)

        return result


class DNSUserLocator:
    """
    Быстрая и стабильная логика:

    1. Смотрим location пользователя из users.
    2. Смотрим dns_assignments.
    3. Если loc=lt/nl -> таблица dns_assignments немедленно приводится к нужному IP.
    4. Если loc=auto -> всегда используем запись location='auto' из dns_assignments;
       если её нет, создаём новую.
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        domains: Optional[List[str]] = None,
        traffic_data_file: Optional[str] = None,
    ) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.db = SubscriptionDB(self.db_path)
        self.assignments = DNSAssignmentDB(self.db_path)

        raw_domains = domains if domains is not None else [
            x.strip()
            for x in os.getenv("KINGLINE_DNS_DOMAINS", "").replace(";", ",").split(",")
            if x.strip()
        ]
        self.domains = [x.strip().strip(".").lower() for x in raw_domains if x.strip()]

        self.traffic_data_file = (
            traffic_data_file
            or os.getenv("TRAFFIC_DATA_FILE")
            or DEFAULT_TRAFFIC_FILE
        )

        self.lt_ip = _get_lt_ip()
        self.nl_ip = _get_nl_ip()

        logger.info(
            "DNSUserLocator initialized: db=%s, domains=%s, lt_ip=%s, nl_ip=%s",
            self.db_path,
            self.domains,
            self.lt_ip,
            self.nl_ip,
        )

    # ---------- helpers ----------

    def describe_ip(self, ip: Optional[str]) -> str:
        if not ip:
            return "Нет IP"
        if self.lt_ip and ip == self.lt_ip:
            return "Литва"
        if self.nl_ip and ip == self.nl_ip:
            return "Германия"
        return "Неизвестная локация"

    def _available_ips(self) -> List[str]:
        return _unique_keep_order([self.lt_ip, self.nl_ip])

    def _normalize_location(self, value: Any) -> str:
        raw = str(value or "").strip().lower()

        if not raw:
            return "auto"

        if raw in {"auto", "default", "any", "random"}:
            return "auto"

        if raw in {"lt", "литва", "lithuania", "🇱🇹"}:
            return "lt"

        if raw in {
            "nl",
            "de",
            "германия",
            "germany",
            "deutschland",
            "нидерланды",
            "netherlands",
            "holland",
            "🇩🇪",
            "🇳🇱",
        }:
            return "nl"

        if self.lt_ip and raw == self.lt_ip.lower():
            return "lt"

        if self.nl_ip and raw == self.nl_ip.lower():
            return "nl"

        if "vpn.kingline.online" in raw:
            return "lt"

        if "net.kingline.online" in raw:
            return "nl"

        return "auto"

    def _extract_sub_id_from_qname(self, qname: str) -> Optional[str]:
        name = qname.rstrip(".").lower()

        for base in self.domains:
            if name == base:
                return None

            suffix = "." + base
            if not name.endswith(suffix):
                continue

            prefix = name[:-len(suffix)]
            if prefix.startswith("."):
                prefix = prefix[1:]

            if not prefix:
                return None
            if "." in prefix:
                return None
            if prefix.startswith("_"):
                return None
            if prefix in RESERVED_SUB_IDS:
                return None

            return prefix

        return None

    def _get_location_by_sub_id(self, sub_id: str) -> str:
        if not sub_id:
            return "auto"

        try:
            val = self.db.get_location_by_sub_id(sub_id)
            return self._normalize_location(val)
        except Exception as exc:
            logger.warning("get_location_by_sub_id(%s) failed: %s", sub_id, exc)
            return "auto"

    def _get_allowed_user_by_sub_id(self, sub_id: str) -> Optional[Dict[str, Any]]:
        if not sub_id:
            return None

        try:
            user = self.db.get_user_by_sub_id(sub_id)
        except Exception as exc:
            logger.warning("get_user_by_sub_id(%s) failed: %s", sub_id, exc)
            return None

        if not user:
            logger.info("DNS sub_id not found in users: %s", sub_id)
            return None

        try:
            if int(user.get("is_blocked") or 0) == 1:
                logger.info("DNS sub_id blocked: %s", sub_id)
                return None
        except Exception:
            pass

        return user

    def _read_latest_metrics(self) -> Dict[str, Dict[str, float]]:
        if not os.path.exists(self.traffic_data_file):
            return {}

        try:
            with open(self.traffic_data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return {}

        if not isinstance(data, list) or not data:
            return {}

        last = data[-1]
        if not isinstance(last, dict):
            return {}

        metrics = last.get("metrics", {})
        if not isinstance(metrics, dict):
            return {}

        result: Dict[str, Dict[str, float]] = {}
        for ip, info in metrics.items():
            if not isinstance(info, dict):
                continue

            try:
                online = float(info.get("online_clients", 0) or 0)
            except Exception:
                online = 0.0

            try:
                latency = float(info.get("latency", -1) or -1)
            except Exception:
                latency = -1.0

            if latency < 0:
                latency = 999999.0

            result[str(ip)] = {
                "online_clients": online,
                "latency": latency,
            }

        return result

    def _choose_new_auto_ip(self) -> Optional[str]:
        candidates = self._available_ips()
        if not candidates:
            return None

        if len(candidates) == 1:
            return candidates[0]

        auto_counts = self.assignments.count_auto_assignments_by_ip()
        metrics = self._read_latest_metrics()

        shuffled = list(candidates)
        random.shuffle(shuffled)

        def score(ip: str) -> Tuple[float, float, float]:
            count = float(auto_counts.get(ip, 0))
            online = float(metrics.get(ip, {}).get("online_clients", 0.0))
            latency = float(metrics.get(ip, {}).get("latency", 999999.0))
            return (count, online, latency)

        shuffled.sort(key=score)
        return shuffled[0]

    def _fixed_ip_for_location(self, location: str) -> Optional[str]:
        location = self._normalize_location(location)
        if location == "lt":
            return self.lt_ip or None
        if location == "nl":
            return self.nl_ip or None
        return None

    # ---------- public API ----------

    def force_set_user_location(self, sub_id: str, location: str) -> Tuple[Optional[str], str, str]:
        """
        Вызывается из бота сразу после смены локации.
        Немедленно синхронизирует dns_assignments.
        """
        if not sub_id:
            return None, "auto", "Нет IP"

        loc = self._normalize_location(location)

        if loc in ("lt", "nl"):
            ip = self._fixed_ip_for_location(loc)
            if not ip:
                raise RuntimeError(f"IP for location {loc!r} is not configured")
            self.assignments.upsert(sub_id, ip, loc)
            logger.info("force_set_user_location: %s -> %s (%s)", sub_id, ip, loc)
            return ip, loc, self.describe_ip(ip)

        # auto
        existing = self.assignments.get(sub_id)
        if existing and existing.get("location") == "auto" and existing.get("ip") in self._available_ips():
            self.assignments.touch(sub_id)
            ip = str(existing["ip"])
            logger.info("force_set_user_location: keep existing auto %s -> %s", sub_id, ip)
            return ip, "auto", self.describe_ip(ip)

        ip = self._choose_new_auto_ip()
        if not ip:
            raise RuntimeError("No available IPs for auto mode")

        self.assignments.upsert(sub_id, ip, "auto")
        logger.info("force_set_user_location: new auto %s -> %s", sub_id, ip)
        return ip, "auto", self.describe_ip(ip)

    def choose_ip_for_qname(self, qname: str) -> Tuple[Optional[str], str, str, Optional[str]]:
        logger.info("DNS query: %s", qname)

        sub_id = self._extract_sub_id_from_qname(qname)
        if not sub_id:
            return None, "auto", "Нет IP", None

        user = self._get_allowed_user_by_sub_id(sub_id)
        if not user:
            return None, "auto", "Нет IP", sub_id

        desired_loc = self._normalize_location(user.get("location_pref") or "auto")
        current = self.assignments.get(sub_id)

        # --- fixed location ---
        if desired_loc in ("lt", "nl"):
            fixed_ip = self._fixed_ip_for_location(desired_loc)
            if not fixed_ip:
                logger.warning("No fixed IP configured for %s", desired_loc)
                return None, desired_loc, "Нет IP", sub_id

            if (
                current
                and current.get("location") == desired_loc
                and current.get("ip") == fixed_ip
            ):
                self.assignments.touch(sub_id)
                logger.info(
                    "FIXED assignment reused: sub_id=%s loc=%s -> %s",
                    sub_id,
                    desired_loc,
                    fixed_ip,
                )
                ip = fixed_ip
            else:
                self.assignments.upsert(sub_id, fixed_ip, desired_loc)
                logger.info(
                    "FIXED assignment updated: sub_id=%s loc=%s -> %s",
                    sub_id,
                    desired_loc,
                    fixed_ip,
                )
                ip = fixed_ip

            loc_name = self.describe_ip(ip)
            logger.info(
                "RESULT: %s -> %s (%s), sub_id=%s, loc=%s",
                qname,
                ip,
                loc_name,
                sub_id,
                desired_loc,
            )
            return ip, desired_loc, loc_name, sub_id

        # --- auto ---
        if (
            current
            and current.get("location") == "auto"
            and current.get("ip") in self._available_ips()
        ):
            ip = str(current["ip"])
            self.assignments.touch(sub_id)
            logger.info(
                "AUTO sticky assignment used: sub_id=%s -> %s (assigned_at=%s)",
                sub_id,
                ip,
                current.get("assigned_at"),
            )
        else:
            ip = self._choose_new_auto_ip()
            if not ip:
                logger.warning("No available IPs for auto sub_id=%s", sub_id)
                return None, "auto", "Нет IP", sub_id

            self.assignments.upsert(sub_id, ip, "auto")
            logger.info("AUTO new assignment created: sub_id=%s -> %s", sub_id, ip)

        loc_name = self.describe_ip(ip)
        logger.info(
            "RESULT: %s -> %s (%s), sub_id=%s, loc=%s",
            qname,
            ip,
            loc_name,
            sub_id,
            "auto",
        )
        return ip, "auto", loc_name, sub_id
