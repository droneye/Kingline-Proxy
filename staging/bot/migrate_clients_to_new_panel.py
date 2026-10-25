import asyncio
import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional

import aiohttp
from dotenv import load_dotenv

from xui import XUIClient


DEFAULT_ENV_PATH = "/kingline/.env"
DEFAULT_SOURCE_INBOUND_IDS = [4, 5, 6]
DEFAULT_TARGET_INBOUND_ID = 1

EMAIL_SUFFIX_RE = re.compile(r"\s*#\d+\s*$")


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", ""}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _parse_int_list(raw: str, default: List[int]) -> List[int]:
    raw = _safe_str(raw)
    if not raw:
        return list(default)

    result: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            result.append(int(part))
    return result or list(default)


def _get_source_inbound_ids() -> List[int]:
    # Приоритет:
    # 1) MIGRATE_SOURCE_INBOUNDS
    # 2) INBOUND_IDS
    # 3) default
    migrate_raw = os.getenv("MIGRATE_SOURCE_INBOUNDS", "")
    if _safe_str(migrate_raw):
        return _parse_int_list(migrate_raw, DEFAULT_SOURCE_INBOUND_IDS)

    inbound_ids_raw = os.getenv("INBOUND_IDS", "")
    if _safe_str(inbound_ids_raw):
        return _parse_int_list(inbound_ids_raw, DEFAULT_SOURCE_INBOUND_IDS)

    return list(DEFAULT_SOURCE_INBOUND_IDS)


def _get_target_inbound_id() -> int:
    return _as_int(os.getenv("MIGRATE_TARGET_INBOUND", DEFAULT_TARGET_INBOUND_ID), DEFAULT_TARGET_INBOUND_ID)


def _get_target_flow_override() -> str:
    # Если пусто — сохраняем flow исходного клиента как есть
    return _safe_str(os.getenv("MIGRATE_TARGET_FLOW", ""))


def email_base(email: str) -> str:
    return EMAIL_SUFFIX_RE.sub("", _safe_str(email)).strip()


def make_base_identity(client: Dict[str, Any]) -> str:
    """
    3x-ui требует email. Если у клиента он пустой, делаем стабильный fallback.
    """
    base = email_base(_safe_str(client.get("email")))
    if base:
        return base

    sub_id = _safe_str(client.get("subId") or client.get("subscription"))
    if sub_id:
        return f"user-{sub_id[:12]}"

    cid = _safe_str(client.get("id") or client.get("uuid") or client.get("clientId"))
    if cid:
        return f"user-{cid[:12]}"

    return f"user-{uuid.uuid4().hex[:12]}"


def user_key(client: Dict[str, Any]) -> str:
    sub_id = _safe_str(client.get("subId") or client.get("subscription")).lower()
    base = make_base_identity(client).lower().replace(" ", "")
    return sub_id or base


class XUIClientEx(XUIClient):
    def __init__(self, *args, verify_ssl: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.verify_ssl = verify_ssl
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        jar = aiohttp.CookieJar(unsafe=True)
        connector = None
        if not self.verify_ssl:
            connector = aiohttp.TCPConnector(ssl=False)

        self._session = aiohttp.ClientSession(
            headers=self._headers,
            cookie_jar=jar,
            connector=connector,
        )
        await self.login()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._session:
            await self._session.close()
            self._session = None


def collect_source_panel() -> Dict[str, Any]:
    url = _safe_str(os.getenv("XUI_LT_URL")).rstrip("/")
    username = _safe_str(os.getenv("XUI_LT_USERNAME"))
    password = _safe_str(os.getenv("XUI_LT_PASSWORD"))
    webbasepath = _safe_str(os.getenv("XUI_LT_WEBBASEPATH"))
    verify_ssl = _as_bool(os.getenv("XUI_LT_VERIFY_SSL", "1"), True)

    if not url or not username or not password:
        raise RuntimeError(
            "В .env отсутствуют обязательные XUI_LT_URL / XUI_LT_USERNAME / XUI_LT_PASSWORD"
        )

    return {
        "tag": "LT",
        "url": url,
        "username": username,
        "password": password,
        "webbasepath": webbasepath,
        "verify_ssl": verify_ssl,
    }


def collect_target_panel() -> Dict[str, Any]:
    url = _safe_str(os.getenv("XUI_FR_URL")).rstrip("/")
    username = _safe_str(os.getenv("XUI_FR_USERNAME"))
    password = _safe_str(os.getenv("XUI_FR_PASSWORD"))
    webbasepath = _safe_str(os.getenv("XUI_FR_WEBBASEPATH"))
    verify_ssl = _as_bool(os.getenv("XUI_FR_VERIFY_SSL", "1"), True)

    if not url or not username or not password:
        raise RuntimeError(
            "В .env отсутствуют обязательные XUI_FR_URL / XUI_FR_USERNAME / XUI_FR_PASSWORD"
        )

    return {
        "tag": "FR",
        "url": url,
        "username": username,
        "password": password,
        "webbasepath": webbasepath,
        "verify_ssl": verify_ssl,
    }


def extract_clients(inbound: Dict[str, Any]) -> List[Dict[str, Any]]:
    settings = inbound.get("settings")
    obj = inbound.get("obj") if isinstance(inbound.get("obj"), dict) else {}

    if not settings:
        settings = obj.get("settings")

    settings = XUIClient._safe_json_loads(settings)
    settings = XUIClient._safe_json_loads(settings)

    clients: List[Dict[str, Any]] = []

    if isinstance(settings, dict) and isinstance(settings.get("clients"), list):
        clients.extend(settings["clients"])
    if isinstance(inbound.get("clients"), list):
        clients.extend(inbound["clients"])
    if isinstance(obj.get("clients"), list):
        clients.extend(obj["clients"])

    uniq: List[Dict[str, Any]] = []
    seen = set()

    for c in clients:
        if not isinstance(c, dict):
            continue

        cid = _safe_str(c.get("id") or c.get("uuid") or c.get("clientId"))
        email = _safe_str(c.get("email"))
        sub_id = _safe_str(c.get("subId") or c.get("subscription"))
        key = (cid, email, sub_id)

        if key in seen:
            continue

        seen.add(key)
        uniq.append(c)

    return uniq


def build_target_client(
    sample: Dict[str, Any],
    target_inbound_id: int,
    flow_override: str,
) -> Dict[str, Any]:
    base = make_base_identity(sample)
    sub_id = _safe_str(sample.get("subId") or sample.get("subscription"))
    seed = f"kingline|lt-to-fr|{sub_id}|{base}|{target_inbound_id}"

    tg_id_val = sample.get("tgId") if sample.get("tgId") is not None else sample.get("tgID")
    limit_ip_val = sample.get("limitIp") if sample.get("limitIp") is not None else sample.get("limitIP")

    client: Dict[str, Any] = {
        "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, seed)),
        "email": base,
        "limitIp": _as_int(limit_ip_val, 0),
        "totalGB": 0,  # без лимита
        "expiryTime": _as_int(sample.get("expiryTime"), 0),
        "enable": _as_bool(sample.get("enable"), True),
        "subId": sub_id,
        "tgId": _as_int(tg_id_val, 0),
        "reset": _as_int(sample.get("reset"), 0),
    }

    flow = flow_override or _safe_str(sample.get("flow"))
    if flow:
        client["flow"] = flow

    if sample.get("comment") is not None:
        client["comment"] = sample.get("comment")

    return client


async def add_client_full(xui: XUIClientEx, inbound_id: int, client_obj: Dict[str, Any]) -> None:
    payload = {
        "id": str(inbound_id),
        "settings": json.dumps({"clients": [client_obj]}, ensure_ascii=False),
    }

    st, body = await xui._post_form(xui._panel_api_url("/api/inbounds/addClient"), payload)
    if st != 200:
        raise RuntimeError(f"addClient HTTP {st}: {body}")
    if not isinstance(body, dict) or not body.get("success"):
        raise RuntimeError(f"addClient failed: {body}")

    if not client_obj.get("enable", True):
        await xui.update_client(inbound_id, client_obj["id"], client_obj)


async def collect_users_from_lt(
    source_panel: Dict[str, Any],
    source_inbound_ids: List[int],
) -> Dict[str, Dict[str, Any]]:
    users: Dict[str, Dict[str, Any]] = {}

    async with XUIClientEx(
        source_panel["url"],
        source_panel["username"],
        source_panel["password"],
        webbasepath=source_panel.get("webbasepath") or "",
        verify_ssl=source_panel.get("verify_ssl", True),
    ) as xui:
        inbounds = await xui.list_inbounds()
        inbound_map = {
            int(item["id"]): item for item in inbounds if str(item.get("id", "")).isdigit()
        }

        for src_inbound_id in source_inbound_ids:
            inbound = inbound_map.get(src_inbound_id)
            if not inbound:
                print(f"[WARN] На панели LT нет inbound {src_inbound_id}")
                continue

            clients = extract_clients(inbound)
            print(f"[INFO] LT inbound {src_inbound_id}: найдено клиентов {len(clients)}")

            for client in clients:
                key = user_key(client)
                if not key:
                    continue

                bundle = users.setdefault(
                    key,
                    {
                        "sample": client,
                        "base_email": make_base_identity(client),
                        "subId": _safe_str(client.get("subId") or client.get("subscription")),
                        "from": [],
                    },
                )

                if not bundle.get("subId"):
                    bundle["subId"] = _safe_str(client.get("subId") or client.get("subscription"))

                if not bundle.get("base_email"):
                    bundle["base_email"] = make_base_identity(client)

                if not _safe_str(bundle["sample"].get("subId")) and _safe_str(client.get("subId")):
                    bundle["sample"] = client

                bundle["from"].append(f"LT:{src_inbound_id}:{client.get('email')}")

    return users


async def migrate() -> None:
    env_path = os.getenv("MIGRATE_ENV_PATH", DEFAULT_ENV_PATH)
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        load_dotenv()

    dry_run = _as_bool(os.getenv("MIGRATE_DRY_RUN", "0"), False)
    source_inbound_ids = _get_source_inbound_ids()
    target_inbound_id = _get_target_inbound_id()
    flow_override = _get_target_flow_override()

    source_panel = collect_source_panel()
    target_panel = collect_target_panel()

    print(f"[INFO] env file:          {env_path if os.path.exists(env_path) else '.env'}")
    print(f"[INFO] source panel:      {source_panel['url']} / {source_panel.get('webbasepath') or '/'}")
    print(f"[INFO] target panel:      {target_panel['url']} / {target_panel.get('webbasepath') or '/'}")
    print(f"[INFO] source inbounds:   {source_inbound_ids}")
    print(f"[INFO] target inbound:    {target_inbound_id}")
    print(f"[INFO] dry run:           {dry_run}")
    print("[INFO] traffic limit:     UNLIMITED (totalGB=0)")
    print(f"[INFO] flow override:     {flow_override or '(keep source flow)'}")

    users = await collect_users_from_lt(source_panel, source_inbound_ids)
    print(f"[INFO] найдено уникальных пользователей: {len(users)}")

    async with XUIClientEx(
        target_panel["url"],
        target_panel["username"],
        target_panel["password"],
        webbasepath=target_panel.get("webbasepath") or "",
        verify_ssl=target_panel.get("verify_ssl", True),
    ) as target_xui:
        target_inbounds = await target_xui.list_inbounds()
        target_inbound_map = {
            int(item["id"]): item for item in target_inbounds if str(item.get("id", "")).isdigit()
        }

        if target_inbound_id not in target_inbound_map:
            raise RuntimeError(f"На панели FR отсутствует target inbound id: {target_inbound_id}")

        created = 0
        skipped = 0
        failed = 0

        for key, bundle in users.items():
            sample = bundle["sample"]
            base = bundle["base_email"]
            sub_id = bundle["subId"]

            if not base:
                skipped += 1
                print(f"[SKIP] {key}: пустой base email")
                continue

            target_client = build_target_client(sample, target_inbound_id, flow_override)

            print(f"[USER] {base} | subId={sub_id or '-'} | from={bundle['from'][:3]}")
            print(
                f"       -> inbound {target_inbound_id}: "
                f"email={target_client['email']} | "
                f"flow={target_client.get('flow', '-') or '-'} | "
                f"enable={target_client['enable']} | "
                f"expiry={target_client['expiryTime']} | "
                f"totalGB={target_client['totalGB']}"
            )

            if dry_run:
                created += 1
                continue

            try:
                deleted_total = await target_xui.delete_user_clients(
                    target_inbound_map[target_inbound_id],
                    username_clean=base,
                    sub_id=sub_id,
                )

                if deleted_total:
                    print(f"       deleted old target clients: {deleted_total}")

                await add_client_full(target_xui, target_inbound_id, target_client)
                created += 1

            except Exception as e:
                failed += 1
                print(f"[FAIL] {base}: {e}")

        print("\n[RESULT]")
        print(f"  created target clients: {created}")
        print(f"  skipped users:          {skipped}")
        print(f"  failed users:           {failed}")


if __name__ == "__main__":
    asyncio.run(migrate())