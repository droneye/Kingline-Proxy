import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, List
from urllib.parse import urlparse, urlunparse

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


def build_backup_sub_link(sub_id: str) -> str:
    """
    Строит запасную subscription-ссылку на основе основной,
    заменяя хост вида sub.* -> sub-ru.*.

    Пример:
      https://sub.kingline.online:9443/sub/xxx
      ->
      https://sub-ru.kingline.online:9443/sub/xxx
    """
    backup_base = os.getenv("SUB_BACKUP_BASE", "").strip()
    if backup_base:
        return backup_base.rstrip("/") + "/" + sub_id + (XUI_SUB_SUFFIX or "")
    primary = build_sub_link(sub_id)

    try:
        parsed = urlparse(primary)
        host = parsed.hostname or ""
        if not host:
            return primary.replace("://sub.", "://sub-ru.", 1)

        if host.startswith("sub."):
            new_host = "sub-ru." + host[len("sub."):]
        else:
            # запасной вариант, если вдруг хост не начинается с sub.
            new_host = host.replace("sub", "sub-ru", 1)

        netloc = new_host

        if parsed.port:
            netloc = f"{new_host}:{parsed.port}"

        if parsed.username:
            auth = parsed.username
            if parsed.password:
                auth += f":{parsed.password}"
            netloc = f"{auth}@{netloc}"

        return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        return primary.replace("://sub.", "://sub-ru.", 1)


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
