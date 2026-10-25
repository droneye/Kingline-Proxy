#!/usr/bin/python3
"""Set up per-server SSH keys without reading or saving server passwords."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime


WORKSPACE = Path(__file__).resolve().parent
SSH_DIR = Path.home() / ".ssh"
STATUS_FILE = WORKSPACE / "ssh-setup-status.json"
SERVERS = (
    {"ip": "198.51.100.31", "alias": "vpn-de", "name": "Германия"},
    {"ip": "198.51.100.32", "alias": "vpn-dk", "name": "Дания"},
)
BEGIN = "# BEGIN VPN SERVERS MANAGED SETUP"
END = "# END VPN SERVERS MANAGED SETUP"


def key_path(server):
    return SSH_DIR / ("id_ed25519_" + server["ip"].replace(".", "_"))


def write_status(status):
    status["updated_at"] = datetime.now().astimezone().isoformat()
    temporary = STATUS_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, STATUS_FILE)


def prepare():
    SSH_DIR.mkdir(mode=0o700, exist_ok=True)
    SSH_DIR.chmod(0o700)
    for server in SERVERS:
        private = key_path(server)
        public = Path(str(private) + ".pub")
        if not private.exists():
            if public.exists():
                raise RuntimeError("Найден публичный ключ без приватного: " + str(public))
            subprocess.run(
                ["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-a", "100",
                 "-N", "", "-C", "vladislav-mac-" + server["alias"], "-f", str(private)],
                check=True,
            )
        if not public.exists():
            derived = subprocess.run(
                ["/usr/bin/ssh-keygen", "-y", "-P", "", "-f", str(private)],
                capture_output=True, text=True, check=True,
            )
            public.write_text(derived.stdout)
        private.chmod(0o600)
        public.chmod(0o644)

    config = SSH_DIR / "config"
    original = config.read_text() if config.exists() else ""
    remaining = original
    if BEGIN in original or END in original:
        if original.count(BEGIN) != 1 or original.count(END) != 1:
            raise RuntimeError("Неоднозначные границы блока VPN в SSH config.")
        start = original.index(BEGIN)
        finish = original.index(END) + len(END)
        if finish <= start:
            raise RuntimeError("Повреждены границы блока VPN в SSH config.")
        remaining = original[:start] + original[finish:].lstrip("\n")

    blocks = [BEGIN]
    for server in SERVERS:
        blocks.extend([
            "Host " + server["alias"] + " " + server["ip"],
            "  HostName " + server["ip"],
            "  User root",
            '  IdentityFile "' + str(key_path(server)) + '"',
            "  IdentitiesOnly yes",
            "",
        ])
    updated = "\n".join(blocks) + END + "\n\n" + remaining
    if updated != original:
        if config.exists():
            backup = SSH_DIR / ("config.backup-vpn-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
            shutil.copy2(config, backup)
            backup.chmod(0o600)
        fd, temporary = tempfile.mkstemp(prefix="config.vpn-", dir=SSH_DIR)
        try:
            with os.fdopen(fd, "w") as output:
                output.write(updated)
            os.chmod(temporary, 0o600)
            os.replace(temporary, config)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    config.chmod(0o600)


def verify(server, accept_new=False):
    return subprocess.run(
        ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "PreferredAuthentications=publickey",
         "-o", "PasswordAuthentication=no", "-o", "KbdInteractiveAuthentication=no",
         "-o", "StrictHostKeyChecking=" + ("accept-new" if accept_new else "yes"),
         "-o", "ConnectTimeout=15", "-o", "ConnectionAttempts=1",
         "-o", "ControlMaster=no", "-o", "ControlPath=none",
         "-o", "IdentitiesOnly=yes", "-i", str(key_path(server)),
         "root@" + server["ip"], "printf 'SSH_KEY_OK\\n'; id -u"],
        capture_output=True, text=True, timeout=30,
    )


def verified(result):
    return result.returncode == 0 and "SSH_KEY_OK\n0" in result.stdout


def install():
    prepare()
    status = {"state": "running", "servers": {}}
    write_status(status)
    print("Настройка входа с Mac по SSH-ключам", flush=True)
    print("Пароли вводятся только здесь, в Terminal. Символы при вводе не видны.", flush=True)
    print("В чат отправлять пароли не нужно.\n", flush=True)
    for server in SERVERS:
        ip = server["ip"]
        status["servers"][ip] = {"name": server["name"], "alias": server["alias"], "state": "checking"}
        write_status(status)
        print("\n" + server["name"] + ": root@" + ip, flush=True)
        try:
            first = verify(server, accept_new=True)
            if not verified(first):
                status["servers"][ip]["state"] = "awaiting_password"
                write_status(status)
                print("Введите текущий пароль root, когда появится запрос password:", flush=True)
                copied = subprocess.run(
                    ["/usr/bin/ssh-copy-id", "-i", str(key_path(server)) + ".pub",
                     "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15",
                     "-o", "ConnectionAttempts=1", "-o", "IdentitiesOnly=yes",
                     "-o", "ControlMaster=no", "-o", "ControlPath=none", "root@" + ip],
                )
                if copied.returncode != 0:
                    raise RuntimeError("Не удалось установить ключ (код " + str(copied.returncode) + ").")
                status["servers"][ip]["state"] = "verifying"
                write_status(status)
                result = verify(server)
            else:
                result = first
            if not verified(result):
                raise RuntimeError("Проверка входа по ключу не прошла: " + result.stderr.strip())
            status["servers"][ip]["state"] = "verified"
            print("ГОТОВО: вход по ключу проверен. Команда: ssh " + server["alias"], flush=True)
        except Exception as error:
            status["servers"][ip]["state"] = "failed"
            status["servers"][ip]["error"] = str(error)
            print("ОШИБКА: " + str(error), flush=True)
        write_status(status)
    ok = all(value["state"] == "verified" for value in status["servers"].values())
    status["state"] = "complete" if ok else "needs_attention"
    write_status(status)
    print("\nОба сервера готовы." if ok else "\nНе все серверы настроены. Результат сохранён.", flush=True)
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-and-open", action="store_true")
    args = parser.parse_args()
    if args.prepare_and_open:
        prepare()
        write_status({"state": "ready_for_terminal", "servers": {}})
        subprocess.run(["/usr/bin/open", "-a", "Terminal", str(WORKSPACE / "install-ssh-keys.command")], check=True)
        print("SSH-ключи и настройки созданы. Открыт Terminal для установки ключей на серверы.")
        return 0
    return install()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        write_status({"state": "failed", "error": str(error)})
        print("ОШИБКА: " + str(error), file=sys.stderr, flush=True)
        sys.exit(1)
