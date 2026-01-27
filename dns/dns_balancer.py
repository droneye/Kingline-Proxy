from __future__ import annotations

import os
import random
import socket
import time
import json
from typing import Literal, Optional, List

LocationCode = Literal["auto", "lt", "nl"]

def _parse_ips_from_env() -> List[str]:
    raw = os.getenv("KINGLINE_DNS_IPS", "")
    parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    return parts

def _is_ip(s: str) -> bool:
    try:
        socket.inet_aton(s)
        return True
    except Exception:
        return False

def _host_to_ip(host: str) -> Optional[str]:
    """Преобразует домен в IP с учетом env переменных"""
    if not host:
        return None
    if _is_ip(host):
        return host

    # Сначала проверяем панель
    panel_domain = os.getenv("KINGLINE_DNS_PANEL_DOMAIN", "").strip().strip(".")
    panel_ip = os.getenv("KINGLINE_DNS_PANEL_IP", "").strip()
    if panel_domain and host.endswith(panel_domain) and panel_ip:
        return panel_ip

    # Проверяем явные маппинги
    env_lt = os.getenv("KINGLINE_LT_IP", "").strip()
    env_nl = os.getenv("KINGLINE_NL_IP", "").strip()
    
    # Специфичные домены из ваших панелей
    if "vpn.kingline.online" in host and env_lt:
        return env_lt
    if "net.kingline.online" in host and env_nl:
        return env_nl

    # Пробуем DNS резолв
    try:
        return socket.gethostbyname(host)
    except Exception:
        return None

class IPProvider:
    def __init__(self) -> None:
        self.lt_ip = ""
        self.nl_ip = ""
        self.pool: List[str] = []
        self._last_refresh = 0
        self._update_pool_from_env()

    def _update_pool_from_env(self) -> None:
        ips = _parse_ips_from_env()

        self.lt_ip = os.getenv("KINGLINE_LT_IP") or (ips[0] if len(ips) > 0 else "")
        self.nl_ip = os.getenv("KINGLINE_NL_IP") or (ips[1] if len(ips) > 1 else "")

        # Пул IP для авто-выбора
        self.pool = [ip for ip in (self.lt_ip, self.nl_ip) if ip] or ips
        self._last_refresh = time.time()
        print(f"IPProvider инициализирован: LT={self.lt_ip}, NL={self.nl_ip}, pool={self.pool}")

    def update_from_list(self, hosts: List[str]) -> None:
        hosts = [h.strip() for h in hosts if h and h.strip()]
        if not hosts:
            self._update_pool_from_env()
            return

        resolved: List[str] = []
        seen = set()

        for h in hosts:
            ip = _host_to_ip(h)  # Используем нашу функцию преобразования
            if ip and ip not in seen:
                seen.add(ip)
                resolved.append(ip)

        if resolved:
            # Сохраняем lt/nl если они есть в resolved
            env_lt = os.getenv("KINGLINE_LT_IP")
            env_nl = os.getenv("KINGLINE_NL_IP")
            
            if env_lt and env_lt in resolved:
                self.lt_ip = env_lt
            else:
                self.lt_ip = resolved[0] if resolved else (env_lt or "")

            if env_nl and env_nl in resolved:
                self.nl_ip = env_nl
            else:
                self.nl_ip = resolved[1] if len(resolved) > 1 else (env_nl or "")

            self.pool = resolved
            self._last_refresh = time.time()
            print(f"IPProvider обновлен из списка: LT={self.lt_ip}, NL={self.nl_ip}, pool={self.pool}")

    def get_random_ip(self) -> Optional[str]:
        if not self.pool:
            return None
        return random.choice(self.pool)

    def get_ip_by_location(self, location: LocationCode) -> Optional[str]:
        if location == "lt" and self.lt_ip:
            return self.lt_ip
        if location == "nl" and self.nl_ip:
            return self.nl_ip
        return self.get_random_ip()  # Fallback

    def should_refresh(self, max_age: int = 300) -> bool:
        return time.time() - self._last_refresh > max_age

class DNSBalancer:
    def __init__(self, *, traffic_data_file: Optional[str] = None) -> None:
        self.ip_provider = IPProvider()
        # Явно указываем путь к файлу трафика
        self.traffic_data_file = traffic_data_file or os.path.join(os.path.dirname(__file__), "traffic_data.json")
        self._last_pool_refresh = 0
        self._initialized = False
        
        print(f"DNSBalancer инициализирован, traffic_file: {self.traffic_data_file}")
        
        # Сразу обновляем пул при инициализации
        try:
            self.refresh_pool()
            self._initialized = True
        except Exception as e:
            print(f"Ошибка инициализации DNSBalancer: {e}")
            self._initialized = False

    def _update_from_env(self) -> None:
        """Обновляет пул IP из переменных окружения"""
        print("Обновление пула IP из переменных окружения")
        self.ip_provider._update_pool_from_env()
        self._last_pool_refresh = time.time()
        self._initialized = True

    def refresh_pool(self) -> None:
        if time.time() - self._last_pool_refresh < 300 and self._initialized:
            return
            
        if not os.path.exists(self.traffic_data_file):
            print(f"Файл трафика не найден: {self.traffic_data_file}")
            # Используем IP из переменных окружения как fallback
            self._update_from_env()
            return

        try:
            with open(self.traffic_data_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            print(f"Загружено {len(data)} записей из traffic_data.json")
        except Exception as e:
            print(f"Ошибка загрузки traffic_data.json: {e}")
            self._update_from_env()
            return

        hosts = []
        if isinstance(data, list) and data:
            # Берем последние 10 записей для актуальности
            for entry in data[-10:]:
                if not isinstance(entry, dict):
                    continue
                metrics = entry.get("metrics", {})
                if isinstance(metrics, dict):
                    for host in metrics.keys():
                        if host and host not in hosts:
                            hosts.append(host)
                            
        if hosts:
            print(f"Найдены хосты в traffic_data: {hosts}")
            self.ip_provider.update_from_list(hosts)
            self._last_pool_refresh = time.time()
            self._initialized = True
        else:
            print("Не найдено хостов в traffic_data.json, используем env переменные")
            self._update_from_env()

    def choose_ip(self, location: str = "auto") -> Optional[str]:
        if not self._initialized:
            print("DNSBalancer не инициализирован, пытаемся восстановить...")
            try:
                self.refresh_pool()
            except Exception as e:
                print(f"Не удалось восстановить DNSBalancer: {e}")
                return None

        # Всегда обновляем пул при выборе IP если прошло больше минуты
        if self.ip_provider.should_refresh(60):
            try:
                self.refresh_pool()
            except Exception as e:
                print(f"Ошибка обновления пула: {e}")

        if location in ("lt", "nl"):
            ip = self.ip_provider.get_ip_by_location(location)
        else:
            ip = self.ip_provider.get_random_ip()

        print(f"DNSBalancer выбрал IP для локации {location}: {ip}")
        return ip

    def describe_ip(self, ip: str | None) -> str:
        if not ip:
            return "Нет IP"
        if ip == self.ip_provider.lt_ip:
            return "Литва"
        if ip == self.ip_provider.nl_ip:
            return "Нидерланды"
        return "Неизвестная локация"