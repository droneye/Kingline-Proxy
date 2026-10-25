#!/usr/bin/env python3
"""Reset one user's VPN backend while preserving billing and website auth."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from handlers.billing_db import BillingDB, OPTIONAL_PANEL_TAGS
from utils import extract_clients_from_inbound
from xui import XUIClient
from config import DB_PATH, db


BACKUP_ROOT = Path("/var/backups/kingline-user-reset")
VPN_HISTORY_TABLES = {
    "web_xui_access_daily_counts": "username = ?",
    "web_xui_access_events": "username = ?",
    "web_xui_client_aliases": "username = ? OR tg_id = ?",
    "web_xui_device_profiles": "username = ?",
    "web_xui_ip_history": "username = ? OR tg_id = ?",
    "web_xui_user_insights_cache": "username = ?",
    "web_xui_user_last_seen": "username = ? OR tg_id = ?",
    "web_xui_user_samples": "username = ? OR tg_id = ?",
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(os.path.abspath(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _auth_fingerprint(conn: sqlite3.Connection, tg_id: int) -> dict[str, Any]:
    accounts = _rows(
        conn,
        "SELECT * FROM web_accounts WHERE linked_tg_id = ? OR web_tg_id = ? ORDER BY id",
        (tg_id, tg_id),
    )
    account_ids = [int(row["id"]) for row in accounts]
    placeholders = ",".join("?" for _ in account_ids) or "NULL"
    sessions = _rows(
        conn,
        f"SELECT * FROM web_sessions WHERE account_id IN ({placeholders}) ORDER BY account_id, created_at",
        tuple(account_ids),
    )
    password_material = "\n".join(
        f"{row['id']}:{row.get('__REDACTED_FOR_STAGING__') or ''}" for row in accounts
    ).encode()
    session_material = "\n".join(
        f"{row['account_id']}:{row.get('token_hash') or ''}" for row in sessions
    ).encode()
    return {
        "account_ids": account_ids,
        "emails": [row.get("email") for row in accounts],
        "__REDACTED_FOR_STAGING___fingerprint": hashlib.sha256(password_material).hexdigest(),
        "session_hash_fingerprint": hashlib.sha256(session_material).hexdigest(),
        "session_count": len(sessions),
    }


def _client_id(client: dict[str, Any]) -> str:
    return str(client.get("id") or client.get("uuid") or client.get("clientId") or "").strip()


async def _panel_clients(sub_id: str, username: str) -> tuple[list[dict[str, Any]], list[str]]:
    found: list[dict[str, Any]] = []
    errors: list[str] = []
    for panel in BillingDB._get_panels():
        tag = str(panel.get("tag") or "XUI").strip().upper()
        try:
            async with XUIClient(
                root_url=panel["url"],
                username=panel["username"],
                password=panel["password"],
                webbasepath=panel.get("webbasepath", "") or "",
                verify_ssl=bool(panel.get("verify_ssl", True)),
            ) as client:
                inbounds = await client.list_inbounds()
        except Exception as exc:
            errors.append(f"{tag}: {exc}")
            continue

        wanted = {str(spec["inbound_id"]) for spec in BillingDB._panel_slot_plan(tag)}
        for inbound in inbounds:
            inbound_id = inbound.get("id")
            if inbound_id is None and isinstance(inbound.get("obj"), dict):
                inbound_id = inbound["obj"].get("id")
            if inbound_id is None or str(inbound_id) not in wanted:
                continue
            for raw in extract_clients_from_inbound(inbound):
                if not isinstance(raw, dict) or not _client_id(raw):
                    continue
                if BillingDB._client_matches_user(raw, username, sub_id):
                    found.append(
                        {
                            "panel": tag,
                            "panel_url": panel["url"],
                            "inbound_id": int(inbound_id),
                            "client": raw,
                        }
                    )
    return found, errors


def _backup_database_rows(conn: sqlite3.Connection, tg_id: int, username: str, sub_id: str) -> dict[str, Any]:
    auth = _auth_fingerprint(conn, tg_id)
    account_ids = auth["account_ids"]
    placeholders = ",".join("?" for _ in account_ids) or "NULL"
    payload: dict[str, Any] = {
        "users": _rows(conn, "SELECT * FROM users WHERE tg_id = ?", (tg_id,)),
        "payments": _rows(conn, "SELECT * FROM payments WHERE tg_id = ? ORDER BY id", (tg_id,)),
        "dns_assignments": _rows(conn, "SELECT * FROM dns_assignments WHERE sub_id = ?", (sub_id,)),
        "web_accounts": _rows(
            conn,
            "SELECT * FROM web_accounts WHERE linked_tg_id = ? OR web_tg_id = ? ORDER BY id",
            (tg_id, tg_id),
        ),
        "web_sessions": _rows(
            conn,
            f"SELECT * FROM web_sessions WHERE account_id IN ({placeholders})",
            tuple(account_ids),
        ),
        "auth_fingerprint": auth,
    }
    for table, where in VPN_HISTORY_TABLES.items():
        params: tuple[Any, ...] = (username, tg_id) if "tg_id" in where else (username,)
        payload[table] = _rows(conn, f"SELECT * FROM {table} WHERE {where}", params)
    return payload


def _write_backup(payload: dict[str, Any], tg_id: int) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_dir = BACKUP_ROOT / f"{stamp}-tg{tg_id}"
    backup_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    backup_file = backup_dir / "state.json"
    backup_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.chmod(backup_file, 0o600)
    return backup_file


async def _restore_panel_clients(snapshot: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    panels = {str(p.get("tag") or "XUI").strip().upper(): p for p in BillingDB._get_panels()}
    for item in snapshot:
        panel = panels.get(item["panel"])
        raw = item["client"]
        if not panel:
            errors.append(f"{item['panel']}: panel config missing")
            continue
        try:
            async with XUIClient(
                root_url=panel["url"],
                username=panel["username"],
                password=panel["password"],
                webbasepath=panel.get("webbasepath", "") or "",
                verify_ssl=bool(panel.get("verify_ssl", True)),
            ) as client:
                await client.add_client_append(
                    inbound_id=item["inbound_id"],
                    email=str(raw.get("email") or ""),
                    flow=str(raw.get("flow") or ""),
                    sub_id=str(raw.get("subId") or raw.get("subscription") or ""),
                    expiry_time=int(raw.get("expiryTime") or 0),
                    client_id=_client_id(raw),
                    total_gb=int(raw.get("totalGB") or 0),
                    limit_ip=int(raw.get("limitIp") or 0),
                    tg_id=int(raw.get("tgId") or 0),
                    reset=int(raw.get("reset") or 0),
                )
        except Exception as exc:
            errors.append(f"{item['panel']}/{item['inbound_id']}: {exc}")
    return errors


def _validate_required_slots(snapshot: list[dict[str, Any]], errors: list[str]) -> None:
    panels = BillingDB._get_panels()
    required_tags = {
        str(panel.get("tag") or "XUI").strip().upper()
        for panel in panels
        if str(panel.get("tag") or "XUI").strip().upper() not in OPTIONAL_PANEL_TAGS
    }
    failed_required = [error for error in errors if error.split(":", 1)[0] in required_tags]
    if failed_required:
        raise RuntimeError("Required panel inspection failed: " + "; ".join(failed_required))

    actual = {(item["panel"], int(item["inbound_id"])) for item in snapshot}
    missing: list[str] = []
    for panel in panels:
        tag = str(panel.get("tag") or "XUI").strip().upper()
        if tag not in required_tags:
            continue
        for spec in BillingDB._panel_slot_plan(tag):
            key = (tag, int(spec["inbound_id"]))
            if key not in actual:
                missing.append(f"{tag}/{spec['inbound_id']}")
    if missing:
        raise RuntimeError("Fresh VPN profile is missing required slots: " + ", ".join(missing))


def _clear_vpn_database_state(
    conn: sqlite3.Connection,
    *,
    tg_id: int,
    user_id: int,
    username: str,
    old_sub_id: str,
    new_sub_id: str,
    new_client_ids: list[str],
    key_created_at: str,
    key_expires_at: str,
) -> dict[str, int]:
    deleted: dict[str, int] = {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM dns_assignments WHERE sub_id = ?", (old_sub_id,))
        for table, where in VPN_HISTORY_TABLES.items():
            params: tuple[Any, ...] = (username, tg_id) if "tg_id" in where else (username,)
            cursor = conn.execute(f"DELETE FROM {table} WHERE {where}", params)
            deleted[table] = int(cursor.rowcount or 0)

        padded = (new_client_ids + [None, None, None])[:3]
        cursor = conn.execute(
            """
            UPDATE users
            SET sub_id = ?, sub_name = ?, key_created_at = ?, key_expires_at = ?,
                client_id1 = ?, client_id2 = ?, client_id3 = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND tg_id = ?
            """,
            (
                new_sub_id,
                new_sub_id,
                key_created_at,
                key_expires_at,
                padded[0],
                padded[1],
                padded[2],
                user_id,
                tg_id,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("users row changed during reset")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return deleted


async def run(tg_id: int, execute: bool, requested_new_sub_id: str | None = None) -> dict[str, Any]:
    with _connect() as conn:
        user_rows = _rows(conn, "SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        if len(user_rows) != 1:
            raise RuntimeError(f"Expected one users row for tg_id={tg_id}, got {len(user_rows)}")
        user = user_rows[0]
        username = str(user.get("username") or f"user{tg_id}").strip()
        old_sub_id = str(user.get("sub_id") or "").strip()
        if not old_sub_id:
            raise RuntimeError("User has no existing sub_id")
        if not user.get("key_expires_at"):
            raise RuntimeError("User has no paid-access expiry to preserve")
        db_rows = _backup_database_rows(conn, tg_id, username, old_sub_id)
        auth_before = _auth_fingerprint(conn, tg_id)

    old_panel_clients, old_panel_errors = await _panel_clients(old_sub_id, username)
    _validate_required_slots(old_panel_clients, old_panel_errors)
    backup_file = _write_backup(
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "target_tg_id": tg_id,
            "database": db_rows,
            "panel_clients": old_panel_clients,
            "panel_errors": old_panel_errors,
        },
        tg_id,
    )

    summary: dict[str, Any] = {
        "mode": "execute" if execute else "dry-run",
        "tg_id": tg_id,
        "username": username,
        "old_sub_id": old_sub_id,
        "backup": str(backup_file),
        "old_panel_client_count": len(old_panel_clients),
        "panel_inspection_errors": old_panel_errors,
        "website_account_ids": auth_before["account_ids"],
        "website_session_count": auth_before["session_count"],
        "preserved_expiry": user["key_expires_at"],
    }
    if not execute:
        return summary

    new_sub_id = (requested_new_sub_id or "").strip() or BillingDB.generate_sub_id(
        tg_id,
        str(user.get("tg_name") or ""),
        username,
    )
    if new_sub_id == old_sub_id or "/" in new_sub_id or not new_sub_id:
        raise RuntimeError("Invalid replacement sub_id")
    expiry_ms = int(
        datetime.strptime(str(user["key_expires_at"]), "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=timezone.utc)
        .timestamp()
        * 1000
    )
    new_clients: list[dict[str, Any]] = []
    try:
        async with BillingDB.subscription_update_lock(f"vpn-reset:{tg_id}"):
            deleted_count = await BillingDB.delete_all_user_clients(old_sub_id, username)
            leftovers, leftover_errors = await _panel_clients(old_sub_id, username)
            if leftover_errors:
                raise RuntimeError("Could not verify old-client deletion: " + "; ".join(leftover_errors))
            if leftovers:
                raise RuntimeError(f"Old VPN clients remain after deletion: {len(leftovers)}")

            client_ids, _ = await BillingDB.create_clients_for_user(
                sub_id=new_sub_id,
                username_clean=username,
                expiry_time_ms=expiry_ms,
            )
            new_clients, new_panel_errors = await _panel_clients(new_sub_id, username)
            _validate_required_slots(new_clients, new_panel_errors)

            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            with _connect() as conn:
                deleted_history = _clear_vpn_database_state(
                    conn,
                    tg_id=tg_id,
                    user_id=int(user["id"]),
                    username=username,
                    old_sub_id=old_sub_id,
                    new_sub_id=new_sub_id,
                    new_client_ids=client_ids,
                    key_created_at=now_iso,
                    key_expires_at=str(user["key_expires_at"]),
                )
    except Exception as exc:
        rollback_errors: list[str] = []
        if new_sub_id:
            try:
                await BillingDB.delete_all_user_clients(new_sub_id, username)
            except Exception as rollback_exc:
                rollback_errors.append(f"new-client cleanup: {rollback_exc}")
        current_old, _ = await _panel_clients(old_sub_id, username)
        if len(current_old) < len(old_panel_clients):
            rollback_errors.extend(await _restore_panel_clients(old_panel_clients))
        suffix = f"; rollback errors: {rollback_errors}" if rollback_errors else "; old VPN profile restored"
        raise RuntimeError(f"VPN reset failed: {exc}{suffix}") from exc

    with _connect() as conn:
        user_after = _rows(conn, "SELECT * FROM users WHERE tg_id = ?", (tg_id,))[0]
        auth_after = _auth_fingerprint(conn, tg_id)
        old_dns_count = conn.execute(
            "SELECT COUNT(*) FROM dns_assignments WHERE sub_id = ?", (old_sub_id,)
        ).fetchone()[0]

    if auth_after != auth_before:
        raise RuntimeError("Website account or sessions changed during VPN reset")
    if user_after["sub_id"] != new_sub_id:
        raise RuntimeError("Database did not switch to the fresh sub_id")

    summary.update(
        {
            "new_sub_id": new_sub_id,
            "deleted_panel_client_count": deleted_count,
            "fresh_panel_client_count": len(new_clients),
            "fresh_panel_tags": sorted({item["panel"] for item in new_clients}),
            "vpn_history_rows_deleted": sum(deleted_history.values()),
            "old_dns_rows_remaining": int(old_dns_count),
            "website_auth_unchanged": True,
            "tariff": user_after["tariff"],
            "subscription_started_at": user_after["subscription_started_at"],
            "subscription_expires_at": user_after["subscription_expires_at"],
        }
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tg-id", type=int, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--new-sub-id")
    args = parser.parse_args()
    result = asyncio.run(run(args.tg_id, args.execute, args.new_sub_id))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
