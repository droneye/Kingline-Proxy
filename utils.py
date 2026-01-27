import json
from datetime import datetime, timezone
from typing import Dict, Any, List

from telegram import Update

from config import SUB_BASE, XUI_SUB_SUFFIX


def get_username_clean(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "user"
    if user.username:
        base = user.username
    else:
        base = f"user{user.id}"
    base = base.replace("@", "").replace(" ", "")
    return base or f"user{user.id}"


def build_sub_link(sub_id: str) -> str:
    url = f"{SUB_BASE}{sub_id}"
    if XUI_SUB_SUFFIX:
        if XUI_SUB_SUFFIX.startswith("?") and "?" not in url:
            url += XUI_SUB_SUFFIX
        else:
            url += XUI_SUB_SUFFIX
    return url


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_clients_from_inbound(inbound: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Достаём список клиентов из inbound под разные форматы:
    settings.clients, inbound.clients, obj.clients.
    """
    clients: List[Dict[str, Any]] = []

    settings = inbound.get("settings")
    obj = inbound.get("obj")
    if isinstance(obj, dict) and not settings:
        settings = obj.get("settings")

    def _loads(val):
        if not isinstance(val, str):
            return val
        try:
            return json.loads(val)
        except Exception:
            return val

    settings = _loads(settings)
    settings = _loads(settings)

    if isinstance(settings, dict) and isinstance(settings.get("clients"), list):
        clients.extend(settings["clients"])
    if isinstance(inbound.get("clients"), list):
        clients.extend(inbound["clients"])
    if isinstance(obj, dict) and isinstance(obj.get("clients"), list):
        clients.extend(obj["clients"])

    uniq: List[Dict[str, Any]] = []
    seen = set()
    for c in clients:
        if not isinstance(c, dict):
            continue
        cid = str(
            c.get("id") or c.get("uuid") or c.get("clientId") or ""
        ).strip()
        key = (cid, c.get("email"), c.get("subId"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)

    return uniq
