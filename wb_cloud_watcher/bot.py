from __future__ import annotations

import html
import json
import sys
import tarfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .cli import (
    Config,
    Controller,
    controller_to_state,
    env,
    fetch_controllers,
    find_changes,
    format_notification_body,
    load_state,
    remote_access_url,
    save_state,
)


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    allowed_user_ids: set[int]
    admin_user_ids: set[int]
    users_file: Path
    backup_dir: Path
    timeout_seconds: int


def read_telegram_config() -> TelegramConfig:
    bot_token = env("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required")

    allowed_user_ids = parse_allowed_user_ids(env("TELEGRAM_ALLOWED_USER_IDS"))
    admin_user_ids = parse_allowed_user_ids(env("TELEGRAM_ADMIN_USER_IDS"))
    if not admin_user_ids:
        admin_user_ids = allowed_user_ids
    if not allowed_user_ids and not admin_user_ids:
        raise SystemExit("TELEGRAM_ALLOWED_USER_IDS or TELEGRAM_ADMIN_USER_IDS is required")

    users_file = Path(env("TELEGRAM_USERS_FILE", ".state/telegram_users.json"))
    backup_dir = Path(env("BACKUP_DIR", ".backups"))

    return TelegramConfig(
        bot_token=bot_token,
        allowed_user_ids=allowed_user_ids,
        admin_user_ids=admin_user_ids,
        users_file=users_file,
        backup_dir=backup_dir,
        timeout_seconds=int(env("TELEGRAM_TIMEOUT_SECONDS", "30")),
    )


def parse_allowed_user_ids(raw: str) -> set[int]:
    values: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        item = part.strip()
        if not item:
            continue
        values.add(int(item))
    return values


def run_bot(wb_config: Config, tg_config: TelegramConfig) -> int:
    offset: int | None = None
    next_check_at = 0.0
    print("telegram bot started")

    while True:
        now = time.time()
        if now >= next_check_at:
            check_and_notify(wb_config, tg_config)
            next_check_at = now + wb_config.poll_interval_seconds

        updates = telegram_request(tg_config, "getUpdates", {"timeout": 10, "offset": offset})
        for update in updates.get("result", []):
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
            handle_update(wb_config, tg_config, update)


def check_and_notify(wb_config: Config, tg_config: TelegramConfig) -> None:
    try:
        controllers = fetch_controllers(wb_config)
        previous = load_state(wb_config.state_file)
        first_run = not wb_config.state_file.exists()
        changes = find_changes(
            previous,
            controllers,
            first_run=first_run,
            notify_on_first_run=wb_config.notify_on_first_run,
        )
        current = {controller.controller_id: controller_to_state(controller) for controller in controllers}
        save_state(wb_config.state_file, current)
    except Exception as exc:
        print(f"background check failed: {exc}", file=sys.stderr)
        return

    for controller, old_online in changes:
        text = html_pre(format_notification_body(controller, old_online))
        keyboard = inline_keyboard(
            [
                [url_button("Open remote access", remote_access_url(controller.controller_id))],
                [callback_button("Refresh controller", f"controller:{controller.controller_id}")],
                [callback_button("All controllers", "status:all")],
            ]
        )
        for user_id in get_allowed_user_ids(tg_config):
            send_message(tg_config, user_id, text, keyboard)


def handle_update(wb_config: Config, tg_config: TelegramConfig, update: dict[str, Any]) -> None:
    if "callback_query" in update:
        handle_callback(wb_config, tg_config, update["callback_query"])
        return

    message = update.get("message")
    if not isinstance(message, dict):
        return

    chat = message.get("chat", {})
    from_user = message.get("from", {})
    chat_id = chat.get("id")
    user_id = from_user.get("id")
    if not isinstance(chat_id, int) or not isinstance(user_id, int):
        return

    if not is_allowed(tg_config, user_id):
        send_message(tg_config, chat_id, f"Access denied.\nYour Telegram user id: <code>{user_id}</code>")
        return

    text = str(message.get("text") or "").strip()
    if text.startswith("/id"):
        send_message(tg_config, chat_id, f"Your Telegram user id: <code>{user_id}</code>")
    elif text.startswith("/users"):
        require_admin_or_reply(tg_config, chat_id, user_id, lambda: send_users(tg_config, chat_id))
    elif text.startswith("/adduser"):
        require_admin_or_reply(tg_config, chat_id, user_id, lambda: add_user_command(tg_config, chat_id, text))
    elif text.startswith("/deluser") or text.startswith("/removeuser"):
        require_admin_or_reply(tg_config, chat_id, user_id, lambda: remove_user_command(tg_config, chat_id, text))
    elif text.startswith("/backup"):
        require_admin_or_reply(tg_config, chat_id, user_id, lambda: send_backup(tg_config, chat_id))
    elif text.startswith("/status"):
        parts = text.split(maxsplit=1)
        serial_or_name = parts[1].strip() if len(parts) > 1 else None
        send_status(wb_config, tg_config, chat_id, serial_or_name)
    elif text.startswith("/start") or text.startswith("/help"):
        send_message(tg_config, chat_id, help_text(), main_menu_keyboard())
    else:
        send_message(tg_config, chat_id, help_text(), main_menu_keyboard())


def handle_callback(wb_config: Config, tg_config: TelegramConfig, callback: dict[str, Any]) -> None:
    from_user = callback.get("from", {})
    user_id = from_user.get("id")
    callback_id = callback.get("id")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    data = str(callback.get("data") or "")

    if isinstance(callback_id, str):
        telegram_request(tg_config, "answerCallbackQuery", {"callback_query_id": callback_id})

    if not isinstance(user_id, int) or not is_allowed(tg_config, user_id):
        if isinstance(callback_id, str):
            telegram_request(
                tg_config,
                "answerCallbackQuery",
                {"callback_query_id": callback_id, "text": "Access denied", "show_alert": True},
            )
        return

    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return

    if data == "status:all":
        edit_status(wb_config, tg_config, chat_id, message_id, only_offline=False)
    elif data == "status:offline":
        edit_status(wb_config, tg_config, chat_id, message_id, only_offline=True)
    elif data == "admin:users" and is_admin(tg_config, user_id):
        edit_message(tg_config, chat_id, message_id, format_users(tg_config), admin_keyboard())
    elif data == "admin:backup" and is_admin(tg_config, user_id):
        send_backup(tg_config, chat_id)
    elif data.startswith("controller:"):
        serial_number = data.split(":", 1)[1]
        edit_controller(wb_config, tg_config, chat_id, message_id, serial_number)


def send_status(
    wb_config: Config,
    tg_config: TelegramConfig,
    chat_id: int,
    serial_or_name: str | None = None,
) -> None:
    try:
        controllers = fetch_controllers(wb_config)
    except Exception as exc:
        send_message(tg_config, chat_id, f"Could not fetch controller status:\n<code>{html.escape(str(exc))}</code>")
        return

    if serial_or_name:
        controller = find_controller(controllers, serial_or_name)
        if controller is None:
            send_message(tg_config, chat_id, f"Controller not found: <code>{html.escape(serial_or_name)}</code>")
            return
        send_message(tg_config, chat_id, format_controller_detail(controller), controller_keyboard(controller))
        return

    send_message(tg_config, chat_id, format_controller_list(controllers), controllers_keyboard(controllers))


def edit_status(
    wb_config: Config,
    tg_config: TelegramConfig,
    chat_id: int,
    message_id: int,
    *,
    only_offline: bool,
) -> None:
    try:
        controllers = fetch_controllers(wb_config)
    except Exception as exc:
        edit_message(tg_config, chat_id, message_id, f"Could not fetch controller status:\n<code>{html.escape(str(exc))}</code>")
        return

    visible = [controller for controller in controllers if not controller.online] if only_offline else controllers
    edit_message(tg_config, chat_id, message_id, format_controller_list(visible), controllers_keyboard(visible))


def edit_controller(
    wb_config: Config,
    tg_config: TelegramConfig,
    chat_id: int,
    message_id: int,
    serial_number: str,
) -> None:
    try:
        controllers = fetch_controllers(wb_config)
    except Exception as exc:
        edit_message(tg_config, chat_id, message_id, f"Could not fetch controller status:\n<code>{html.escape(str(exc))}</code>")
        return

    controller = find_controller(controllers, serial_number)
    if controller is None:
        edit_message(tg_config, chat_id, message_id, f"Controller not found: <code>{html.escape(serial_number)}</code>")
        return
    edit_message(tg_config, chat_id, message_id, format_controller_detail(controller), controller_keyboard(controller))


def format_controller_list(controllers: list[Controller]) -> str:
    total = len(controllers)
    down = len([controller for controller in controllers if not controller.online])
    up = total - down
    lines = [
        "<b>Wirenboard.cloud status</b>",
        f"Total: <b>{total}</b>",
        f"Online: <b>{up}</b>",
        f"Offline: <b>{down}</b>",
        "",
    ]

    if not controllers:
        lines.append("No controllers to show.")
        return "\n".join(lines)

    for controller in sorted(controllers, key=lambda item: (item.online, item.name.lower())):
        status = "ONLINE" if controller.online else "OFFLINE"
        lines.append(f"<b>{status}</b> {html.escape(controller.name)}")
        lines.append(f"<code>{html.escape(controller.controller_id)}</code>")
        if controller.last_seen:
            lines.append(f"Last ping: {html.escape(controller.last_seen)}")
        lines.append("")

    return "\n".join(lines).strip()


def format_controller_detail(controller: Controller) -> str:
    status = "ONLINE" if controller.online else "OFFLINE"
    lines = [
        f"<b>{html.escape(controller.name)}</b>",
        f"Status: <b>{status}</b>",
        f"Serial: <code>{html.escape(controller.controller_id)}</code>",
    ]
    if controller.last_seen:
        lines.append(f"Last ping: {html.escape(controller.last_seen)}")
    lines.append(f"Remote access: {html.escape(remote_access_url(controller.controller_id))}")
    return "\n".join(lines)


def find_controller(controllers: list[Controller], serial_or_name: str) -> Controller | None:
    needle = serial_or_name.casefold()
    for controller in controllers:
        if controller.controller_id.casefold() == needle:
            return controller
    for controller in controllers:
        if needle in controller.name.casefold():
            return controller
    return None


def help_text() -> str:
    return "\n".join(
        [
            "<b>Wirenboard Cloud Watcher</b>",
            "",
            "/status - all controllers",
            "/status SERIAL - one controller",
            "/id - show your Telegram user id",
            "/users - approved users (admin)",
            "/adduser ID - approve user (admin)",
            "/deluser ID - remove user (admin)",
            "/backup - download backup archive (admin)",
        ]
    )


def main_menu_keyboard() -> dict[str, Any]:
    return inline_keyboard(
        [
            [callback_button("All controllers", "status:all")],
            [callback_button("Offline only", "status:offline")],
            [callback_button("Users", "admin:users"), callback_button("Backup", "admin:backup")],
        ]
    )


def controllers_keyboard(controllers: list[Controller]) -> dict[str, Any]:
    rows = [
        [callback_button("Refresh all", "status:all"), callback_button("Offline only", "status:offline")],
    ]
    for controller in sorted(controllers, key=lambda item: (item.online, item.name.lower()))[:50]:
        label = f"{'UP' if controller.online else 'DOWN'} {controller.name}"
        rows.append([callback_button(label[:60], f"controller:{controller.controller_id}")])
    return inline_keyboard(rows)


def controller_keyboard(controller: Controller) -> dict[str, Any]:
    return inline_keyboard(
        [
            [url_button("Open remote access", remote_access_url(controller.controller_id))],
            [callback_button("Refresh", f"controller:{controller.controller_id}")],
            [callback_button("All controllers", "status:all")],
        ]
    )


def inline_keyboard(rows: list[list[dict[str, str]]]) -> dict[str, Any]:
    return {"inline_keyboard": rows}


def callback_button(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


def url_button(text: str, url: str) -> dict[str, str]:
    return {"text": text, "url": url}


def html_pre(value: str) -> str:
    return f"<pre>{html.escape(value)}</pre>"


def is_allowed(tg_config: TelegramConfig, user_id: int) -> bool:
    return user_id in get_allowed_user_ids(tg_config) or user_id in get_admin_user_ids(tg_config)


def is_admin(tg_config: TelegramConfig, user_id: int) -> bool:
    return user_id in get_admin_user_ids(tg_config)


def require_admin_or_reply(tg_config: TelegramConfig, chat_id: int, user_id: int, action) -> None:
    if not is_admin(tg_config, user_id):
        send_message(tg_config, chat_id, "Admin access required.")
        return
    action()


def load_user_access(tg_config: TelegramConfig) -> dict[str, set[int]]:
    if not tg_config.users_file.exists():
        access = {
            "allowed": set(tg_config.allowed_user_ids) | set(tg_config.admin_user_ids),
            "admins": set(tg_config.admin_user_ids),
        }
        save_user_access(tg_config, access)
        return access

    data = json.loads(tg_config.users_file.read_text(encoding="utf-8"))
    allowed = set(int(value) for value in data.get("allowed", []))
    admins = set(int(value) for value in data.get("admins", [])) | set(tg_config.admin_user_ids)
    return {"allowed": allowed | admins, "admins": admins}


def save_user_access(tg_config: TelegramConfig, access: dict[str, set[int]]) -> None:
    tg_config.users_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "allowed": sorted(access.get("allowed", set()) | access.get("admins", set())),
        "admins": sorted(access.get("admins", set())),
    }
    tg_config.users_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        tg_config.users_file.chmod(0o600)
    except OSError:
        pass


def get_allowed_user_ids(tg_config: TelegramConfig) -> set[int]:
    return load_user_access(tg_config)["allowed"]


def get_admin_user_ids(tg_config: TelegramConfig) -> set[int]:
    return load_user_access(tg_config)["admins"]


def send_users(tg_config: TelegramConfig, chat_id: int) -> None:
    send_message(tg_config, chat_id, format_users(tg_config), admin_keyboard())


def format_users(tg_config: TelegramConfig) -> str:
    access = load_user_access(tg_config)
    lines = ["<b>Approved users</b>", ""]
    for user_id in sorted(access["allowed"]):
        role = "admin" if user_id in access["admins"] else "user"
        lines.append(f"<code>{user_id}</code> - {role}")
    return "\n".join(lines)


def add_user_command(tg_config: TelegramConfig, chat_id: int, text: str) -> None:
    user_id = parse_user_id_argument(text)
    if user_id is None:
        send_message(tg_config, chat_id, "Usage: <code>/adduser 123456789</code>")
        return

    access = load_user_access(tg_config)
    access["allowed"].add(user_id)
    save_user_access(tg_config, access)
    send_message(tg_config, chat_id, f"User approved: <code>{user_id}</code>", admin_keyboard())


def remove_user_command(tg_config: TelegramConfig, chat_id: int, text: str) -> None:
    user_id = parse_user_id_argument(text)
    if user_id is None:
        send_message(tg_config, chat_id, "Usage: <code>/deluser 123456789</code>")
        return

    access = load_user_access(tg_config)
    if user_id in access["admins"]:
        send_message(tg_config, chat_id, "Admin users cannot be removed through /deluser. Edit .env to change admins.")
        return
    access["allowed"].discard(user_id)
    save_user_access(tg_config, access)
    send_message(tg_config, chat_id, f"User removed: <code>{user_id}</code>", admin_keyboard())


def parse_user_id_argument(text: str) -> int | None:
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        return int(parts[1].strip())
    except ValueError:
        return None


def admin_keyboard() -> dict[str, Any]:
    return inline_keyboard(
        [
            [callback_button("Users", "admin:users")],
            [callback_button("Download backup", "admin:backup")],
            [callback_button("All controllers", "status:all")],
        ]
    )


def send_backup(tg_config: TelegramConfig, chat_id: int) -> None:
    try:
        backup_path = create_bot_backup(Path.cwd(), tg_config.backup_dir)
        send_document(tg_config, chat_id, backup_path, "Backup archive. Keep it private: it contains .env tokens.")
    except Exception as exc:
        send_message(tg_config, chat_id, f"Could not create backup:\n<code>{html.escape(str(exc))}</code>")


def create_bot_backup(project_dir: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"wb-cloud-watcher-backup-{timestamp}.tar.gz"
    with tarfile.open(backup_path, "w:gz") as archive:
        add_backup_tree(archive, project_dir, "wb-cloud-watcher")
    try:
        backup_path.chmod(0o600)
    except OSError:
        pass
    return backup_path


def add_backup_tree(archive: tarfile.TarFile, root: Path, arcname: str) -> None:
    excluded_names = {".git", "__pycache__", ".pytest_cache", ".venv"}
    excluded_dirs = {".backups"}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in excluded_names for part in relative.parts):
            continue
        if any(part in excluded_dirs for part in relative.parts):
            continue
        archive.add(path, arcname=str(Path(arcname) / relative))


def send_message(
    tg_config: TelegramConfig,
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    telegram_request(tg_config, "sendMessage", payload)


def send_document(tg_config: TelegramConfig, chat_id: int, path: Path, caption: str = "") -> None:
    fields = {"chat_id": str(chat_id), "caption": caption}
    files = {"document": path}
    telegram_multipart_request(tg_config, "sendDocument", fields, files)


def edit_message(
    tg_config: TelegramConfig,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        telegram_request(tg_config, "editMessageText", payload)
    except RuntimeError as exc:
        if "message is not modified" not in str(exc):
            raise


def telegram_request(tg_config: TelegramConfig, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{quote(tg_config.bot_token, safe=':')}/{method}"
    clean_payload = {key: value for key, value in (payload or {}).items() if value is not None}
    body = json.dumps(clean_payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "wb-cloud-watcher/0.1"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=tg_config.timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API returned HTTP {exc.code}: {details}") from exc
    except URLError as exc:
        raise RuntimeError(f"Telegram API request failed: {exc.reason}") from exc

    data = json.loads(response_body)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API returned error: {data}")
    return data


def telegram_multipart_request(
    tg_config: TelegramConfig,
    method: str,
    fields: dict[str, str],
    files: dict[str, Path],
) -> dict[str, Any]:
    boundary = f"----wb-cloud-watcher-{int(time.time() * 1000)}"
    body = build_multipart_body(boundary, fields, files)
    url = f"https://api.telegram.org/bot{quote(tg_config.bot_token, safe=':')}/{method}"
    request = Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "wb-cloud-watcher/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=tg_config.timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram API returned HTTP {exc.code}: {details}") from exc
    except URLError as exc:
        raise RuntimeError(f"Telegram API request failed: {exc.reason}") from exc

    data = json.loads(response_body)
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API returned error: {data}")
    return data


def build_multipart_body(boundary: str, fields: dict[str, str], files: dict[str, Path]) -> bytes:
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    for name, path in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{path.name}"\r\n'
                ).encode("utf-8"),
                b"Content-Type: application/gzip\r\n\r\n",
                path.read_bytes(),
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks)
