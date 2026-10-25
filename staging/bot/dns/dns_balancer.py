from __future__ import annotations

import json
import logging
import os
import random
import socket
import time
from typing import Literal, Optional, List

LocationCode = Literal["auto", "lt", "nl"]

logger = logging.getLogger("dns_balancer")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _unique_keep_order(items: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _parse_ips_from_env() -> List[str]:
    raw = os.getenv("KINGLINE_DNS_IPS", "")
    return [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]


def _is_ip(value: str) -> bool:
    try:
        socket.inet_aton(value)
        return True
    except Exception:
        return False


def _host_to_ip(host: str) -> Optional[str]:
    if not host:
        return None
    if _is_ip(host):
        return host

    env_lt = os.getenv("KINGLINE_LT_IP", "").strip()
    env_nl = os.getenv("KINGLINE_NL_IP", "").strip()

    panel_domain = os.getenv("KINGLINE_DNS_PANEL_DOMAIN", "").strip().strip(".").lower()
    panel_ip = os.getenv("KINGLINE_DNS_PANEL_IP", "").strip()

    host_l = host.strip().strip(".").lower()

    if panel_domain and host_l == panel_domain and panel_ip:
        return panel_ip

    if host_l.endswith("vpn.kingline.online") and env_lt:
        return env_lt
    if host_l.endswith("net.kingline.online") and env_nl:
        return env_nl

    try:
        return socket.gethostbyname(host_l)
    except Exception:
        return None


class IPProvider:
    def __init__(self) -> None:
        self.lt_ip = ""
        self.nl_ip = ""
        self.pool: List[str] = []
        self._last_refresh = 0.0
        self.refresh_from_env()

    def refresh_from_env(self) -> None:
        env_ips = _parse_ips_from_env()
        env_lt = os.getenv("KINGLINE_LT_IP", "").strip()
        env_nl = os.getenv("KINGLINE_NL_IP", "").strip()

        lt = env_lt or (env_ips[0] if len(env_ips) > 0 else "")
        nl = env_nl or (env_ips[1] if len(env_ips) > 1 else "")

        pool = _unique_keep_order([lt, nl] + env_ips)

        self.lt_ip = lt
        self.nl_ip = nl
        self.pool = pool
        self._last_refresh = time.time()

        logger.info(
            "IPProvider refresh_from_env: LT=%s, NL=%s, pool=%s",
            self.lt_ip,
            self.nl_ip,
            self.pool,
        )

    def update_from_metrics_hosts(self, hosts: List[str]) -> None:
        hosts = [h.strip() for h in hosts if h and h.strip()]
        resolved = _unique_keep_order(
            [_host_to_ip(h) for h in hosts if _host_to_ip(h)]
        )

        env_ips = _parse_ips_from_env()
        env_lt = os.getenv("KINGLINE_LT_IP", "").strip()
        env_nl = os.getenv("KINGLINE_NL_IP", "").strip()

        if env_lt or env_nl or env_ips:
            self.refresh_from_env()
            return

        self.lt_ip = resolved[0] if len(resolved) > 0 else ""
        self.nl_ip = resolved[1] if len(resolved) > 1 else ""
        self.pool = _unique_keep_order(resolved)
        self._last_refresh = time.time()

        logger.info(
            "IPProvider update_from_metrics_hosts: LT=%s, NL=%s, pool=%s",
            self.lt_ip,
            self.nl_ip,
            self.pool,
        )

    def get_random_ip(self) -> Optional[str]:
        if not self.pool:
            return None
        return random.choice(self.pool)

    def get_ip_by_location(self, location: LocationCode) -> Optional[str]:
        if location == "lt" and self.lt_ip:
            return self.lt_ip
        if location == "nl" and self.nl_ip:
            return self.nl_ip
        return self.get_random_ip()

    def should_refresh(self, max_age: int = 60) -> bool:
        return (time.time() - self._last_refresh) > max_age


class DNSBalancer:
    def __init__(self, *, traffic_data_file: Optional[str] = None) -> None:
        self.ip_provider = IPProvider()
        self.traffic_data_file = (
            traffic_data_file
            or os.getenv("TRAFFIC_DATA_FILE")
            or os.path.join(os.path.dirname(__file__), "traffic_data.json")
        )
        self._last_pool_refresh = 0.0
        self._initialized = False

        try:
            self.refresh_pool(force=True)
            self._initialized = True
        except Exception as exc:
            logger.warning("Ошибка инициализации DNSBalancer: %s", exc)
            self.ip_provider.refresh_from_env()
            self._initialized = True

    @property
    def pool(self) -> List[str]:
        return self.ip_provider.pool

    def refresh_pool(self, force: bool = False) -> None:
        if not force and (time.time() - self._last_pool_refresh < 60):
            return

        self.ip_provider.refresh_from_env()

        if not os.path.exists(self.traffic_data_file):
            logger.info("traffic_data.json не найден, используем env")
            self._last_pool_refresh = time.time()
            self._initialized = True
            return

        try:
            with open(self.traffic_data_file, "r", encoding="utf-8") as file:
                data = json.load(file)
        except Exception as exc:
            logger.warning("Ошибка чтения traffic_data.json: %s", exc)
            self._last_pool_refresh = time.time()
            self._initialized = True
            return

        hosts: List[str] = []
        if isinstance(data, list):
            for entry in data[-10:]:
                if not isinstance(entry, dict):
                    continue
                metrics = entry.get("metrics", {})
                if not isinstance(metrics, dict):
                    continue
                for host in metrics.keys():
                    if host and host not in hosts:
                        hosts.append(host)

        if hosts:
            self.ip_provider.update_from_metrics_hosts(hosts)

        self._last_pool_refresh = time.time()
        self._initialized = True

        logger.info("DNSBalancer refreshed, pool=%s", self.ip_provider.pool)

    def choose_ip(self, location: str = "auto") -> Optional[str]:
        if not self._initialized:
            self.refresh_pool(force=True)

        if self.ip_provider.should_refresh(60):
            try:
                self.refresh_pool()
            except Exception as exc:
                logger.warning("Ошибка refresh_pool(): %s", exc)

        if location in ("lt", "nl"):
            ip = self.ip_provider.get_ip_by_location(location)  # type: ignore[arg-type]
        else:
            ip = self.ip_provider.get_random_ip()

        logger.info("DNSBalancer.choose_ip(%s) -> %s", location, ip)
        return ip

    def describe_ip(self, ip: str | None) -> str:
        if not ip:
            return "Нет IP"
        if ip == self.ip_provider.lt_ip:
            return "Литва"
        if ip == self.ip_provider.nl_ip:
            return "Германия"
        return "Неизвестная локация"
