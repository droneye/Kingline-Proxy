import asyncio
import json
import logging
import os
from shutil import which

# жёсткие таймауты, чтобы никогда не висело
OOKLA_TIMEOUT = 45
CLI_TIMEOUT = 45

class CheckService:
    """
    Возвращает компактный отчёт:
    ⚡️ Speedtest (Ookla|CLI)
    Down: X Mbps
    Up:   Y Mbps
    Ping: Z ms
    Location: Country
    """
    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or logging.getLogger(__name__)

    async def _run_cmd(self, cmd: list[str], timeout: int) -> tuple[int, str, str]:
        # Немного «чистим» окружение, чтобы утилита не пыталась открыть TTY и пр.
        env = os.environ.copy()
        env["TERM"] = "dumb"
        env["LC_ALL"] = env.get("LC_ALL", "C")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return proc.returncode, stdout.decode(errors="ignore"), stderr.decode(errors="ignore")
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return 124, "", f"Timeout after {timeout}s"

    @staticmethod
    def _mbps_from_ookla_bytes_per_sec(bps_bytes: float) -> float:
        # Ookla JSON: bandwidth — BYTES/sec
        return round((bps_bytes * 8) / 1_000_000, 2)

    @staticmethod
    def _mbps_from_cli_bits_per_sec(bits_per_sec: float) -> float:
        # speedtest-cli JSON: bits/sec
        return round(bits_per_sec / 1_000_000, 2)

    async def _try_ookla(self) -> str | None:
        if not which("speedtest"):
            return None
        rc, out, err = await self._run_cmd(
            ["speedtest", "--accept-license", "--accept-gdpr", "-f", "json"],
            timeout=OOKLA_TIMEOUT,
        )
        if rc != 0 or not out.strip():
            self.logger.warning(f"Ookla failed rc={rc}, err={err.strip()}")
            return None
        try:
            data = json.loads(out)
            dl = self._mbps_from_ookla_bytes_per_sec(data.get("download", {}).get("bandwidth", 0))
            ul = self._mbps_from_ookla_bytes_per_sec(data.get("upload", {}).get("bandwidth", 0))
            ping_ms = round(data.get("ping", {}).get("latency", 0), 2)
            server = data.get("server", {}) or {}
            country = (server.get("country") or server.get("location") or "").strip()
            return (
                "⚡️ Speedtest (Ookla)\n"
                f"Down: {dl} Mbps\n"
                f"Up: {ul} Mbps\n"
                f"Ping: {ping_ms} ms\n"
                f"Location: {country}"
            )
        except Exception as e:
            self.logger.warning(f"Parse Ookla JSON failed: {e}")
            return None

    async def _try_cli(self) -> str | None:
        if not which("speedtest-cli"):
            return None
        rc, out, err = await self._run_cmd(["speedtest-cli", "--json", "--secure"], timeout=CLI_TIMEOUT)
        if rc != 0 or not out.strip():
            self.logger.warning(f"speedtest-cli failed rc={rc}, err={err.strip()}")
            return None
        try:
            data = json.loads(out)
            dl = self._mbps_from_cli_bits_per_sec(float(data.get("download", 0)))
            ul = self._mbps_from_cli_bits_per_sec(float(data.get("upload", 0)))
            ping_ms = round(float(data.get("ping", 0)), 2)
            server = data.get("server", {}) or {}
            country = (server.get("country") or "").strip()
            return (
                "⚡️ Speedtest (CLI)\n"
                f"Down: {dl} Mbps\n"
                f"Up: {ul} Mbps\n"
                f"Ping: {ping_ms} ms\n"
                f"Location: {country}"
            )
        except Exception as e:
            self.logger.warning(f"Parse CLI JSON failed: {e}")
            return None

    async def do_speedtest(self) -> str:
        # Порядок: Ookla → CLI
        res = await self._try_ookla()
        if res:
            return res

        res = await self._try_cli()
        if res:
            return res

        # Ни один вариант не сработал
        have_ookla = bool(which("speedtest"))
        have_cli = bool(which("speedtest-cli"))
        if not have_ookla and not have_cli:
            return (
                "Не удалось запустить speedtest.\n"
                "Установите одну из утилит на сервере:\n"
                "• apt install -y speedtest-cli\n"
                "  или официальный Ookla speedtest (репозиторий Ookla)."
            )
        return "Speedtest не дал результата (таймаут/ошибка). Повторите чуть позже."

    async def check_and_render(self) -> str:
        speed = await self.do_speedtest()
        return f"✅ Результаты с сервера:\n\n{speed}"
