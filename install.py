#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from wb_cloud_watcher.cli import post_json


DEFAULT_INSTALL_DIR = Path("/opt/wb-cloud-watcher")
DEFAULT_SERVICE_USER = "wbwatcher"
DEFAULT_SERVICE_NAME = "wb-cloud-watcher-bot"
DEFAULT_WB_EMAIL = "ig@gilpert.ru"


@dataclass(frozen=True)
class InstallAnswers:
    install_dir: Path
    service_user: str
    service_name: str
    wb_email: str
    wb_token: str
    telegram_bot_token: str
    telegram_allowed_user_ids: str
    telegram_admin_user_ids: str
    poll_interval_seconds: int
    notify_on_first_run: bool


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install Wirenboard Cloud Watcher Telegram bot on Linux.")
    parser.add_argument("--dry-run", action="store_true", help="Ask questions and create files without systemd changes.")
    args = parser.parse_args(argv)

    ensure_linux()
    source_dir = Path(__file__).resolve().parent
    answers = collect_answers()

    if not args.dry_run:
        ensure_root()
        ensure_service_user(answers.service_user, answers.install_dir)

    copy_project(source_dir, answers.install_dir)
    env_path = write_env(answers)
    service_path = write_service(answers)
    set_permissions(answers.install_dir, answers.service_user, args.dry_run)

    print(f"\nConfig written to {env_path}")
    print(f"Service file written to {service_path}")

    if not args.dry_run:
        install_systemd_service(service_path, answers.service_name)

    backup_path = create_backup(answers.install_dir, env_path, service_path)
    print(f"Backup created: {backup_path}")
    print("Admins can download fresh backups from Telegram with /backup.")

    if not args.dry_run:
        print(f"\nService status: systemctl status {answers.service_name}")
        print(f"Logs: journalctl -u {answers.service_name} -f")

    return 0


def collect_answers() -> InstallAnswers:
    print("Wirenboard Cloud Watcher installer\n")
    install_dir = Path(prompt("Install directory", str(DEFAULT_INSTALL_DIR))).expanduser()
    service_user = prompt("Linux service user", DEFAULT_SERVICE_USER)
    service_name = prompt("systemd service name", DEFAULT_SERVICE_NAME)

    wb_email = prompt("Wirenboard.cloud email", DEFAULT_WB_EMAIL)
    wb_token = prompt_wb_token(wb_email)

    telegram_bot_token = prompt_secret("Telegram bot token from @BotFather")
    telegram_admin_user_ids = prompt(
        "Admin Telegram user IDs, comma-separated. Use 0 first if you need to discover your id",
        "0",
    )
    telegram_allowed_user_ids = prompt(
        "Allowed Telegram user IDs, comma-separated",
        telegram_admin_user_ids,
    )
    poll_interval_seconds = prompt_int("Polling interval seconds", 60)
    notify_on_first_run = prompt_bool("Notify about already-offline controllers on first run", False)

    return InstallAnswers(
        install_dir=install_dir,
        service_user=service_user,
        service_name=service_name,
        wb_email=wb_email,
        wb_token=wb_token,
        telegram_bot_token=telegram_bot_token,
        telegram_allowed_user_ids=telegram_allowed_user_ids,
        telegram_admin_user_ids=telegram_admin_user_ids,
        poll_interval_seconds=poll_interval_seconds,
        notify_on_first_run=notify_on_first_run,
    )


def prompt_wb_token(email: str) -> str:
    existing = prompt_secret("Existing Wirenboard.cloud access token (leave empty to log in)", required=False)
    if existing:
        return existing

    password = prompt_secret("Wirenboard.cloud password")
    totp_code = prompt("TOTP code if enabled (leave empty if not)", "", required=False)
    recovery_code = ""
    if not totp_code:
        recovery_code = prompt("Recovery code if needed (leave empty if not)", "", required=False)

    payload = {"email": email, "password": password}
    if totp_code:
        payload["totpCode"] = totp_code
    if recovery_code:
        payload["recoveryCode"] = recovery_code

    print("Requesting Wirenboard.cloud token...")
    response = post_json("https://wirenboard.cloud/api/v1/auth/token/", payload, timeout_seconds=30)
    if not isinstance(response, dict) or not response.get("access"):
        raise RuntimeError("Wirenboard.cloud token response does not contain access token")
    return str(response["access"])


def copy_project(source_dir: Path, install_dir: Path) -> None:
    if source_dir.resolve() == install_dir.resolve():
        return

    install_dir.mkdir(parents=True, exist_ok=True)
    for item in source_dir.iterdir():
        if item.name in {".git", ".env", ".state", "__pycache__", ".pytest_cache", ".venv", "work", "outputs"}:
            continue
        target = install_dir / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(item, target)


def write_env(answers: InstallAnswers) -> Path:
    env_path = answers.install_dir / ".env"
    values = {
        "WB_API_URL": "https://wirenboard.cloud/api/v1/controllers/?page_size=100",
        "WB_TOKEN": answers.wb_token,
        "WB_AUTH_SCHEME": "Bearer",
        "CONTROLLERS_PATH": "results",
        "ID_FIELD": "serialNumber",
        "NAME_FIELD": "description",
        "ONLINE_FIELD": "isAgentOk",
        "LAST_SEEN_FIELD": "lastAgentPingAt",
        "STATE_FILE": ".state/controllers.json",
        "POLL_INTERVAL_SECONDS": str(answers.poll_interval_seconds),
        "NOTIFY_ON_FIRST_RUN": str(answers.notify_on_first_run).lower(),
        "WB_TIMEOUT_SECONDS": "15",
        "NTFY_SERVER": "https://ntfy.sh",
        "NTFY_TOPIC": "",
        "NTFY_TOKEN": "",
        "NTFY_PRIORITY": "default",
        "TELEGRAM_BOT_TOKEN": answers.telegram_bot_token,
        "TELEGRAM_ALLOWED_USER_IDS": answers.telegram_allowed_user_ids,
        "TELEGRAM_ADMIN_USER_IDS": answers.telegram_admin_user_ids,
        "TELEGRAM_USERS_FILE": ".state/telegram_users.json",
        "TELEGRAM_TIMEOUT_SECONDS": "30",
        "BACKUP_DIR": ".backups",
    }
    env_path.write_text(format_env(values), encoding="utf-8")
    env_path.chmod(0o600)
    return env_path


def write_service(answers: InstallAnswers) -> Path:
    service_path = answers.install_dir / "deploy" / f"{answers.service_name}.service"
    service_path.parent.mkdir(parents=True, exist_ok=True)
    service_path.write_text(
        "\n".join(
            [
                "[Unit]",
                "Description=Wirenboard Cloud Watcher Telegram bot",
                "After=network-online.target",
                "Wants=network-online.target",
                "",
                "[Service]",
                "Type=simple",
                f"WorkingDirectory={answers.install_dir}",
                f"EnvironmentFile={answers.install_dir / '.env'}",
                "ExecStart=/usr/bin/python3 -m wb_cloud_watcher bot",
                "Restart=always",
                "RestartSec=10",
                f"User={answers.service_user}",
                f"Group={answers.service_user}",
                "",
                "[Install]",
                "WantedBy=multi-user.target",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return service_path


def create_backup(install_dir: Path, env_path: Path, service_path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = Path(tempfile.gettempdir()) / f"wb-cloud-watcher-backup-{timestamp}.tar.gz"
    with tarfile.open(backup_path, "w:gz") as archive:
        add_tree(archive, install_dir, "wb-cloud-watcher")
        add_if_exists(archive, env_path, "wb-cloud-watcher/.env")
        add_if_exists(archive, service_path, f"wb-cloud-watcher/deploy/{service_path.name}")
    backup_path.chmod(0o600)
    return backup_path


def install_systemd_service(service_path: Path, service_name: str) -> None:
    system_service = Path("/etc/systemd/system") / f"{service_name}.service"
    shutil.copy2(service_path, system_service)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", service_name])
    active = subprocess.run(["systemctl", "is-active", "--quiet", service_name], check=False)
    if active.returncode == 0:
        run(["systemctl", "restart", service_name])
    else:
        run(["systemctl", "start", service_name])


def ensure_service_user(user: str, home: Path) -> None:
    result = subprocess.run(["id", "-u", user], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode == 0:
        return
    run(["useradd", "--system", "--home", str(home), "--shell", "/usr/sbin/nologin", user])


def set_permissions(install_dir: Path, service_user: str, dry_run: bool) -> None:
    if dry_run:
        return
    run(["chown", "-R", f"{service_user}:{service_user}", str(install_dir)])
    (install_dir / ".env").chmod(0o600)


def format_env(values: dict[str, str]) -> str:
    lines = [f"{key}={escape_env_value(value)}" for key, value in values.items()]
    return "\n".join(lines) + "\n"


def escape_env_value(value: str) -> str:
    if value == "":
        return ""
    if any(char.isspace() or char in {'"', "'", "#"} for char in value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def add_if_exists(archive: tarfile.TarFile, path: Path, arcname: str) -> None:
    if path.exists():
        archive.add(path, arcname=arcname)


def add_tree(archive: tarfile.TarFile, root: Path, arcname: str) -> None:
    excluded_names = {".git", ".state", "__pycache__", ".pytest_cache", ".venv"}
    for path in root.rglob("*"):
        if any(part in excluded_names for part in path.relative_to(root).parts):
            continue
        archive.add(path, arcname=str(Path(arcname) / path.relative_to(root)))


def prompt(label: str, default: str = "", *, required: bool = True) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if not value:
            value = default
        if value or not required:
            return value
        print("Value is required.")


def prompt_secret(label: str, *, required: bool = True) -> str:
    while True:
        value = getpass.getpass(f"{label}: ").strip()
        if value or not required:
            return value
        print("Value is required.")


def prompt_int(label: str, default: int) -> int:
    while True:
        raw = prompt(label, str(default))
        try:
            return int(raw)
        except ValueError:
            print("Enter a valid integer.")


def prompt_bool(label: str, default: bool) -> bool:
    default_text = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{label} [{default_text}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes", "true", "1"}:
            return True
        if raw in {"n", "no", "false", "0"}:
            return False
        print("Enter yes or no.")


def ensure_linux() -> None:
    if os.name != "posix":
        raise SystemExit("install.py is intended to run on Linux servers.")


def ensure_root() -> None:
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise SystemExit("Run installer as root: sudo python3 install.py")


def run(command: Iterable[str]) -> None:
    subprocess.run(list(command), check=True)


if __name__ == "__main__":
    raise SystemExit(main())
