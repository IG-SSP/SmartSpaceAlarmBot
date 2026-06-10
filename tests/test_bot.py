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
    get_allowed_user_ids,
    get_admin_user_ids,
    remove_user_command,
    parse_allowed_user_ids,
)
from wb_cloud_watcher.cli import Controller


class BotTests(unittest.TestCase):
    def test_parse_allowed_user_ids_supports_commas_and_semicolons(self):
        self.assertEqual(parse_allowed_user_ids("1, 2;3"), {1, 2, 3})

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

        self.assertIn("Status: <b>OFFLINE</b>", format_controller_detail(controller))
        self.assertIn("https://wirenboard.cloud/connect/http/ABC123/", format_controller_detail(controller))

    def test_format_controller_list_shows_summary(self):
        controllers = [
            Controller(controller_id="ABC123", name="Boiler room", online=True),
            Controller(controller_id="DEF456", name="Kitchen", online=False),
        ]

        text = format_controller_list(controllers)

        self.assertIn("Total: <b>2</b>", text)
        self.assertIn("Online: <b>1</b>", text)
        self.assertIn("Offline: <b>1</b>", text)

    def test_controllers_keyboard_has_controller_buttons(self):
        controllers = [Controller(controller_id="ABC123", name="Boiler room", online=True)]

        keyboard = controllers_keyboard(controllers)

        self.assertEqual(keyboard["inline_keyboard"][1][0]["callback_data"], "controller:ABC123")

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
            self.assertIn("Admin users cannot be removed", messages[1][2])

    def test_build_multipart_body_contains_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backup.tar.gz"
            path.write_bytes(b"backup")

            body = build_multipart_body("boundary", {"chat_id": "1"}, {"document": path})

        self.assertIn(b'name="chat_id"', body)
        self.assertIn(b'filename="backup.tar.gz"', body)
        self.assertIn(b"backup", body)


if __name__ == "__main__":
    unittest.main()
