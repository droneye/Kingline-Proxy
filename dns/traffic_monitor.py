#!/usr/bin/env python3
# traffic_monitor.py
from __future__ import annotations

import os
import re
import asyncio
import time
import json
import logging
import socket
import statistics
import aiohttp
from typing import Dict, Any, List, Optional, Tuple, Callable
from urllib.parse import urlparse

# ---- Settings ----
MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(MODULE_DIR, "traffic_data.json")
LOG_FILE = os.path.join(MODULE_DIR, "traffic_monitor.log")

SAMPLE_RATE = 5.0
WINDOW_SECS = 60.0
CONNECT_TIMEOUT = 1.5
LOG_LEVEL = os.getenv("TRAFFIC_MONITOR_LOG_LEVEL", "INFO")

# Create log dir if missing
os.makedirs(MODULE_DIR, exist_ok=True)

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
LOG = logging.getLogger("traffic_monitor")


def _host_to_ip(host: str) -> Optional[str]:
    """Преобразует домен в IP для traffic monitor."""
    if not host:
        return None

    # already IP?
    try:
        socket.inet_aton(host)
        return host
    except OSError:
        pass

    env_lt = os.getenv("KINGLINE_LT_IP", "").strip()
    env_nl = os.getenv("KINGLINE_NL_IP", "").strip()

    if "vpn.kingline.online" in host and env_lt:
        return env_lt
    if "net.kingline.online" in host and env_nl:
        return env_nl

    try:
        return socket.gethostbyname(host)
    except (socket.gaierror, OSError):
        return host


def _base_host(base_url: str) -> str:
    """Extract host from base url."""
    try:
        p = urlparse(base_url)
        return p.hostname or base_url.rstrip("/").split("/")[-1]
    except Exception:
        return base_url.rstrip("/").split("/")[-1]


class TrafficMonitor:
    """
    Асинхронный монитор трафика для сбора метрик с 3x-ui (X-UI) панелей.

    ВАЖНО (по твоему тесту):
    - Логин работает по POST https://<host>/login (НЕ /panel/login)
    - server status: GET  https://<host>/panel/api/server/status
    - inbounds list: GET  https://<host>/panel/api/inbounds/list
    - onlines:       POST https://<host>/panel/api/inbounds/onlines (GET 404)
    """

    def __init__(
        self,
        *,
        poll_interval: float = 60.0,
        on_cycle_complete: Optional[Callable[[Dict[str, Dict[str, Any]]], None]] = None,
    ):
        self.servers = self._discover_panels()
        self._session: Optional[aiohttp.ClientSession] = None
        self._stop: bool = False
        self._last_cleanup: float = time.time()
        self._task: Optional[asyncio.Task] = None
        self._poll_interval = float(poll_interval)
        self._on_cycle_complete = on_cycle_complete

        LOG.info(
            "TrafficMonitor инициализирован. Серверов: %d, интервал: %.1fs",
            len(self.servers),
            self._poll_interval,
        )

    # ---------- Discovery ----------
    def _discover_panels(self) -> Dict[str, Dict[str, str]]:
        """
        Обнаруживает панели.

        Поддерживает ДВА режима:

        1) Legacy (как у тебя было):
           XUI_BASE_1, XUI_COOKIE_1
           XUI_BASE_2, XUI_COOKIE_2
           ...

        2) Правильный (рекомендуемый) — логинимся сами:
           XUI_LT_URL / XUI_LT_USERNAME / XUI_LT_PASSWORD
           XUI_NL_URL / XUI_NL_USERNAME / XUI_NL_PASSWORD
        """
        servers: Dict[str, Dict[str, str]] = {}

        # --- New style (username/password) ---
        lt_url = os.getenv("XUI_LT_URL", "").strip()
        lt_user = os.getenv("XUI_LT_USERNAME", "").strip()
        lt_pass = os.getenv("XUI_LT_PASSWORD", "").strip()

        nl_url = os.getenv("XUI_NL_URL", "").strip()
        nl_user = os.getenv("XUI_NL_USERNAME", "").strip()
        nl_pass = os.getenv("XUI_NL_PASSWORD", "").strip()

        def add_named(name: str, base: str, user: str, pwd: str) -> None:
            host = _base_host(base)
            servers[host] = {
                "base": base.strip(),
                "username": user.strip(),
                "password": pwd.strip(),
            }
            LOG.debug("Добавлен сервер (login): %s -> %s", host, base)

        if lt_url and lt_user and lt_pass:
            add_named("LT", lt_url, lt_user, lt_pass)
        if nl_url and nl_user and nl_pass:
            add_named("NL", nl_url, nl_user, nl_pass)

        # --- Legacy cookie style (fallback) ---
        pattern = re.compile(r"^XUI_BASE_(\d+)$")
        for k, v in list(os.environ.items()):
            m = pattern.match(k)
            if not m:
                continue

            idx = m.group(1)
            base = v.strip()
            cookie = os.environ.get(f"XUI_COOKIE_{idx}", "").strip()
            if not cookie:
                LOG.warning("Найден %s, но XUI_COOKIE_%s не задан — пропускаем", base, idx)
                continue

            host = _base_host(base)
            if host in servers:
                # если уже добавили через username/password — не перетираем
                continue

            servers[host] = {"base": base, "cookie": cookie}
            LOG.debug("Добавлен сервер (cookie): %s -> %s", host, base)

        LOG.info("Обнаружено %d серверов для мониторинга: %s", len(servers), ", ".join(servers.keys()))
        return servers

    # ---------- Session ----------
    async def _ensure_session(self) -> None:
        if not self._session:
            self._session = aiohttp.ClientSession(
                cookie_jar=aiohttp.CookieJar(unsafe=True),
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "X-Requested-With": "XMLHttpRequest",
                    "User-Agent": "traffic-monitor/1.0",
                },
                timeout=aiohttp.ClientTimeout(total=15),
            )

    # ---------- URL helpers ----------
    @staticmethod
    def _panel_api_url(base: str, path: str) -> str:
        """
        Всегда строим URL как:
          https://host[/...]/panel + path
        где path начинается с /api/...
        """
        b = base.rstrip("/")
        if b.endswith("/panel"):
            return f"{b}{path}"
        return f"{b}/panel{path}"

    def _choose_status_url(self, base: str) -> str:
        return self._panel_api_url(base, "/api/server/status")

    def _choose_onlines_url(self, base: str) -> str:
        # ВАЖНО: по тесту onlines есть только POST на /panel/api/inbounds/onlines
        return self._panel_api_url(base, "/api/inbounds/onlines")

    def _choose_inbounds_list_url(self, base: str) -> str:
        return self._panel_api_url(base, "/api/inbounds/list")

    @staticmethod
    def _login_url(base: str) -> str:
        """
        ВАЖНО: по тесту логин работает по https://host/login (без /panel).
        """
        b = base.rstrip("/")
        return f"{b}/login"

    # ---------- Auth ----------
    async def _login_if_needed(self, cfg: Dict[str, str]) -> bool:
        """
        Если cfg содержит username/password — логинимся и держим cookie в cookie_jar.
        Возвращает True если логин успешен (или не нужен), False если провал.
        """
        user = cfg.get("username", "").strip()
        pwd = cfg.get("password", "").strip()
        if not (user and pwd):
            # cookie-mode, логин не нужен
            return True

        await self._ensure_session()
        url = self._login_url(cfg["base"])
        payload = {"username": user, "password": pwd}

        try:
            async with self._session.post(url, data=payload, allow_redirects=False) as resp:
                txt = await resp.text()
                if resp.status != 200:
                    LOG.warning("LOGIN HTTP %d %s: %s", resp.status, url, txt[:200])
                    return False
                try:
                    data = json.loads(txt)
                except Exception:
                    LOG.warning("LOGIN non-json %s: %s", url, txt[:200])
                    return False

                if isinstance(data, dict) and data.get("success") is True:
                    return True

                LOG.warning("LOGIN failed %s: success=%r msg=%r body=%s", url, data.get("success"), data.get("msg"), txt[:200])
                return False
        except Exception as e:
            LOG.warning("LOGIN error %s: %s", url, e)
            return False

    # ---------- API fetch ----------
    async def _request_json(
        self,
        method: str,
        url: str,
        cfg: Dict[str, str],
        *,
        timeout: int = 10,
        data: Optional[Dict[str, Any]] = None,
        retry_on_unauth: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Универсальный JSON запрос.
        - добавляет Cookie header если cfg в cookie-mode
        - проверяет success=false
        - если unauthorized и есть username/password — делает релогин и повторяет 1 раз
        """
        await self._ensure_session()

        headers: Dict[str, str] = {}
        cookie = cfg.get("cookie", "").strip()
        if cookie:
            headers["Cookie"] = cookie

        try:
            req = self._session.request
            async with req(
                method.upper(),
                url,
                headers=headers if headers else None,
                data=data,
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as resp:
                txt = await resp.text()

                if resp.status != 200:
                    LOG.debug("HTTP %d %s: %s", resp.status, url, txt[:200])
                    return None

                try:
                    payload = json.loads(txt)
                except Exception as e:
                    LOG.warning("Non-JSON %s: %s (err=%s)", url, txt[:200], e)
                    return None

                if not isinstance(payload, dict):
                    LOG.warning("Unexpected JSON type %s from %s", type(payload).__name__, url)
                    return None

                # ВАЖНО: проверяем success=false
                if payload.get("success") is False:
                    msg = str(payload.get("msg") or "")
                    LOG.debug("API success=false %s msg=%r body=%s", url, msg, txt[:200])

                    if retry_on_unauth and ("unauth" in msg.lower() or "login" in msg.lower()):
                        # попробуем релогиниться и повторить 1 раз (если username/password)
                        if cfg.get("username") and cfg.get("password"):
                            ok = await self._login_if_needed(cfg)
                            if ok:
                                return await self._request_json(
                                    method,
                                    url,
                                    cfg,
                                    timeout=timeout,
                                    data=data,
                                    retry_on_unauth=False,
                                )
                    return None

                return payload

        except asyncio.TimeoutError:
            LOG.debug("Timeout %s %s", method, url)
            return None
        except Exception as e:
            LOG.debug("Request error %s %s: %s", method, url, e)
            return None

    async def _fetch_status_obj(self, cfg: Dict[str, str]) -> Dict[str, Any]:
        """
        Возвращает obj из /panel/api/server/status
        """
        # если есть логин — убедимся что он есть
        await self._login_if_needed(cfg)

        url = self._choose_status_url(cfg["base"])
        payload = await self._request_json("GET", url, cfg, timeout=10)
        if not payload:
            return {}

        obj = payload.get("obj")
        if isinstance(obj, dict):
            return obj

        # иногда могут возвращать сразу dict с полями — но в твоем тесте obj=dict
        return {}

    @staticmethod
    def _extract_bytes(obj: Dict[str, Any]) -> Tuple[int, int]:
        """Извлекает значения переданных/принятых байт из объекта статуса."""
        up = down = 0

        nio = obj.get("netIO")
        if isinstance(nio, dict):
            try:
                up = int(nio.get("up") or 0)
                down = int(nio.get("down") or 0)
            except Exception:
                up = down = 0

        if up == 0 and down == 0:
            nt = obj.get("netTraffic")
            if isinstance(nt, dict):
                try:
                    up = int(nt.get("sent") or 0)
                    down = int(nt.get("recv") or 0)
                except Exception:
                    up = down = 0

        return int(up), int(down)

    async def _get_online_clients(self, cfg: Dict[str, str]) -> int:
        """
        ВАЖНО: по твоему тесту:
          GET  /panel/api/inbounds/onlines -> 404
          POST /panel/api/inbounds/onlines -> 200
        """
        await self._login_if_needed(cfg)

        url = self._choose_onlines_url(cfg["base"])
        payload = await self._request_json("POST", url, cfg, timeout=10)
        if not payload:
            return 0

        obj = payload.get("obj")
        if isinstance(obj, list):
            return len(obj)
        if isinstance(obj, dict):
            return len(obj)

        # иногда может быть list на верхнем уровне (редко)
        return 0

    async def _measure_latency(self, host_or_base: str) -> float:
        """Измеряет задержку до сервера (TCP connect к 443)."""
        try:
            host = host_or_base
            if "://" in host:
                host = host.split("://", 1)[1].split("/", 1)[0]
            host = host.split(":", 1)[0]

            start = time.monotonic()
            fut = asyncio.open_connection(host, 443)
            reader, writer = await asyncio.wait_for(fut, timeout=CONNECT_TIMEOUT)
            latency = time.monotonic() - start
            writer.close()
            await writer.wait_closed()
            return max(0.0, latency)
        except asyncio.TimeoutError:
            LOG.debug("Latency timeout for %s", host_or_base)
            return -1.0
        except Exception as e:
            LOG.debug("Latency error for %s: %s", host_or_base, e)
            return -1.0

    async def _measure_speed(self, cfg: Dict[str, str]) -> Dict[str, float]:
        """
        Измеряет скорость по дельте (up+down) за ~10 секунд, опрашивая status.
        """
        samples: List[float] = []

        obj0 = await self._fetch_status_obj(cfg)
        up0, down0 = self._extract_bytes(obj0)

        last_up, last_down = up0, down0
        last_t = time.monotonic()
        end = last_t + 10.0

        while time.monotonic() < end and not self._stop:
            await asyncio.sleep(2.0)
            obj = await self._fetch_status_obj(cfg)
            upn, downn = self._extract_bytes(obj)
            tn = time.monotonic()

            delta = max(0, (upn - last_up) + (downn - last_down))
            dt = max(1e-6, tn - last_t)
            inst = delta / dt
            samples.append(inst)

            last_up, last_down = upn, downn
            last_t = tn

        if not samples:
            return {"avg": 0.0, "peak": 0.0}

        return {"avg": float(statistics.mean(samples)), "peak": float(max(samples))}

    # ---------- Main collection ----------
    async def collect_metrics(self) -> Dict[str, Dict[str, Any]]:
        metrics: Dict[str, Dict[str, Any]] = {}

        if not self.servers:
            LOG.warning("Нет серверов для мониторинга!")
            return metrics

        LOG.info("Начинаем сбор метрик для %d серверов", len(self.servers))

        for host, cfg in self.servers.items():
            host_ip = _host_to_ip(host) or host
            try:
                LOG.debug("Сбор метрик для %s", host)

                # IMPORTANT: online должен быть POST /panel/api/inbounds/onlines
                online_clients = await self._get_online_clients(cfg)

                speed_stats = await self._measure_speed(cfg)
                latency = await self._measure_latency(cfg["base"])

                metrics[host_ip] = {
                    "online_clients": online_clients,
                    "speed_avg": speed_stats["avg"],
                    "speed_peak": speed_stats["peak"],
                    "latency": latency,
                    "timestamp": time.time(),
                    "original_host": host,
                }

                LOG.info(
                    "Собраны метрики для %s -> %s: online=%d, speed_avg=%.1fB/s, latency=%.3fs",
                    host,
                    host_ip,
                    online_clients,
                    speed_stats["avg"],
                    latency,
                )

            except Exception as e:
                LOG.error("Ошибка сбора метрик для %s: %s", host, e)
                metrics[host_ip] = {
                    "online_clients": 0,
                    "speed_avg": 0.0,
                    "speed_peak": 0.0,
                    "latency": -1.0,
                    "timestamp": time.time(),
                    "original_host": host,
                }

        return metrics

    # ---------- Persistence ----------
    def load_data(self) -> List[Dict[str, Any]]:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        LOG.debug("Загружено %d записей из %s", len(data), DATA_FILE)
                        return data
        except Exception as e:
            LOG.error("Ошибка загрузки данных из %s: %s", DATA_FILE, e)
        return []

    def save_data(self, data: List[Dict[str, Any]]) -> None:
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            LOG.debug("Сохранено %d записей в %s", len(data), DATA_FILE)
        except Exception as e:
            LOG.error("Ошибка сохранения данных в %s: %s", DATA_FILE, e)

    def cleanup_old_data(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        now = time.time()
        six_hours_ago = now - 6 * 3600
        old_count = len(data)
        cleaned = [entry for entry in data if entry.get("timestamp", 0) > six_hours_ago]
        new_count = len(cleaned)

        if old_count != new_count:
            LOG.info("Очистка данных: %d -> %d (удалено %d)", old_count, new_count, old_count - new_count)
        return cleaned

    # ---------- Cycle ----------
    async def run_monitoring_cycle(self) -> None:
        LOG.info("Запуск цикла мониторинга...")

        try:
            metrics = await self.collect_metrics()
            data = self.load_data()

            current_time = time.time()
            if current_time - self._last_cleanup >= 6 * 3600:
                data = self.cleanup_old_data(data)
                self._last_cleanup = current_time

            entry = {"timestamp": current_time, "metrics": metrics}
            data.append(entry)
            self.save_data(data)

            if self._on_cycle_complete:
                try:
                    self._on_cycle_complete(metrics)
                except Exception as e:
                    LOG.warning("on_cycle_complete callback failed: %s", e)

            LOG.info("Метрики собраны. Серверы: %s", list(metrics.keys()))

        except Exception as e:
            LOG.error("Критическая ошибка в цикле мониторинга: %s", e)

    async def _background_loop(self) -> None:
        LOG.info("TrafficMonitor background loop started")
        cycle_count = 0

        while not self._stop:
            try:
                cycle_count += 1
                LOG.debug("Цикл мониторинга #%d", cycle_count)
                await self.run_monitoring_cycle()
            except Exception as e:
                LOG.error("Ошибка в цикле мониторинга #%d: %s", cycle_count, e)

            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                LOG.info("Фоновая задача отменена")
                break

        LOG.info("TrafficMonitor background loop exiting")

    def start_background(self) -> None:
        if self._task and not self._task.done():
            LOG.warning("TrafficMonitor уже запущен")
            return

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        self._stop = False
        self._task = loop.create_task(self._background_loop())
        LOG.info("Фоновая задача TrafficMonitor создана")

    async def stop(self) -> None:
        LOG.info("Остановка TrafficMonitor...")
        self._stop = True

        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
                LOG.info("Фоновая задача корректно завершена")
            except asyncio.TimeoutError:
                LOG.warning("Таймаут ожидания завершения фоновой задачи")
                self._task.cancel()
            except Exception as e:
                LOG.warning("Ошибка при остановке фоновой задачи: %s", e)
            finally:
                self._task = None

        if self._session:
            await self._session.close()
            self._session = None
            LOG.info("HTTP сессия закрыта")


__all__ = ["TrafficMonitor", "DATA_FILE"]
