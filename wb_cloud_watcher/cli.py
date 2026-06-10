from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TRUE_VALUES = {"true", "1", "online", "up", "connected", "active", "ok"}
FALSE_VALUES = {"false", "0", "offline", "down", "disconnected", "inactive", "fail", "failed", "error"}


@dataclass(frozen=True)
class Config:
    api_url: str
    auth_header: str
    refresh_token: str
    auth_scheme: str
    env_path: Path
    controllers_path: str
    id_field: str
    name_field: str
    online_field: str
    last_seen_field: str
    state_file: Path
    poll_interval_seconds: int
    notify_on_first_run: bool
    timeout_seconds: int
    ntfy_server: str
    ntfy_topic: str
    ntfy_token: str
    ntfy_priority: str


@dataclass(frozen=True)
class Controller:
    controller_id: str
    name: str
    online: bool
    last_seen: str | None = None


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(".env"))
    parser = argparse.ArgumentParser(description="Monitor Wirenboard.cloud controllers and send push alerts.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("once", help="Run a single check.")
    subparsers.add_parser("loop", help="Run checks forever.")
    subparsers.add_parser("dump", help="Fetch and print normalized controller states without notifications.")
    subparsers.add_parser("bot", help="Run Telegram bot with approved users and status buttons.")
    token_parser = subparsers.add_parser("token", help="Request Wirenboard.cloud access and refresh tokens.")
    token_parser.add_argument("--email", help="Wirenboard.cloud account email.")
    token_parser.add_argument("--totp-code", help="Six-digit TOTP code, if enabled.")
    token_parser.add_argument("--recovery-code", help="Recovery code, if TOTP is unavailable.")

    args = parser.parse_args(argv)

    if args.command == "loop":
        config = read_config()
        return run_loop(config)
    if args.command == "dump":
        config = read_config()
        return dump(config)
    if args.command == "token":
        return request_token(args.email, args.totp_code, args.recovery_code)
    if args.command == "bot":
        from .bot import read_telegram_config, run_bot

        config = read_config()
        return run_bot(config, read_telegram_config())

    config = read_config()
    return run_once(config)


def run_loop(config: Config) -> int:
    while True:
        try:
            run_once(config)
        except Exception as exc:
            print(f"check failed: {exc}", file=sys.stderr)
        time.sleep(config.poll_interval_seconds)


def dump(config: Config) -> int:
    controllers = fetch_controllers(config)
    print(json.dumps([controller_to_state(c) for c in controllers], ensure_ascii=False, indent=2))
    return 0


def run_once(config: Config) -> int:
    controllers = fetch_controllers(config)
    previous = load_state(config.state_file)
    first_run = not config.state_file.exists()
    current = {controller.controller_id: controller_to_state(controller) for controller in controllers}

    changes = find_changes(previous, controllers, first_run=first_run, notify_on_first_run=config.notify_on_first_run)
    for controller, old_online in changes:
        send_notification(config, controller, old_online)

    save_state(config.state_file, current)
    print(f"checked {len(controllers)} controller(s), changes: {len(changes)}")
    return 0


def find_changes(
    previous: dict[str, dict[str, Any]],
    controllers: list[Controller],
    *,
    first_run: bool,
    notify_on_first_run: bool,
) -> list[tuple[Controller, bool | None]]:
    changes: list[tuple[Controller, bool | None]] = []
    for controller in controllers:
        old = previous.get(controller.controller_id)
        if old is None:
            if first_run and notify_on_first_run and not controller.online:
                changes.append((controller, None))
            continue

        old_online = bool(old.get("online"))
        if old_online != controller.online:
            changes.append((controller, old_online))
    return changes


def fetch_controllers(config: Config) -> list[Controller]:
    raw_controllers = fetch_controller_payloads(config)

    controllers: list[Controller] = []
    for index, raw in enumerate(raw_controllers):
        if not isinstance(raw, dict):
            raise ValueError(f"controller at index {index} must be an object")

        controller_id = str(get_path(raw, config.id_field))
        name_value = get_path(raw, config.name_field, default=controller_id)
        online_value = get_path(raw, config.online_field)
        last_seen_value = get_path(raw, config.last_seen_field, default=None) if config.last_seen_field else None

        controllers.append(
            Controller(
                controller_id=controller_id,
                name=str(name_value or controller_id),
                online=parse_online(online_value),
                last_seen=str(last_seen_value) if last_seen_value is not None else None,
            )
        )
    return controllers


def fetch_controller_payloads(config: Config) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    next_url: str | None = config.api_url
    auth_header = current_auth_header(config)
    refresh_token = env("WB_REFRESH_TOKEN", config.refresh_token)

    while next_url:
        payload, auth_header, refresh_token = get_json_authenticated(config, next_url, auth_header, refresh_token)
        raw_controllers = get_path(payload, config.controllers_path)
        if not isinstance(raw_controllers, list):
            raise ValueError(f"controllers path must point to a list, got {type(raw_controllers).__name__}")

        for raw in raw_controllers:
            if not isinstance(raw, dict):
                raise ValueError("controller item must be an object")
            payloads.append(raw)

        next_value = payload.get("next") if isinstance(payload, dict) else None
        next_url = urljoin(next_url, next_value) if isinstance(next_value, str) and next_value else None

    return payloads


def get_json_authenticated(
    config: Config,
    url: str,
    auth_header: str,
    refresh_token: str,
) -> tuple[Any, str, str]:
    try:
        return get_json(url, auth_header, config.timeout_seconds), auth_header, refresh_token
    except RuntimeError as exc:
        if "HTTP 401" not in str(exc) or not refresh_token:
            raise

    access, new_refresh = refresh_access_token(refresh_token, config.timeout_seconds)
    if new_refresh:
        refresh_token = new_refresh
    auth_scheme = env("WB_AUTH_SCHEME", config.auth_scheme)
    auth_header = f"Authorization: {auth_scheme} {access}"
    persist_tokens(config.env_path, access, refresh_token, auth_scheme)
    return get_json(url, auth_header, config.timeout_seconds), auth_header, refresh_token


def current_auth_header(config: Config) -> str:
    auth_header = env("WB_AUTH_HEADER")
    token = env("WB_TOKEN")
    auth_scheme = env("WB_AUTH_SCHEME", config.auth_scheme)
    if token and not auth_header:
        return f"Authorization: {auth_scheme} {token}"
    return auth_header or config.auth_header


def get_json(url: str, auth_header: str, timeout_seconds: int) -> Any:
    try:
        return get_json_with_header(url, auth_header, timeout_seconds)
    except HTTPError as exc:
        fallback_header = fallback_auth_header(auth_header)
        if exc.code != 401 or not fallback_header:
            raise format_http_error("API", exc) from exc
        try:
            return get_json_with_header(url, fallback_header, timeout_seconds)
        except HTTPError as fallback_exc:
            raise format_http_error("API", fallback_exc) from fallback_exc
    except URLError as exc:
        raise RuntimeError(f"API request failed: {exc.reason}") from exc


def get_json_with_header(url: str, auth_header: str, timeout_seconds: int) -> Any:
    headers = {"Accept": "application/json", "User-Agent": "wb-cloud-watcher/0.1"}
    if auth_header:
        name, value = parse_header(auth_header)
        headers[name] = value

    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("API returned invalid JSON") from exc


def post_json(url: str, payload: dict[str, Any], timeout_seconds: int) -> Any:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "wb-cloud-watcher/0.1",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        message = f"API returned HTTP {exc.code}"
        if details:
            message = f"{message}: {details}"
        raise RuntimeError(message) from exc
    except URLError as exc:
        raise RuntimeError(f"API request failed: {exc.reason}") from exc

    try:
        return json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise RuntimeError("API returned invalid JSON") from exc


def refresh_access_token(refresh_token: str, timeout_seconds: int) -> tuple[str, str]:
    response = post_json(
        "https://wirenboard.cloud/api/v1/auth/token/refresh/",
        {"refresh": refresh_token},
        timeout_seconds=timeout_seconds,
    )
    access = response.get("access") if isinstance(response, dict) else None
    new_refresh = response.get("refresh") if isinstance(response, dict) else None
    if not access:
        raise RuntimeError("refresh response does not contain 'access'")
    return str(access), str(new_refresh or refresh_token)


def request_token(email: str | None, totp_code: str | None, recovery_code: str | None) -> int:
    email = email or input("Wirenboard.cloud email: ").strip()
    password = getpass.getpass("Wirenboard.cloud password: ")
    payload: dict[str, Any] = {"email": email, "password": password}
    if totp_code:
        payload["totpCode"] = totp_code
    if recovery_code:
        payload["recoveryCode"] = recovery_code

    response = post_json("https://wirenboard.cloud/api/v1/auth/token/", payload, timeout_seconds=30)
    access = response.get("access") if isinstance(response, dict) else None
    refresh = response.get("refresh") if isinstance(response, dict) else None
    if not access:
        raise RuntimeError("token response does not contain 'access'")

    print("Access token:")
    print(access)
    if refresh:
        print("\nRefresh token:")
        print(refresh)
    print("\nPut this into .env:")
    print(f"WB_TOKEN={access}")
    if refresh:
        print(f"WB_REFRESH_TOKEN={refresh}")
    print("WB_AUTH_SCHEME=Bearer")
    return 0


def send_notification(config: Config, controller: Controller, old_online: bool | None) -> None:
    if not config.ntfy_topic:
        print(f"notification skipped for {controller.name}: NTFY_TOPIC is empty", file=sys.stderr)
        return

    is_up = controller.online
    title = "Wirenboard controller is up" if is_up else "Wirenboard controller is down"
    body = format_notification_body(controller, old_online)
    remote_url = remote_access_url(controller.controller_id)

    headers = {
        "Title": title,
        "Priority": config.ntfy_priority,
        "Tags": "white_check_mark" if is_up else "warning",
        "Click": remote_url,
    }
    if config.ntfy_token:
        headers["Authorization"] = f"Bearer {config.ntfy_token}"

    endpoint = f"{config.ntfy_server.rstrip('/')}/{config.ntfy_topic}"
    request = Request(endpoint, data=body.encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=config.timeout_seconds):
            pass
    except HTTPError as exc:
        raise RuntimeError(f"ntfy returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"ntfy request failed: {exc.reason}") from exc


def format_notification_body(controller: Controller, old_online: bool | None) -> str:
    status = "ONLINE" if controller.online else "OFFLINE"
    previous_text = "unknown" if old_online is None else ("online" if old_online else "offline")
    current_text = "online" if controller.online else "offline"
    label = controller.name if controller.name != controller.controller_id else controller.controller_id
    lines = [
        f"{label}",
        f"Status: {status}",
        f"Changed: {previous_text} -> {current_text}",
        f"Serial: {controller.controller_id}",
    ]
    if controller.last_seen:
        lines.append(f"Last ping: {controller.last_seen}")
    lines.append(f"Remote access: {remote_access_url(controller.controller_id)}")
    return "\n".join(lines)


def remote_access_url(serial_number: str) -> str:
    return f"https://wirenboard.cloud/connect/http/{quote(serial_number, safe='')}/"


def load_state(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"state file must contain an object: {path}")
    return data


def save_state(path: Path, state: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")


def controller_to_state(controller: Controller) -> dict[str, Any]:
    return {
        "id": controller.controller_id,
        "name": controller.name,
        "online": controller.online,
        "last_seen": controller.last_seen,
        "checked_at": int(time.time()),
    }


def read_config() -> Config:
    env_path = Path(".env")
    api_url = env("WB_API_URL", "https://wirenboard.cloud/api/v1/controllers/?page_size=100")
    if not api_url:
        raise SystemExit("WB_API_URL is required")
    auth_header = env("WB_AUTH_HEADER")
    token = env("WB_TOKEN")
    auth_scheme = env("WB_AUTH_SCHEME", "Bearer")
    if token and not auth_header:
        auth_header = f"Authorization: {auth_scheme} {token}"

    return Config(
        api_url=api_url,
        auth_header=auth_header,
        refresh_token=env("WB_REFRESH_TOKEN"),
        auth_scheme=auth_scheme,
        env_path=env_path,
        controllers_path=env("CONTROLLERS_PATH", "results"),
        id_field=env("ID_FIELD", "serialNumber"),
        name_field=env("NAME_FIELD", "description"),
        online_field=env("ONLINE_FIELD", "isAgentOk"),
        last_seen_field=env("LAST_SEEN_FIELD", "lastAgentPingAt"),
        state_file=Path(env("STATE_FILE", ".state/controllers.json")),
        poll_interval_seconds=env_int("POLL_INTERVAL_SECONDS", 60),
        notify_on_first_run=env_bool("NOTIFY_ON_FIRST_RUN", False),
        timeout_seconds=env_int("WB_TIMEOUT_SECONDS", 15),
        ntfy_server=env("NTFY_SERVER", "https://ntfy.sh"),
        ntfy_topic=env("NTFY_TOPIC"),
        ntfy_token=env("NTFY_TOKEN"),
        ntfy_priority=env("NTFY_PRIORITY", "default"),
    )


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def persist_tokens(env_path: Path, access_token: str, refresh_token: str, auth_scheme: str) -> None:
    values = {
        "WB_TOKEN": access_token,
        "WB_REFRESH_TOKEN": refresh_token,
        "WB_AUTH_SCHEME": auth_scheme,
    }
    update_env_file(env_path, values)
    os.environ.update(values)


def update_env_file(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            updated.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in values:
            updated.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            updated.append(line)
    for key, value in values.items():
        if key not in seen:
            updated.append(f"{key}={value}")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    raw = env(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer") from exc


def env_bool(name: str, default: bool) -> bool:
    raw = env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def parse_header(raw: str) -> tuple[str, str]:
    if ":" not in raw:
        raise ValueError("WB_AUTH_HEADER must look like 'Header-Name: value'")
    name, value = raw.split(":", 1)
    name = name.strip()
    value = value.strip()
    if not name or not value:
        raise ValueError("WB_AUTH_HEADER must include both header name and value")
    return name, value


def fallback_auth_header(raw: str) -> str:
    try:
        name, value = parse_header(raw)
    except ValueError:
        return ""
    if name.lower() != "authorization":
        return ""
    if value.startswith("Bearer "):
        return f"{name}: Token {value.removeprefix('Bearer ')}"
    if value.startswith("Token "):
        return f"{name}: Bearer {value.removeprefix('Token ')}"
    return ""


def format_http_error(prefix: str, exc: HTTPError) -> RuntimeError:
    details = exc.read().decode("utf-8", errors="replace")
    message = f"{prefix} returned HTTP {exc.code}"
    if details:
        message = f"{message}: {details}"
    return RuntimeError(message)


def get_path(value: Any, path: str, default: Any = ...):
    if not path:
        return value

    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        if isinstance(current, list) and part.isdigit():
            current = current[int(part)]
            continue
        if default is not ...:
            return default
        raise KeyError(f"path not found: {path}")
    return current


def parse_online(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0

    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    raise ValueError(f"cannot parse online status from value: {value!r}")
