import unittest
import tempfile
from pathlib import Path

from wb_cloud_watcher.bot import (
    TelegramConfig,
    add_user_command,
    build_multipart_body,
    controllers_keyboard,
    find_controller,
    format_controller_detail,
    format_controller_list,
    format_telegram_notification_body,
    get_user_preferences,
    has_ip_note,
    history_event,
    get_allowed_user_ids,
    get_admin_user_ids,
    parse_page,
    remove_user_command,
    parse_allowed_user_ids,
    remember_user_profile,
    resolve_user_identifier,
    save_history,
    load_history,
    set_user_theme,
    set_user_offline_delay,
)
from wb_cloud_watcher.cli import Controller


class BotTests(unittest.TestCase):
    def test_parse_allowed_user_ids_supports_commas_and_semicolons(self):
        self.assertEqual(parse_allowed_user_ids("1, 2;3"), {1, 2, 3})

    def test_has_ip_note_detects_existing_note(self):
        self.assertTrue(has_ip_note([{"label": "IP", "value": "192.168.1.1/16"}]))
        self.assertFalse(has_ip_note([{"label": "Location", "value": "Office"}]))

    def test_find_controller_matches_serial_and_name_part(self):
        controllers = [
            Controller(controller_id="ABC123", name="Boiler room", online=True),
            Controller(controller_id="DEF456", name="Kitchen", online=False),
        ]

        self.assertEqual(find_controller(controllers, "ABC123"), controllers[0])
        self.assertEqual(find_controller(controllers, "boiler"), controllers[0])

    def test_format_controller_detail_contains_remote_access_link(self):
        controller = Controller(
            controller_id="ABC123",
            name="Boiler room",
            online=False,
            last_seen="2026-06-10T12:00:00Z",
        )

        self.assertIn("Статус: <b>Не в сети</b>", format_controller_detail(controller))
        self.assertIn("https://wirenboard.cloud/connect/http/ABC123/", format_controller_detail(controller))

    def test_format_controller_list_shows_summary(self):
        controllers = [
            Controller(controller_id="ABC123", name="Boiler room", online=True),
            Controller(controller_id="DEF456", name="Kitchen", online=False),
        ]

        text = format_controller_list(controllers)

        self.assertIn("Всего: <b>2</b>", text)
        self.assertIn("В сети: <b>1</b>", text)
        self.assertIn("Не в сети: <b>1</b>", text)

    def test_controllers_keyboard_has_controller_buttons(self):
        controllers = [Controller(controller_id="ABC123", name="Boiler room", online=True)]

        keyboard = controllers_keyboard(controllers)

        self.assertEqual(keyboard["inline_keyboard"][2][0]["callback_data"], "controller:ABC123")

    def test_controllers_keyboard_paginates_controller_buttons(self):
        controllers = [Controller(controller_id=f"WB{i:03}", name=f"Controller {i:03}", online=True) for i in range(20)]

        keyboard = controllers_keyboard(controllers, page=1, mode="all")

        self.assertEqual(keyboard["inline_keyboard"][2][0]["callback_data"], "status:all:0")
        self.assertEqual(keyboard["inline_keyboard"][2][2]["callback_data"], "status:all:1")
        self.assertEqual(keyboard["inline_keyboard"][3][0]["callback_data"], "controller:WB012")

    def test_parse_page_defaults_to_zero(self):
        self.assertEqual(parse_page("status:all:3"), 3)
        self.assertEqual(parse_page("status:all"), 0)
        self.assertEqual(parse_page("status:all:nope"), 0)

    def test_user_access_initializes_from_env_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            tg_config = TelegramConfig(
                bot_token="token",
                allowed_user_ids={1},
                admin_user_ids={2},
                users_file=Path(tmp) / "users.json",
                backup_dir=Path(tmp) / "backups",
                timeout_seconds=30,
            )

            self.assertEqual(get_allowed_user_ids(tg_config), {1, 2})
            self.assertEqual(get_admin_user_ids(tg_config), {2})

    def test_add_and_remove_user_updates_store_but_keeps_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            tg_config = TelegramConfig(
                bot_token="token",
                allowed_user_ids={1},
                admin_user_ids={2},
                users_file=Path(tmp) / "users.json",
                backup_dir=Path(tmp) / "backups",
                timeout_seconds=30,
            )
            messages = []

            with unittest.mock.patch("wb_cloud_watcher.bot.send_message", side_effect=lambda *args: messages.append(args)):
                add_user_command(tg_config, 100, "/adduser 3")
                remove_user_command(tg_config, 100, "/deluser 2")
                remove_user_command(tg_config, 100, "/deluser 3")

            self.assertEqual(get_allowed_user_ids(tg_config), {1, 2})
            self.assertIn("Админов нельзя удалить", messages[1][2])

    def test_user_preferences_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tg_config = TelegramConfig(
                bot_token="token",
                allowed_user_ids={1},
                admin_user_ids={2},
                users_file=Path(tmp) / "users.json",
                backup_dir=Path(tmp) / "backups",
                timeout_seconds=30,
            )

            set_user_theme(tg_config, 1, "matrix")
            set_user_offline_delay(tg_config, 1, 900)

            self.assertEqual(get_user_preferences(tg_config, 1), {"theme": "dark", "offline_delay_seconds": 900})

    def test_matrix_theme_is_admin_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            tg_config = TelegramConfig(
                bot_token="token",
                allowed_user_ids={1},
                admin_user_ids={2},
                users_file=Path(tmp) / "users.json",
                backup_dir=Path(tmp) / "backups",
                timeout_seconds=30,
            )

            set_user_theme(tg_config, 1, "matrix")
            set_user_theme(tg_config, 2, "matrix")

            self.assertEqual(get_user_preferences(tg_config, 1)["theme"], "dark")
            self.assertEqual(get_user_preferences(tg_config, 2)["theme"], "matrix")

    def test_resolve_user_identifier_supports_known_username(self):
        with tempfile.TemporaryDirectory() as tmp:
            tg_config = TelegramConfig(
                bot_token="token",
                allowed_user_ids={1},
                admin_user_ids={2},
                users_file=Path(tmp) / "users.json",
                backup_dir=Path(tmp) / "backups",
                timeout_seconds=30,
            )
            remember_user_profile(tg_config, {"id": 3, "username": "demo"})

            self.assertEqual(resolve_user_identifier(tg_config, "@demo"), 3)
            self.assertEqual(resolve_user_identifier(tg_config, "3"), 3)

    def test_history_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            tg_config = TelegramConfig(
                bot_token="token",
                allowed_user_ids={1},
                admin_user_ids={2},
                users_file=Path(tmp) / "users.json",
                backup_dir=Path(tmp) / "backups",
                timeout_seconds=30,
            )
            event = history_event(Controller(controller_id="ABC123", name="Boiler", online=False), "offline", 123)

            save_history(tg_config, [event])

            self.assertEqual(load_history(tg_config), [event])

    def test_build_multipart_body_contains_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backup.tar.gz"
            path.write_bytes(b"backup")

            body = build_multipart_body("boundary", {"chat_id": "1"}, {"document": path})

        self.assertIn(b'name="chat_id"', body)
        self.assertIn(b'filename="backup.tar.gz"', body)
        self.assertIn(b"backup", body)

    def test_format_telegram_notification_body_is_russian(self):
        controller = Controller(
            controller_id="ABC123",
            name="Boiler room",
            online=False,
            last_seen="2026-06-10T12:00:00Z",
        )

        text = format_telegram_notification_body(controller, old_online=True)

        self.assertIn("Статус: НЕ В СЕТИ", text)
        self.assertIn("Изменение: в сети -> не в сети", text)


if __name__ == "__main__":
    unittest.main()
