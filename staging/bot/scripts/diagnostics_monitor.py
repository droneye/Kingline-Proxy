#!/usr/bin/env python3
"""Read-only network and host health probes, run once per minute by systemd."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from diagnostics import configure, event


def http_probe(target, url):
    started, status, error = time.monotonic(), None, None
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            status = response.status
            payload = json.loads(response.read(4096)) if "application/json" in response.headers.get("Content-Type", "") else response.read(4096)
            if isinstance(payload, dict) and payload.get("ok") is not True:
                error = "UnhealthyResponse"
    except urllib.error.HTTPError as exc:
        status, error = exc.code, type(exc).__name__
    except Exception as exc:
        error = type(exc).__name__
    duration = round((time.monotonic() - started) * 1000, 3)
    event("http_probe", "WARNING" if error or duration > 1000 else "INFO",
          target=target, status=status, error=error, duration_ms=duration)


def network_probe(target, host, port):
    started, error = time.monotonic(), None
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except Exception as exc:
        error = type(exc).__name__
    event("tcp_probe", "WARNING" if error else "INFO", target=target,
          host=host, port=port, error=error, duration_ms=round((time.monotonic() - started) * 1000, 3))
    try:
        result = subprocess.run(["ping", "-n", "-c", "1", "-W", "2", "--", host],
                                capture_output=True, text=True, timeout=4, env={**os.environ, "LC_ALL": "C"})
        match = re.search(r"time[=<]([\d.]+)\s*ms", result.stdout)
        event("icmp_probe", target=target, host=host, reachable=result.returncode == 0,
              rtt_ms=float(match[1]) if match else None,
              packet_loss_percent=0 if result.returncode == 0 else 100)
    except Exception as exc:
        event("icmp_probe", "WARNING", target=target, error=type(exc).__name__)


def dns_probe():
    from dnslib import DNSRecord
    started, error, rcode = time.monotonic(), None, None
    try:
        query = DNSRecord.question("sub.kingline.online", "A")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(3)
            client.sendto(query.pack(), ("127.0.0.1", 53))
            response = DNSRecord.parse(client.recv(4096))
        rcode = response.header.rcode
        if rcode or not response.rr:
            error = "InvalidDNSResponse"
    except Exception as exc:
        error = type(exc).__name__
    event("dns_probe", "WARNING" if error else "INFO", target="local-dns", error=error,
          rcode=rcode, duration_ms=round((time.monotonic() - started) * 1000, 3))


def host_snapshot():
    disk = shutil.disk_usage("/")
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        memory[key] = int(value.strip().split()[0]) * 1024
    free_ratio = disk.free / disk.total
    event("host_resources", "WARNING" if free_ratio < 0.1 else "INFO",
          disk_total_bytes=disk.total, disk_free_bytes=disk.free,
          disk_used_percent=round(100 * disk.used / disk.total, 2),
          memory_total_bytes=memory["MemTotal"], memory_available_bytes=memory["MemAvailable"],
          swap_free_bytes=memory["SwapFree"], load_1m=os.getloadavg()[0], cpus=os.cpu_count())
    units = ["kingline_bot.service", "kingline-site-api.service", "x-ui.service",
             "nginx.service", "kingline-stream-signaling.service", "mediamtx.service"]
    result = subprocess.run(["systemctl", "show", *units, "-p", "Id", "-p", "ActiveState",
                             "-p", "SubState", "-p", "NRestarts", "-p", "MemoryCurrent",
                             "-p", "CPUUsageNSec"], capture_output=True, text=True, timeout=5, check=True)
    for block in result.stdout.strip().split("\n\n"):
        props = dict(line.split("=", 1) for line in block.splitlines() if "=" in line)
        event("service_health", "INFO" if props.get("ActiveState") == "active" else "ERROR",
              unit=props.get("Id"), active=props.get("ActiveState"), state=props.get("SubState"),
              restarts=props.get("NRestarts"), memory_bytes=props.get("MemoryCurrent"),
              cpu_usage_ns=props.get("CPUUsageNSec"))


def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    recorder = configure("monitor")
    try:
        host_snapshot()
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = [
                pool.submit(http_probe, "site-local", "http://127.0.0.1:8787/api/health"),
                pool.submit(http_probe, "subscription-local", "http://127.0.0.1:8080/health"),
                pool.submit(http_probe, "site-public", "https://kingline.online/api/health"),
                pool.submit(dns_probe),
            ]
            for tag in os.getenv("XUI_TAGS", "LT,NL").split(","):
                tag = tag.strip().upper()
                parsed = urlsplit(os.getenv(f"XUI_{tag}_URL", ""))
                if parsed.hostname:
                    futures.append(pool.submit(network_probe, tag, parsed.hostname,
                                               parsed.port or (443 if parsed.scheme == "https" else 80)))
            for future in futures:
                try:
                    future.result()
                except Exception as exc:
                    event("monitor_error", "ERROR", error=type(exc).__name__)
    finally:
        recorder.close()


if __name__ == "__main__":
    main()
