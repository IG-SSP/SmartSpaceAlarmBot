from __future__ import annotations

import hashlib
import hmac
import json
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlparse

from .cli import Config, controller_to_state, env, fetch_controllers, remote_access_url


STATIC_DIR = Path(__file__).resolve().parent.parent / "webapp_static"


@dataclass(frozen=True)
class WebAppConfig:
    host: str
    port: int
    public_url: str
    auth_max_age_seconds: int


def read_webapp_config() -> WebAppConfig:
    return WebAppConfig(
        host=env("WEBAPP_HOST", "127.0.0.1"),
        port=int(env("WEBAPP_PORT", "8088")),
        public_url=env("WEBAPP_PUBLIC_URL").rstrip("/"),
        auth_max_age_seconds=int(env("WEBAPP_AUTH_MAX_AGE_SECONDS", "86400")),
    )


def start_webapp_server(
    wb_config: Config,
    tg_config: Any,
    webapp_config: WebAppConfig,
    is_allowed_user: Callable[[int], bool],
    get_user_preferences: Callable[[int], dict[str, Any]] | None = None,
    set_user_preferences: Callable[[int, dict[str, Any]], None] | None = None,
    get_history: Callable[[], list[dict[str, Any]]] | None = None,
    is_admin_user: Callable[[int], bool] | None = None,
    list_users: Callable[[], dict[str, Any]] | None = None,
    add_user: Callable[[str], dict[str, Any]] | None = None,
    remove_user: Callable[[str], dict[str, Any]] | None = None,
    request_access: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> ThreadingHTTPServer | None:
    if not webapp_config.public_url:
        return None

    handler = build_handler(
        wb_config,
        tg_config,
        webapp_config,
        is_allowed_user,
        get_user_preferences,
        set_user_preferences,
        get_history,
        is_admin_user,
        list_users,
        add_user,
        remove_user,
        request_access,
    )
    server = ThreadingHTTPServer((webapp_config.host, webapp_config.port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"telegram web app server started on {webapp_config.host}:{webapp_config.port}")
    return server


def build_handler(
    wb_config: Config,
    tg_config: Any,
    webapp_config: WebAppConfig,
    is_allowed_user: Callable[[int], bool],
    get_user_preferences: Callable[[int], dict[str, Any]] | None = None,
    set_user_preferences: Callable[[int, dict[str, Any]], None] | None = None,
    get_history: Callable[[], list[dict[str, Any]]] | None = None,
    is_admin_user: Callable[[int], bool] | None = None,
    list_users: Callable[[], dict[str, Any]] | None = None,
    add_user: Callable[[str], dict[str, Any]] | None = None,
    remove_user: Callable[[str], dict[str, Any]] | None = None,
    request_access: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> type[BaseHTTPRequestHandler]:
    class WebAppHandler(BaseHTTPRequestHandler):
        server_version = "SmartSpaceAlarmBotWebApp/0.1"

        def do_HEAD(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"", "/"}:
                self.send_static_headers("index.html", "text/html; charset=utf-8")
                return
            self.send_response(404)
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path in {"", "/"}:
                self.send_static_file("index.html", "text/html; charset=utf-8")
                return
            if parsed.path == "/app.js":
                self.send_static_file("app.js", "application/javascript; charset=utf-8")
                return
            if parsed.path == "/style.css":
                self.send_static_file("style.css", "text/css; charset=utf-8")
                return
            if parsed.path == "/api/controllers":
                self.handle_controllers()
                return
            if parsed.path == "/api/me":
                self.handle_me()
                return
            if parsed.path == "/api/preferences":
                self.handle_preferences()
                return
            if parsed.path == "/api/history":
                self.handle_history()
                return
            if parsed.path == "/api/users":
                self.handle_users()
                return
            self.send_json(404, {"error": "not_found"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/preferences":
                self.handle_update_preferences()
                return
            if parsed.path == "/api/users":
                self.handle_add_user()
                return
            if parsed.path == "/api/access-request":
                self.handle_access_request()
                return
            self.send_json(404, {"error": "not_found"})

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/users":
                self.handle_remove_user()
                return
            self.send_json(404, {"error": "not_found"})

        def handle_me(self) -> None:
            user = self.authorize()
            if user is None:
                return
            preferences = get_user_preferences(user["id"]) if get_user_preferences else {}
            is_admin = is_admin_user(user["id"]) if is_admin_user else False
            self.send_json(200, {"user": user, "preferences": preferences, "is_admin": is_admin})

        def handle_preferences(self) -> None:
            user = self.authorize()
            if user is None:
                return
            preferences = get_user_preferences(user["id"]) if get_user_preferences else {}
            self.send_json(200, {"preferences": preferences})

        def handle_history(self) -> None:
            user = self.authorize()
            if user is None:
                return
            self.send_json(200, {"events": list(reversed((get_history or (lambda: []))()))[:200]})

        def handle_users(self) -> None:
            user = self.authorize_admin()
            if user is None:
                return
            self.send_json(200, list_users() if list_users else {"users": []})

        def handle_add_user(self) -> None:
            user = self.authorize_admin()
            if user is None:
                return
            if add_user is None:
                self.send_json(503, {"error": "users_unavailable"})
                return
            payload = self.read_json_payload()
            if payload is None:
                return
            result = add_user(str(payload.get("user") or ""))
            self.send_json(200 if result.get("ok") else 400, result)

        def handle_remove_user(self) -> None:
            user = self.authorize_admin()
            if user is None:
                return
            if remove_user is None:
                self.send_json(503, {"error": "users_unavailable"})
                return
            payload = self.read_json_payload()
            if payload is None:
                return
            result = remove_user(str(payload.get("user") or ""))
            self.send_json(200 if result.get("ok") else 400, result)

        def handle_access_request(self) -> None:
            user = self.telegram_user()
            if user is None:
                return
            if is_allowed_user(int(user["id"])):
                self.send_json(200, {"ok": True, "already_allowed": True})
                return
            if request_access is None:
                self.send_json(503, {"ok": False, "error": "access_requests_unavailable"})
                return
            result = request_access(user)
            self.send_json(200 if result.get("ok") else 400, result)

        def handle_update_preferences(self) -> None:
            user = self.authorize()
            if user is None:
                return
            if set_user_preferences is None or get_user_preferences is None:
                self.send_json(503, {"error": "preferences_unavailable"})
                return
            payload = self.read_json_payload()
            if payload is None:
                return
            set_user_preferences(user["id"], payload)
            self.send_json(200, {"preferences": get_user_preferences(user["id"])})

        def read_json_payload(self) -> dict[str, Any] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception:
                self.send_json(400, {"error": "bad_json"})
                return None
            if not isinstance(payload, dict):
                self.send_json(400, {"error": "bad_json"})
                return None
            return payload

        def handle_controllers(self) -> None:
            user = self.authorize()
            if user is None:
                return
            try:
                controllers = fetch_controllers(wb_config)
            except Exception as exc:
                self.send_json(502, {"error": "wirenboard_unavailable", "message": str(exc)})
                return

            payload = []
            for controller in sorted(controllers, key=lambda item: (item.online, item.name.lower())):
                item = controller_to_state(controller)
                item["remote_url"] = remote_access_url(controller.controller_id)
                item["local_url"] = f"http://{controller.local_ip}/" if controller.local_ip else None
                item["access_url"] = item["local_url"] if not controller.online and item["local_url"] else item["remote_url"]
                payload.append(item)

            total = len(payload)
            offline = len([item for item in payload if not item["online"]])
            self.send_json(
                200,
                {
                    "summary": {
                        "total": total,
                        "online": total - offline,
                        "offline": offline,
                        "checked_at": int(time.time()),
                    },
                    "controllers": payload,
                },
            )

        def authorize(self) -> dict[str, Any] | None:
            user = self.telegram_user()
            if user is None:
                return None
            user_id = user.get("id")
            if not isinstance(user_id, int) or not is_allowed_user(user_id):
                self.send_json(403, {"error": "forbidden", "user_id": user_id})
                return None
            return user

        def telegram_user(self) -> dict[str, Any] | None:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("tma "):
                self.send_json(401, {"error": "missing_init_data"})
                return None
            try:
                user = validate_init_data(
                    auth[4:],
                    tg_config.bot_token,
                    webapp_config.auth_max_age_seconds,
                )
            except ValueError as exc:
                self.send_json(401, {"error": "bad_init_data", "message": str(exc)})
                return None

            user_id = user.get("id")
            if not isinstance(user_id, int):
                self.send_json(401, {"error": "bad_init_data", "message": "user id отсутствует"})
                return None
            return user

        def authorize_admin(self) -> dict[str, Any] | None:
            user = self.authorize()
            if user is None:
                return None
            if not is_admin_user or not is_admin_user(user["id"]):
                self.send_json(403, {"error": "admin_required"})
                return None
            return user

        def send_static_file(self, filename: str, content_type: str) -> None:
            path = self.send_static_headers(filename, content_type)
            if path is None:
                return
            self.wfile.write(path.read_bytes())

        def send_static_headers(self, filename: str, content_type: str) -> Path | None:
            path = STATIC_DIR / filename
            if not path.exists():
                self.send_json(404, {"error": "asset_not_found"})
                return None
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return path

        def send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"webapp: {self.address_string()} {format % args}", file=sys.stderr)

    return WebAppHandler


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int) -> dict[str, Any]:
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = fields.pop("hash", "")
    if not received_hash:
        raise ValueError("hash отсутствует")

    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("подпись не совпала")

    auth_date_raw = fields.get("auth_date", "0")
    try:
        auth_date = int(auth_date_raw)
    except ValueError as exc:
        raise ValueError("некорректный auth_date") from exc
    if max_age_seconds > 0 and time.time() - auth_date > max_age_seconds:
        raise ValueError("initData устарел")

    user_raw = fields.get("user")
    if not user_raw:
        raise ValueError("user отсутствует")
    user = json.loads(user_raw)
    if not isinstance(user, dict):
        raise ValueError("user должен быть объектом")
    return user
