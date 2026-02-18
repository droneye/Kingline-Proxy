# refferal/ref_db.py

import sqlite3
import json
from typing import Any, Dict, List, Optional, Tuple
from contextlib import closing


class ReferralDB:
    """
    Хранилище реферальной информации в отдельной БД ref.db.

    Таблица referrals:
      - inviter_tg_id : tg_id пригласившего (уникальный)
      - ref_code      : реферальный код пригласившего (уникальный)
      - invited_json  : JSON-массив объектов:
                        [{"tg_id": 123, "rewarded": 0}, ...]
                        rewarded:
                          0 = бонус не выдавался (eligible)
                          2 = бонус зарезервирован (pending) — защита от гонок/повторов
                          1 = бонус выдан (done)
    """

    def __init__(self, path: str = "ref.db"):
        self.path = path
        self._init_db()

    # ===== internals =====

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, check_same_thread=False)

    def _init_db(self) -> None:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    inviter_tg_id INTEGER UNIQUE NOT NULL,
                    ref_code TEXT UNIQUE NOT NULL,
                    invited_json TEXT DEFAULT '[]'
                )
                """
            )
            conn.commit()

    # ===== helpers =====

    @staticmethod
    def _row_to_dict(row: tuple) -> Dict[str, Any]:
        if not row:
            return {}
        cols = ["id", "inviter_tg_id", "ref_code", "invited_json"]
        return dict(zip(cols, row))

    @staticmethod
    def _parse_invited(invited_json: str) -> List[Dict[str, Any]]:
        if not invited_json:
            return []
        try:
            data = json.loads(invited_json)
            if isinstance(data, list):
                return data
            return []
        except Exception:
            return []

    @staticmethod
    def _dump_invited(items: List[Dict[str, Any]]) -> str:
        return json.dumps(items, ensure_ascii=False)

    # ===== basic queries =====

    def get_by_inviter(self, inviter_tg_id: int) -> Optional[Dict[str, Any]]:
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                SELECT id, inviter_tg_id, ref_code, invited_json
                FROM referrals
                WHERE inviter_tg_id = ?
                """,
                (inviter_tg_id,),
            )
            row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def get_by_code(self, code: str) -> Optional[Dict[str, Any]]:
        code = (code or "").strip().upper()
        if not code:
            return None

        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                SELECT id, inviter_tg_id, ref_code, invited_json
                FROM referrals
                WHERE ref_code = ?
                """,
                (code,),
            )
            row = cur.fetchone()
        return self._row_to_dict(row) if row else None

    def get_invitation_for_invited(self, invited_tg_id: int) -> Optional[Tuple[int, bool]]:
        """
        Найти, есть ли запись о том, что invited_tg_id использовал чей-то код.

        Возвращает (inviter_tg_id, rewarded_already) или None.
        rewarded_already=True если статус rewarded != 0 (то есть 1 или 2).
        """
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute("SELECT inviter_tg_id, invited_json FROM referrals")
            rows = cur.fetchall()

        for inviter_tg_id, invited_json in rows:
            items = self._parse_invited(invited_json or "[]")
            for it in items:
                if int(it.get("tg_id") or 0) == int(invited_tg_id):
                    status = int(it.get("rewarded") or 0)
                    rewarded_already = (status != 0)  # 1=done, 2=pending
                    return int(inviter_tg_id), rewarded_already

        return None

    # ===== Anti-abuse helpers =====

    def has_invited(self, inviter_tg_id: int, invited_tg_id: int) -> bool:
        """
        True если inviter_tg_id уже добавлял invited_tg_id в свой invited_json.
        Используется для запрета взаимных рефералов A<->B.
        """
        row = self.get_by_inviter(inviter_tg_id)
        if not row:
            return False
        items = self._parse_invited(row.get("invited_json") or "[]")
        for it in items:
            if int(it.get("tg_id") or 0) == int(invited_tg_id):
                return True
        return False

    def try_reserve_reward(self, inviter_tg_id: int, invited_tg_id: int) -> bool:
        """
        Атомарно (в рамках одной строки) резервирует бонус для пары inviter->invited:
        rewarded: 0 -> 2 (pending)

        Возвращает True если удалось зарезервировать, иначе False.
        """
        row = self.get_by_inviter(inviter_tg_id)
        if not row:
            return False

        invited_items = self._parse_invited(row.get("invited_json") or "[]")
        changed = False

        for it in invited_items:
            if int(it.get("tg_id") or 0) == int(invited_tg_id):
                status = int(it.get("rewarded") or 0)
                if status == 0:
                    it["rewarded"] = 2  # pending
                    changed = True
                else:
                    # already pending or done
                    return False

        if not changed:
            return False

        invited_json = self._dump_invited(invited_items)
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE referrals
                SET invited_json = ?
                WHERE inviter_tg_id = ?
                """,
                (invited_json, inviter_tg_id),
            )
            conn.commit()
        return True

    def unreserve_reward(self, inviter_tg_id: int, invited_tg_id: int) -> None:
        """
        Откат резерва (pending=2 -> 0), если продление не удалось.
        """
        row = self.get_by_inviter(inviter_tg_id)
        if not row:
            return

        invited_items = self._parse_invited(row.get("invited_json") or "[]")
        changed = False

        for it in invited_items:
            if int(it.get("tg_id") or 0) == int(invited_tg_id):
                status = int(it.get("rewarded") or 0)
                if status == 2:
                    it["rewarded"] = 0
                    changed = True

        if not changed:
            return

        invited_json = self._dump_invited(invited_items)
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE referrals
                SET invited_json = ?
                WHERE inviter_tg_id = ?
                """,
                (invited_json, inviter_tg_id),
            )
            conn.commit()

    # ===== public API =====

    def get_or_create_code(self, inviter_tg_id: int) -> str:
        """
        Гарантирует наличие реферального кода для пользователя.
        Возвращает сам код.
        """
        row = self.get_by_inviter(inviter_tg_id)
        if row and row.get("ref_code"):
            return str(row["ref_code"])

        code = self._generate_unique_code()

        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO referrals (inviter_tg_id, ref_code, invited_json)
                VALUES (?, ?, '[]')
                ON CONFLICT(inviter_tg_id) DO UPDATE SET
                    ref_code = excluded.ref_code
                """,
                (inviter_tg_id, code),
            )
            conn.commit()

        return code

    def add_invited_user(self, inviter_tg_id: int, invited_tg_id: int) -> None:
        """
        Добавляет invited_tg_id в список приглашённых для inviter_tg_id,
        если его там ещё нет. rewarded по умолчанию = 0.
        """
        row = self.get_by_inviter(inviter_tg_id)
        if not row:
            ref_code = self._generate_unique_code()
            invited_items: List[Dict[str, Any]] = []
        else:
            ref_code = row["ref_code"]
            invited_items = self._parse_invited(row.get("invited_json") or "[]")

        for it in invited_items:
            if int(it.get("tg_id") or 0) == int(invited_tg_id):
                return

        invited_items.append({"tg_id": int(invited_tg_id), "rewarded": 0})
        invited_json = self._dump_invited(invited_items)

        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                INSERT INTO referrals (inviter_tg_id, ref_code, invited_json)
                VALUES (?, ?, ?)
                ON CONFLICT(inviter_tg_id) DO UPDATE SET
                    ref_code = excluded.ref_code,
                    invited_json = excluded.invited_json
                """,
                (inviter_tg_id, ref_code, invited_json),
            )
            conn.commit()

    def mark_rewarded(self, inviter_tg_id: int, invited_tg_id: int) -> None:
        """
        Помечает, что за invited_tg_id бонус выдан: rewarded=1.
        (Если было pending=2 — переводим в 1. Если было 0 — тоже ставим 1.)
        """
        row = self.get_by_inviter(inviter_tg_id)
        if not row:
            return

        invited_items = self._parse_invited(row.get("invited_json") or "[]")
        changed = False

        for it in invited_items:
            if int(it.get("tg_id") or 0) == int(invited_tg_id):
                status = int(it.get("rewarded") or 0)
                if status != 1:
                    it["rewarded"] = 1
                    changed = True

        if not changed:
            return

        invited_json = self._dump_invited(invited_items)
        with self._connect() as conn, closing(conn.cursor()) as cur:
            cur.execute(
                """
                UPDATE referrals
                SET invited_json = ?
                WHERE inviter_tg_id = ?
                """,
                (invited_json, inviter_tg_id),
            )
            conn.commit()

    # ===== вспомогательные методы =====

    def _generate_unique_code(self, length: int = 8) -> str:
        """
        Генерирует уникальный реферальный код (A-Z0-9) заданной длины.
        """
        import secrets
        import string

        alphabet = string.ascii_uppercase + string.digits
        while True:
            code = "".join(secrets.choice(alphabet) for _ in range(length))
            if not self.get_by_code(code):
                return code
