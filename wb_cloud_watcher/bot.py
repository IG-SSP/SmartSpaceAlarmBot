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
    fetch_controller_payloads,
    load_state,
    local_access_url,
    patch_json_authenticated,
    preferred_access_url,
    remote_access_url,
    save_state,
)

CONTROLLERS_PAGE_SIZE = 12
DEFAULT_THEME = "dark"
DEFAULT_OFFLINE_DELAY_SECONDS = 0
THEMES = {"light", "dark", "matrix"}
START_ANIMATION_PATH = Path("assets/start.gif")


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    allowed_user_ids: set[int]
    admin_user_ids: set[int]
    users_file: Path
    backup_dir: Path
    timeout_seconds: int
    webapp_public_url: str = ""


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
        webapp_public_url=env("WEBAPP_PUBLIC_URL").rstrip("/"),
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
    from .webapp import read_webapp_config, start_webapp_server

    webapp_config = read_webapp_config()
    start_webapp_server(
        wb_config,
        tg_config,
        webapp_config,
        lambda user_id: is_allowed(tg_config, user_id),
        lambda user_id: get_user_preferences(tg_config, user_id),
        lambda user_id, values: update_user_preferences(tg_config, user_id, sanitize_preferences(values, is_admin(tg_config, user_id))),
        lambda: load_history(tg_config),
        lambda user_id: is_admin(tg_config, user_id),
        lambda: list_user_profiles(tg_config),
        lambda value: add_user_by_identifier(tg_config, value),
        lambda value: remove_user_by_identifier(tg_config, value),
        lambda user: request_access_from_admins(tg_config, user),
    )
    configure_webapp_menu(tg_config)
    try:
        result = ensure_ip_notes(wb_config)
        if result["created"]:
            print(f"created IP notes for {result['created']} controller(s)")
    except Exception as exc:
        print(f"IP note bootstrap failed: {exc}", file=sys.stderr)

    offset: int | None = None
    next_check_at = 0.0
    startup_check_done = False
    print("telegram bot started")

    while True:
        now = time.time()
        if now >= next_check_at:
            check_and_notify(wb_config, tg_config, include_current_offline=not startup_check_done)
            startup_check_done = True
            next_check_at = now + wb_config.poll_interval_seconds

        try:
            updates = telegram_request(tg_config, "getUpdates", {"timeout": 10, "offset": offset})
        except Exception as exc:
            print(f"telegram getUpdates failed: {exc}", file=sys.stderr)
            time.sleep(5)
            continue
        for update in updates.get("result", []):
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
            try:
                handle_update(wb_config, tg_config, update)
            except Exception as exc:
                print(f"update handling failed: {exc}", file=sys.stderr)


def check_and_notify(wb_config: Config, tg_config: TelegramConfig, *, include_current_offline: bool = False) -> None:
    try:
        controllers = fetch_controllers(wb_config)
        previous = load_state(wb_config.state_file)
        first_run = not wb_config.state_file.exists()
        current = build_current_state(controllers, previous)
        save_state(wb_config.state_file, current)
        append_history_events(tg_config, controllers, previous, first_run=first_run)
    except Exception as exc:
        print(f"background check failed: {exc}", file=sys.stderr)
        return

    notification_state = load_notification_state(tg_config)
    now = int(time.time())
    for user_id in get_allowed_user_ids(tg_config):
        preferences = get_user_preferences(tg_config, user_id)
        user_state = notification_state.setdefault(str(user_id), {})
        for controller in controllers:
            old = previous.get(controller.controller_id)
            old_online = bool(old.get("online")) if old is not None else None
            offline_since = current[controller.controller_id].get("offline_since")
            notified = bool(user_state.get(controller.controller_id, {}).get("offline_notified"))

            should_notify_first_run = first_run and include_current_offline and wb_config.notify_on_first_run
            if not controller.online:
                if offline_since is None:
                    continue
                offline_seconds = max(0, now - int(offline_since))
                if not notified and (old is not None or should_notify_first_run) and offline_seconds >= preferences["offline_delay_seconds"]:
                    send_controller_notification(tg_config, user_id, controller, old_online, preferences, offline_seconds)
                    user_state[controller.controller_id] = {"offline_notified": True, "offline_since": offline_since}
                continue

            if notified and old_online is False:
                offline_seconds = 0
                old_offline_since = user_state.get(controller.controller_id, {}).get("offline_since")
                if isinstance(old_offline_since, int):
                    offline_seconds = max(0, now - old_offline_since)
                send_controller_notification(tg_config, user_id, controller, old_online, preferences, offline_seconds)
                user_state.pop(controller.controller_id, None)

        active_ids = {controller.controller_id for controller in controllers if not controller.online}
        for controller_id in list(user_state):
            if controller_id not in active_ids:
                user_state.pop(controller_id, None)

    save_notification_state(tg_config, notification_state)


def build_current_state(controllers: list[Controller], previous: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    now = int(time.time())
    current: dict[str, dict[str, Any]] = {}
    for controller in controllers:
        state = controller_to_state(controller)
        old = previous.get(controller.controller_id, {})
        if controller.online:
            state["offline_since"] = None
        elif old.get("online") is False and isinstance(old.get("offline_since"), int):
            state["offline_since"] = old["offline_since"]
        else:
            state["offline_since"] = now
        current[controller.controller_id] = state
    return current


def append_history_events(
    tg_config: TelegramConfig,
    controllers: list[Controller],
    previous: dict[str, dict[str, Any]],
    *,
    first_run: bool,
) -> None:
    events = load_history(tg_config)
    now = int(time.time())
    changed = False
    for controller in controllers:
        old = previous.get(controller.controller_id)
        if old is None:
            if first_run and not controller.online:
                events.append(history_event(controller, "offline", now))
                changed = True
            continue
        old_online = bool(old.get("online"))
        if old_online == controller.online:
            continue
        events.append(history_event(controller, "online" if controller.online else "offline", now))
        changed = True
    if changed:
        save_history(tg_config, events[-500:])


def history_event(controller: Controller, event_type: str, timestamp: int) -> dict[str, Any]:
    return {
        "timestamp": timestamp,
        "type": event_type,
        "id": controller.controller_id,
        "name": controller.name,
        "online": controller.online,
        "last_seen": controller.last_seen,
        "local_ip": controller.local_ip,
        "organization_id": controller.organization_id,
    }


def ensure_ip_notes(wb_config: Config) -> dict[str, int]:
    raw_controllers = fetch_controller_payloads(wb_config)
    result = {"checked": 0, "already_exists": 0, "created": 0}
    for raw in raw_controllers:
        if not isinstance(raw, dict):
            continue
        serial_number = str(raw.get("serialNumber") or "")
        if not serial_number:
            continue
        result["checked"] += 1
        notes = raw.get("userDefinedData")
        if not isinstance(notes, list):
            notes = []
        if has_ip_note(notes):
            result["already_exists"] += 1
            continue
        updated_notes = [note for note in notes if isinstance(note, dict)]
        updated_notes.append({"label": "IP", "value": "CHANGE_ME"})
        url = f"https://wirenboard.cloud/api/v1/controllers/{quote(serial_number, safe='')}/"
        patch_json_authenticated(wb_config, url, {"userDefinedData": updated_notes})
        result["created"] += 1
    return result


def has_ip_note(notes: list[Any]) -> bool:
    for note in notes:
        if not isinstance(note, dict):
            continue
        if str(note.get("label") or "").strip().casefold() == "ip":
            return True
    return False


def send_ip_note_report(wb_config: Config, tg_config: TelegramConfig, chat_id: int) -> None:
    try:
        result = ensure_ip_notes(wb_config)
    except Exception as exc:
        send_message(tg_config, chat_id, f"Не удалось проверить заметки IP:\n<code>{html.escape(str(exc))}</code>", admin_keyboard())
        return
    text = "\n".join(
        [
            "<b>Заметки IP</b>",
            "",
            f"Проверено объектов: <b>{result['checked']}</b>",
            f"Уже были заметки: <b>{result['already_exists']}</b>",
            f"Создано заметок: <b>{result['created']}</b>",
            "",
            "Если создана заметка <code>IP=CHANGE_ME</code>, откройте объект в WB Cloud и замените значение на локальный адрес, например <code>192.168.1.1/16</code>.",
        ]
    )
    send_message(tg_config, chat_id, text, admin_keyboard())


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
    remember_user_profile(tg_config, from_user)

    if not is_allowed(tg_config, user_id):
        send_message(tg_config, chat_id, f"Доступ не разрешен.\nВаш Telegram ID: <code>{user_id}</code>")
        return

    text = str(message.get("text") or "").strip()
    if text.startswith("/id"):
        send_message(tg_config, chat_id, f"Ваш Telegram ID: <code>{user_id}</code>")
    elif text.startswith("/users"):
        require_admin_or_reply(tg_config, chat_id, user_id, lambda: send_users(tg_config, chat_id))
    elif text.startswith("/adduser"):
        require_admin_or_reply(tg_config, chat_id, user_id, lambda: add_user_command(tg_config, chat_id, text))
    elif text.startswith("/deluser") or text.startswith("/removeuser"):
        require_admin_or_reply(tg_config, chat_id, user_id, lambda: remove_user_command(tg_config, chat_id, text))
    elif text.startswith("/backup"):
        require_admin_or_reply(tg_config, chat_id, user_id, lambda: send_backup(tg_config, chat_id))
    elif text.startswith("/settings") or text.startswith("/prefs"):
        send_settings(tg_config, chat_id, user_id)
    elif text.startswith("/status"):
        parts = text.split(maxsplit=1)
        serial_or_name = parts[1].strip() if len(parts) > 1 else None
        send_status(wb_config, tg_config, chat_id, serial_or_name)
    elif text.startswith("/start"):
        send_start(tg_config, chat_id)
    elif text.startswith("/help"):
        send_message(tg_config, chat_id, help_text(), main_menu_keyboard(tg_config.webapp_public_url))
    else:
        send_message(tg_config, chat_id, help_text(), main_menu_keyboard(tg_config.webapp_public_url))


def handle_callback(wb_config: Config, tg_config: TelegramConfig, callback: dict[str, Any]) -> None:
    from_user = callback.get("from", {})
    user_id = from_user.get("id")
    callback_id = callback.get("id")
    message = callback.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    message_id = message.get("message_id")
    data = str(callback.get("data") or "")
    if isinstance(user_id, int):
        remember_user_profile(tg_config, from_user)

    if isinstance(callback_id, str):
        telegram_request(tg_config, "answerCallbackQuery", {"callback_query_id": callback_id})

    if not isinstance(user_id, int) or not is_allowed(tg_config, user_id):
        if isinstance(callback_id, str):
            telegram_request(
                tg_config,
                "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": "Доступ не разрешен", "show_alert": True},
            )
        return

    if not isinstance(chat_id, int) or not isinstance(message_id, int):
        return

    if data.startswith("status:all"):
        edit_status(wb_config, tg_config, chat_id, message_id, only_offline=False, page=parse_page(data))
    elif data.startswith("status:offline"):
        edit_status(wb_config, tg_config, chat_id, message_id, only_offline=True, page=parse_page(data))
    elif data == "admin:users" and is_admin(tg_config, user_id):
        edit_message(tg_config, chat_id, message_id, format_users(tg_config), admin_keyboard())
    elif data == "admin:backup" and is_admin(tg_config, user_id):
        send_backup(tg_config, chat_id)
    elif data == "admin:ipnotes" and is_admin(tg_config, user_id):
        send_ip_note_report(wb_config, tg_config, chat_id)
    elif data == "settings":
        edit_message(tg_config, chat_id, message_id, format_settings(tg_config, user_id), settings_keyboard(tg_config, user_id))
    elif data.startswith("theme:"):
        set_user_theme(tg_config, user_id, data.split(":", 1)[1])
        edit_message(tg_config, chat_id, message_id, format_settings(tg_config, user_id), settings_keyboard(tg_config, user_id))
    elif data.startswith("delay:"):
        set_user_offline_delay(tg_config, user_id, parse_delay_value(data))
        edit_message(tg_config, chat_id, message_id, format_settings(tg_config, user_id), settings_keyboard(tg_config, user_id))
    elif data.startswith("access:add:") and is_admin(tg_config, user_id):
        requested_user_id = parse_callback_user_id(data)
        if requested_user_id is None:
            edit_message(tg_config, chat_id, message_id, "Не удалось разобрать ID пользователя.", admin_keyboard())
        else:
            result = add_user_by_identifier(tg_config, str(requested_user_id))
            text = f"Пользователь разрешен: <code>{requested_user_id}</code>" if result.get("ok") else html.escape(str(result.get("message") or result.get("error")))
            edit_message(tg_config, chat_id, message_id, text, admin_keyboard())
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
        send_message(tg_config, chat_id, f"Не удалось получить статус контроллеров:\n<code>{html.escape(str(exc))}</code>")
        return

    if serial_or_name:
        controller = find_controller(controllers, serial_or_name)
        if controller is None:
            send_message(tg_config, chat_id, f"Объект не найден: <code>{html.escape(serial_or_name)}</code>")
            return
        send_message(tg_config, chat_id, format_controller_detail(controller), controller_keyboard(controller))
        return

    send_message(tg_config, chat_id, format_controller_list(controllers, page=0), controllers_keyboard(controllers, page=0, mode="all"))


def edit_status(
    wb_config: Config,
    tg_config: TelegramConfig,
    chat_id: int,
    message_id: int,
    *,
    only_offline: bool,
    page: int = 0,
) -> None:
    try:
        controllers = fetch_controllers(wb_config)
    except Exception as exc:
        edit_message(tg_config, chat_id, message_id, f"Не удалось получить статус контроллеров:\n<code>{html.escape(str(exc))}</code>")
        return

    visible = [controller for controller in controllers if not controller.online] if only_offline else controllers
    mode = "offline" if only_offline else "all"
    edit_message(
        tg_config,
        chat_id,
        message_id,
        format_controller_list(visible, page=page),
        controllers_keyboard(visible, page=page, mode=mode),
    )


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
        edit_message(tg_config, chat_id, message_id, f"Не удалось получить статус контроллеров:\n<code>{html.escape(str(exc))}</code>")
        return

    controller = find_controller(controllers, serial_number)
    if controller is None:
        edit_message(tg_config, chat_id, message_id, f"Объект не найден: <code>{html.escape(serial_number)}</code>")
        return
    edit_message(tg_config, chat_id, message_id, format_controller_detail(controller), controller_keyboard(controller))


def format_controller_list(controllers: list[Controller], *, page: int = 0) -> str:
    total = len(controllers)
    down = len([controller for controller in controllers if not controller.online])
    up = total - down
    page_count = max(1, (total + CONTROLLERS_PAGE_SIZE - 1) // CONTROLLERS_PAGE_SIZE)
    page = clamp_page(page, page_count)
    lines = [
        "<b>Статус объектов Wirenboard.cloud</b>",
        f"Всего: <b>{total}</b>",
        f"В сети: <b>{up}</b>",
        f"Не в сети: <b>{down}</b>",
        f"Страница: <b>{page + 1}/{page_count}</b>",
        "",
    ]

    if not controllers:
        lines.append("Нет объектов для отображения.")
        return "\n".join(lines)

    sorted_controllers = sorted(controllers, key=lambda item: (item.online, item.name.lower()))
    start = page * CONTROLLERS_PAGE_SIZE
    for controller in sorted_controllers[start : start + CONTROLLERS_PAGE_SIZE]:
        status = "В СЕТИ" if controller.online else "НЕ В СЕТИ"
        lines.append(f"<b>{status}</b> {html.escape(controller.name)}")
        lines.append(f"<code>{html.escape(controller.controller_id)}</code>")
        if controller.last_seen:
            lines.append(f"Последний пинг: {html.escape(controller.last_seen)}")
        lines.append("")

    return "\n".join(lines).strip()


def format_controller_detail(controller: Controller) -> str:
    status = "В сети" if controller.online else "Не в сети"
    lines = [
        f"<b>{html.escape(controller.name)}</b>",
        f"Статус: <b>{status}</b>",
        f"Серийный номер: <code>{html.escape(controller.controller_id)}</code>",
    ]
    if controller.last_seen:
        lines.append(f"Последний пинг: {html.escape(controller.last_seen)}")
    local_url = local_access_url(controller)
    if not controller.online and local_url:
        lines.append("Локальный доступ: перед открытием включите VPN до объекта.")
        lines.append(f"Локальный веб-интерфейс: {html.escape(local_url)}")
    lines.append(f"Удаленный доступ: {html.escape(remote_access_url(controller.controller_id))}")
    return "\n".join(lines)


def format_telegram_notification_body(controller: Controller, old_online: bool | None) -> str:
    status = "В СЕТИ" if controller.online else "НЕ В СЕТИ"
    previous_text = "неизвестно" if old_online is None else ("в сети" if old_online else "не в сети")
    current_text = "в сети" if controller.online else "не в сети"
    lines = [
        controller.name,
        f"Статус: {status}",
        f"Изменение: {previous_text} -> {current_text}",
        f"Серийный номер: {controller.controller_id}",
    ]
    if controller.last_seen:
        lines.append(f"Последний пинг: {controller.last_seen}")
    local_url = local_access_url(controller)
    if not controller.online and local_url:
        lines.append("Перед локальным переходом включите VPN до объекта.")
        lines.append(f"Локальный доступ: {local_url}")
    lines.append(f"Удаленный доступ: {remote_access_url(controller.controller_id)}")
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
            "<b>SmartSpaceAlarmBot</b>",
            "",
            "Основное управление теперь внутри Mini App.",
            "",
            "/status - все объекты",
            "/status SERIAL - один объект",
            "/settings - ваши уведомления",
            "/id - показать ваш Telegram ID",
            "/users - пользователи (админ)",
            "/adduser ID - разрешить пользователя (админ)",
            "/deluser ID - удалить пользователя (админ)",
            "/backup - скачать резервную копию (админ)",
        ]
    )


def start_text() -> str:
    return "\n".join(
        [
            "<b>SmartSpaceAlarmBot</b>",
            "",
            "Все управление, статусы, поиск, темы и быстрый доступ к объектам находятся внутри приложения.",
            "",
            "Этот чат остается для важных уведомлений: когда контроллер пропал, вернулся в сеть или требует внимания.",
        ]
    )


def send_start(tg_config: TelegramConfig, chat_id: int) -> None:
    keyboard = start_keyboard(tg_config.webapp_public_url)
    if START_ANIMATION_PATH.exists():
        try:
            send_animation(tg_config, chat_id, START_ANIMATION_PATH, start_text(), keyboard)
            return
        except Exception as exc:
            print(f"sendAnimation failed: {exc}", file=sys.stderr)
    send_message(tg_config, chat_id, start_text(), keyboard)


def start_keyboard(webapp_public_url: str = "") -> dict[str, Any]:
    rows = []
    if webapp_public_url:
        rows.append([web_app_button("Открыть приложение", webapp_public_url)])
    rows.append([callback_button("Настройки уведомлений", "settings")])
    return inline_keyboard(rows)


def main_menu_keyboard(webapp_public_url: str = "") -> dict[str, Any]:
    rows = []
    if webapp_public_url:
        rows.append([web_app_button("Открыть приложение", webapp_public_url)])
    rows.extend(
        [
            [callback_button("Все объекты", "status:all:0")],
            [callback_button("Только упавшие", "status:offline:0")],
            [callback_button("Настройки", "settings")],
            [callback_button("Пользователи", "admin:users"), callback_button("Резервная копия", "admin:backup")],
        ]
    )
    return inline_keyboard(rows)


def controllers_keyboard(controllers: list[Controller], *, page: int = 0, mode: str = "all") -> dict[str, Any]:
    page_count = max(1, (len(controllers) + CONTROLLERS_PAGE_SIZE - 1) // CONTROLLERS_PAGE_SIZE)
    page = clamp_page(page, page_count)
    rows = [
        [callback_button("Обновить", f"status:{mode}:{page}")],
        [callback_button("Все", "status:all:0"), callback_button("Упавшие", "status:offline:0")],
    ]
    if page_count > 1:
        prev_page = max(0, page - 1)
        next_page = min(page_count - 1, page + 1)
        rows.append(
            [
                callback_button("Назад", f"status:{mode}:{prev_page}"),
                callback_button(f"{page + 1}/{page_count}", f"status:{mode}:{page}"),
                callback_button("Вперед", f"status:{mode}:{next_page}"),
            ]
        )

    sorted_controllers = sorted(controllers, key=lambda item: (item.online, item.name.lower()))
    start = page * CONTROLLERS_PAGE_SIZE
    for controller in sorted_controllers[start : start + CONTROLLERS_PAGE_SIZE]:
        label = f"{'OK' if controller.online else 'ALARM'} {controller.name}"
        rows.append([callback_button(label[:60], f"controller:{controller.controller_id}")])
    return inline_keyboard(rows)


def controller_keyboard(controller: Controller) -> dict[str, Any]:
    rows = []
    local_url = local_access_url(controller)
    if not controller.online and local_url:
        rows.append([url_button("Локальный веб-интерфейс", local_url), url_button("Пинг локального", local_url)])
        rows.append([url_button("Удаленный доступ", remote_access_url(controller.controller_id))])
    else:
        rows.append([url_button("Открыть веб-интерфейс", remote_access_url(controller.controller_id))])
    rows.extend(
        [
            [callback_button("Обновить", f"controller:{controller.controller_id}")],
            [callback_button("Все объекты", "status:all:0")],
        ]
    )
    return inline_keyboard(rows)


def parse_page(callback_data: str) -> int:
    parts = callback_data.split(":")
    if len(parts) < 3:
        return 0
    try:
        return int(parts[2])
    except ValueError:
        return 0


def clamp_page(page: int, page_count: int) -> int:
    return max(0, min(page, page_count - 1))


def inline_keyboard(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    return {"inline_keyboard": rows}


def callback_button(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


def url_button(text: str, url: str) -> dict[str, str]:
    return {"text": text, "url": url}


def web_app_button(text: str, url: str) -> dict[str, Any]:
    return {"text": text, "web_app": {"url": url}}


def configure_webapp_menu(tg_config: TelegramConfig) -> None:
    if not tg_config.webapp_public_url:
        return
    try:
        telegram_request(
            tg_config,
            "setChatMenuButton",
            {
                "menu_button": {
                    "type": "web_app",
                    "text": "Статус объектов",
                    "web_app": {"url": tg_config.webapp_public_url},
                }
            },
        )
    except Exception as exc:
        print(f"setChatMenuButton failed: {exc}", file=sys.stderr)


def html_pre(value: str) -> str:
    return f"<pre>{html.escape(value)}</pre>"


def send_controller_notification(
    tg_config: TelegramConfig,
    user_id: int,
    controller: Controller,
    old_online: bool | None,
    preferences: dict[str, Any],
    offline_seconds: int,
) -> None:
    theme = normalize_theme(str(preferences.get("theme") or DEFAULT_THEME))
    text = format_themed_notification(controller, old_online, offline_seconds, theme)
    keyboard = notification_keyboard(controller)
    safe_send_message(tg_config, user_id, text, keyboard)


def notification_keyboard(controller: Controller) -> dict[str, Any]:
    rows = []
    local_url = local_access_url(controller)
    if not controller.online and local_url:
        rows.append([url_button("Локальный веб-интерфейс", local_url)])
        rows.append([url_button("Удаленный доступ", remote_access_url(controller.controller_id))])
    else:
        rows.append([url_button("Открыть веб-интерфейс", preferred_access_url(controller))])
    return inline_keyboard(rows)


def format_themed_notification(controller: Controller, old_online: bool | None, offline_seconds: int, theme: str) -> str:
    if theme == "matrix":
        return html_pre(format_matrix_notification(controller, old_online, offline_seconds))
    if theme == "light":
        return format_light_notification(controller, old_online, offline_seconds)
    return format_dark_notification(controller, old_online, offline_seconds)


def format_light_notification(controller: Controller, old_online: bool | None, offline_seconds: int) -> str:
    icon = "✅" if controller.online else "⚠️"
    status = "в сети" if controller.online else "не в сети"
    lines = [
        f"{icon} <b>{html.escape(controller.name)}</b>",
        f"Статус: <b>{status}</b>",
    ]
    if controller.last_seen:
        lines.append(f"Последний пинг: <b>{html.escape(format_readable_datetime(controller.last_seen))}</b>")
    if offline_seconds and not controller.online:
        lines.append(f"Оффлайн: <b>{format_duration(offline_seconds)}</b>")
    if not controller.online and local_access_url(controller):
        lines.append("Перед локальным переходом включите VPN до объекта.")
    return "\n".join(lines)


def format_dark_notification(controller: Controller, old_online: bool | None, offline_seconds: int) -> str:
    status = "ONLINE" if controller.online else "OFFLINE"
    lines = [
        f"<b>{html.escape(controller.name)}</b>",
        f"Статус: <b>{status}</b>",
    ]
    if controller.last_seen:
        lines.append(f"Последний пинг: <code>{html.escape(format_readable_datetime(controller.last_seen))}</code>")
    if offline_seconds and not controller.online:
        lines.append(f"Оффлайн: <b>{format_duration(offline_seconds)}</b>")
    if not controller.online and local_access_url(controller):
        lines.append("Перед локальным переходом включите VPN до объекта.")
    return "\n".join(lines)


def format_matrix_notification(controller: Controller, old_online: bool | None, offline_seconds: int) -> str:
    status = "ONLINE" if controller.online else "OFFLINE"
    lines = [
        "SMARTSPACE::WATCHER",
        f"OBJECT={controller.name}",
        f"STATUS={status}",
    ]
    if controller.last_seen:
        lines.append(f"LAST_PING={format_readable_datetime(controller.last_seen)}")
    if offline_seconds and not controller.online:
        lines.append(f"OFFLINE_FOR={format_duration(offline_seconds)}")
    local_url = local_access_url(controller)
    if not controller.online and local_url:
        lines.append("VPN_REQUIRED=TRUE")
    return "\n".join(lines)


def append_access_lines(lines: list[str], controller: Controller) -> None:
    local_url = local_access_url(controller)
    if not controller.online and local_url:
        lines.append("Перед локальным переходом включите VPN до объекта.")
        lines.append(f"Локальный доступ: {html.escape(local_url)}")
    lines.append(f"Удаленный доступ: {html.escape(remote_access_url(controller.controller_id))}")


def format_readable_datetime(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.astimezone().strftime("%d.%m.%Y %H:%M")


def format_duration(seconds: int) -> str:
    minutes = seconds // 60
    if minutes < 1:
        return f"{seconds} сек."
    hours, rest_minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} ч {rest_minutes} мин"
    return f"{minutes} мин"


def is_allowed(tg_config: TelegramConfig, user_id: int) -> bool:
    return user_id in get_allowed_user_ids(tg_config) or user_id in get_admin_user_ids(tg_config)


def is_admin(tg_config: TelegramConfig, user_id: int) -> bool:
    return user_id in get_admin_user_ids(tg_config)


def require_admin_or_reply(tg_config: TelegramConfig, chat_id: int, user_id: int, action) -> None:
    if not is_admin(tg_config, user_id):
        send_message(tg_config, chat_id, "Нужны права администратора.")
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
    old_payload = {}
    if tg_config.users_file.exists():
        try:
            old_payload = json.loads(tg_config.users_file.read_text(encoding="utf-8"))
        except Exception:
            old_payload = {}
    payload = {
        "allowed": sorted(access.get("allowed", set()) | access.get("admins", set())),
        "admins": sorted(access.get("admins", set())),
        "preferences": old_payload.get("preferences", {}),
        "profiles": old_payload.get("profiles", {}),
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


def send_settings(tg_config: TelegramConfig, chat_id: int, user_id: int) -> None:
    send_message(tg_config, chat_id, format_settings(tg_config, user_id), settings_keyboard(tg_config, user_id))


def format_settings(tg_config: TelegramConfig, user_id: int) -> str:
    preferences = get_user_preferences(tg_config, user_id)
    theme_names = {"light": "светлая", "dark": "темная", "matrix": "матрица"}
    return "\n".join(
        [
            "<b>Ваши настройки</b>",
            "",
            f"Тема уведомлений: <b>{theme_names[preferences['theme']]}</b>",
            f"Задержка offline-уведомления: <b>{format_duration(preferences['offline_delay_seconds'])}</b>",
        ]
    )


def settings_keyboard(tg_config: TelegramConfig, user_id: int) -> dict[str, Any]:
    preferences = get_user_preferences(tg_config, user_id)
    theme = preferences["theme"]
    delay = preferences["offline_delay_seconds"]
    return inline_keyboard(
        [
            [
                callback_button(("✓ " if theme == "dark" else "") + "Темная", "theme:dark"),
                callback_button(("✓ " if theme == "light" else "") + "Светлая", "theme:light"),
                callback_button(("✓ " if theme == "matrix" else "") + "Матрица", "theme:matrix"),
            ],
            [
                callback_button(("✓ " if delay == 0 else "") + "Сразу", "delay:0"),
                callback_button(("✓ " if delay == 300 else "") + "5 мин", "delay:300"),
                callback_button(("✓ " if delay == 900 else "") + "15 мин", "delay:900"),
            ],
            [
                callback_button(("✓ " if delay == 1800 else "") + "30 мин", "delay:1800"),
                callback_button(("✓ " if delay == 3600 else "") + "1 час", "delay:3600"),
                callback_button("Все объекты", "status:all:0"),
            ],
        ]
    )


def parse_delay_value(data: str) -> int:
    try:
        return normalize_delay(data.split(":", 1)[1])
    except IndexError:
        return DEFAULT_OFFLINE_DELAY_SECONDS


def parse_callback_user_id(data: str) -> int | None:
    try:
        return int(data.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def get_user_preferences(tg_config: TelegramConfig, user_id: int) -> dict[str, Any]:
    data = load_users_payload(tg_config)
    raw = data.get("preferences", {}).get(str(user_id), {})
    return {
        "theme": normalize_theme(str(raw.get("theme") or DEFAULT_THEME), allow_matrix=is_admin(tg_config, user_id)),
        "offline_delay_seconds": normalize_delay(raw.get("offline_delay_seconds", DEFAULT_OFFLINE_DELAY_SECONDS)),
    }


def set_user_theme(tg_config: TelegramConfig, user_id: int, theme: str) -> None:
    update_user_preferences(tg_config, user_id, {"theme": normalize_theme(theme, allow_matrix=is_admin(tg_config, user_id))})


def set_user_offline_delay(tg_config: TelegramConfig, user_id: int, delay_seconds: int) -> None:
    update_user_preferences(tg_config, user_id, {"offline_delay_seconds": normalize_delay(delay_seconds)})


def update_user_preferences(tg_config: TelegramConfig, user_id: int, values: dict[str, Any]) -> None:
    data = load_users_payload(tg_config)
    preferences = data.setdefault("preferences", {})
    user_preferences = preferences.setdefault(str(user_id), {})
    user_preferences.update(values)
    save_users_payload(tg_config, data)


def sanitize_preferences(values: dict[str, Any], allow_matrix: bool = False) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    if "theme" in values:
        clean["theme"] = normalize_theme(str(values["theme"]), allow_matrix=allow_matrix)
    if "offline_delay_seconds" in values:
        clean["offline_delay_seconds"] = normalize_delay(values["offline_delay_seconds"])
    return clean


def load_users_payload(tg_config: TelegramConfig) -> dict[str, Any]:
    if not tg_config.users_file.exists():
        load_user_access(tg_config)
    try:
        data = json.loads(tg_config.users_file.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("allowed", sorted(set(tg_config.allowed_user_ids) | set(tg_config.admin_user_ids)))
    data.setdefault("admins", sorted(tg_config.admin_user_ids))
    data.setdefault("preferences", {})
    data.setdefault("profiles", {})
    return data


def save_users_payload(tg_config: TelegramConfig, data: dict[str, Any]) -> None:
    tg_config.users_file.parent.mkdir(parents=True, exist_ok=True)
    tg_config.users_file.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        tg_config.users_file.chmod(0o600)
    except OSError:
        pass


def normalize_theme(theme: str, *, allow_matrix: bool = True) -> str:
    if theme == "matrix" and not allow_matrix:
        return DEFAULT_THEME
    return theme if theme in THEMES else DEFAULT_THEME


def normalize_delay(value: Any) -> int:
    try:
        delay = int(value)
    except (TypeError, ValueError):
        return DEFAULT_OFFLINE_DELAY_SECONDS
    return max(0, min(delay, 86400))


def load_notification_state(tg_config: TelegramConfig) -> dict[str, Any]:
    path = notification_state_path(tg_config)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_notification_state(tg_config: TelegramConfig, state: dict[str, Any]) -> None:
    path = notification_state_path(tg_config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def notification_state_path(tg_config: TelegramConfig) -> Path:
    return tg_config.users_file.with_name("telegram_notifications.json")


def history_path(tg_config: TelegramConfig) -> Path:
    return tg_config.users_file.with_name("controller_history.json")


def load_history(tg_config: TelegramConfig) -> list[dict[str, Any]]:
    path = history_path(tg_config)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def save_history(tg_config: TelegramConfig, events: list[dict[str, Any]]) -> None:
    path = history_path(tg_config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(events, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def remember_user_profile(tg_config: TelegramConfig, user: dict[str, Any]) -> None:
    user_id = user.get("id")
    if not isinstance(user_id, int):
        return
    data = load_users_payload(tg_config)
    profiles = data.setdefault("profiles", {})
    profile = profiles.setdefault(str(user_id), {})
    for key in ("username", "first_name", "last_name"):
        value = user.get(key)
        if value:
            profile[key] = str(value)
    save_users_payload(tg_config, data)


def list_user_profiles(tg_config: TelegramConfig) -> dict[str, Any]:
    data = load_users_payload(tg_config)
    allowed = set(int(value) for value in data.get("allowed", []))
    admins = set(int(value) for value in data.get("admins", [])) | set(tg_config.admin_user_ids)
    profiles = data.get("profiles", {})
    users = []
    for user_id in sorted(allowed | admins):
        profile = profiles.get(str(user_id), {})
        users.append(
            {
                "id": user_id,
                "username": profile.get("username"),
                "first_name": profile.get("first_name"),
                "last_name": profile.get("last_name"),
                "admin": user_id in admins,
                "allowed": user_id in allowed or user_id in admins,
            }
        )
    return {"users": users}


def add_user_by_identifier(tg_config: TelegramConfig, value: str) -> dict[str, Any]:
    user_id = resolve_user_identifier(tg_config, value)
    if user_id is None:
        return {"ok": False, "error": "unknown_user", "message": "Пользователь с таким username еще не писал боту. Используйте Telegram ID или попросите его открыть /start."}
    access = load_user_access(tg_config)
    access["allowed"].add(user_id)
    save_user_access(tg_config, access)
    return {"ok": True, "user_id": user_id}


def remove_user_by_identifier(tg_config: TelegramConfig, value: str) -> dict[str, Any]:
    user_id = resolve_user_identifier(tg_config, value)
    if user_id is None:
        return {"ok": False, "error": "unknown_user", "message": "Пользователь не найден среди известных username/ID."}
    access = load_user_access(tg_config)
    if user_id in access["admins"]:
        return {"ok": False, "error": "admin_user", "message": "Админов нельзя удалить из Mini App. Измените TELEGRAM_ADMIN_USER_IDS в .env."}
    access["allowed"].discard(user_id)
    save_user_access(tg_config, access)
    return {"ok": True, "user_id": user_id}


def resolve_user_identifier(tg_config: TelegramConfig, value: str) -> int | None:
    item = value.strip()
    if not item:
        return None
    if item.isdigit():
        return int(item)
    username = item.lstrip("@").casefold()
    data = load_users_payload(tg_config)
    for user_id, profile in data.get("profiles", {}).items():
        if str(profile.get("username") or "").casefold() == username:
            return int(user_id)
    return None


def request_access_from_admins(tg_config: TelegramConfig, user: dict[str, Any]) -> dict[str, Any]:
    user_id = user.get("id")
    if not isinstance(user_id, int):
        return {"ok": False, "error": "bad_user"}
    remember_user_profile(tg_config, user)
    username = str(user.get("username") or "")
    name = " ".join(str(user.get(key) or "").strip() for key in ("first_name", "last_name")).strip()
    lines = [
        "<b>Запрос доступа к Mini App</b>",
        "",
        f"ID: <code>{user_id}</code>",
    ]
    if username:
        lines.append(f"Username: @{html.escape(username)}")
    if name:
        lines.append(f"Имя: {html.escape(name)}")
    keyboard = inline_keyboard([[callback_button("Разрешить пользователя", f"access:add:{user_id}")]])
    delivered = 0
    for admin_id in get_admin_user_ids(tg_config):
        if safe_send_message(tg_config, admin_id, "\n".join(lines), keyboard):
            delivered += 1
    return {"ok": True, "delivered": delivered}


def send_users(tg_config: TelegramConfig, chat_id: int) -> None:
    send_message(tg_config, chat_id, format_users(tg_config), admin_keyboard())


def format_users(tg_config: TelegramConfig) -> str:
    access = load_user_access(tg_config)
    lines = ["<b>Разрешенные пользователи</b>", ""]
    for user_id in sorted(access["allowed"]):
        role = "админ" if user_id in access["admins"] else "пользователь"
        lines.append(f"<code>{user_id}</code> - {role}")
    return "\n".join(lines)


def add_user_command(tg_config: TelegramConfig, chat_id: int, text: str) -> None:
    user_id = parse_user_id_argument(text)
    if user_id is None:
        send_message(tg_config, chat_id, "Формат: <code>/adduser 123456789</code>")
        return

    access = load_user_access(tg_config)
    access["allowed"].add(user_id)
    save_user_access(tg_config, access)
    send_message(tg_config, chat_id, f"Пользователь разрешен: <code>{user_id}</code>", admin_keyboard())


def remove_user_command(tg_config: TelegramConfig, chat_id: int, text: str) -> None:
    user_id = parse_user_id_argument(text)
    if user_id is None:
        send_message(tg_config, chat_id, "Формат: <code>/deluser 123456789</code>")
        return

    access = load_user_access(tg_config)
    if user_id in access["admins"]:
        send_message(tg_config, chat_id, "Админов нельзя удалить через /deluser. Измените TELEGRAM_ADMIN_USER_IDS в .env.")
        return
    access["allowed"].discard(user_id)
    save_user_access(tg_config, access)
    send_message(tg_config, chat_id, f"Пользователь удален: <code>{user_id}</code>", admin_keyboard())


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
            [callback_button("Пользователи", "admin:users")],
            [callback_button("Проверить IP-заметки", "admin:ipnotes")],
            [callback_button("Скачать backup", "admin:backup")],
            [callback_button("Все объекты", "status:all:0")],
        ]
    )


def send_backup(tg_config: TelegramConfig, chat_id: int) -> None:
    try:
        backup_path = create_bot_backup(Path.cwd(), tg_config.backup_dir)
        send_document(tg_config, chat_id, backup_path, "Резервная копия. Храните осторожно: внутри .env с токенами.")
    except Exception as exc:
        send_message(tg_config, chat_id, f"Не удалось создать backup:\n<code>{html.escape(str(exc))}</code>")


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


def safe_send_message(
    tg_config: TelegramConfig,
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    try:
        send_message(tg_config, chat_id, text, reply_markup)
        return True
    except Exception as exc:
        print(f"sendMessage to {chat_id} failed: {exc}", file=sys.stderr)
        return False


def send_document(tg_config: TelegramConfig, chat_id: int, path: Path, caption: str = "") -> None:
    fields = {"chat_id": str(chat_id), "caption": caption}
    files = {"document": path}
    telegram_multipart_request(tg_config, "sendDocument", fields, files)


def send_animation(
    tg_config: TelegramConfig,
    chat_id: int,
    path: Path,
    caption: str = "",
    reply_markup: dict[str, Any] | None = None,
) -> None:
    fields = {
        "chat_id": str(chat_id),
        "caption": caption,
        "parse_mode": "HTML",
    }
    if reply_markup:
        fields["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    files = {"animation": path}
    telegram_multipart_request(tg_config, "sendAnimation", fields, files)


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
