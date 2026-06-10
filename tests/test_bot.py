import unittest

from wb_cloud_watcher.bot import (
    TelegramConfig,
    controllers_keyboard,
    find_controller,
    format_controller_detail,
    format_controller_list,
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


if __name__ == "__main__":
    unittest.main()
