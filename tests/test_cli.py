import unittest

from unittest.mock import patch

from wb_cloud_watcher.cli import (
    Controller,
    Config,
    fetch_controller_payloads,
    find_changes,
    format_notification_body,
    get_path,
    parse_online,
    request_token,
    remote_access_url,
)


class CliTests(unittest.TestCase):
    def test_parse_online_common_values(self):
        self.assertIs(parse_online(True), True)
        self.assertIs(parse_online("online"), True)
        self.assertIs(parse_online("connected"), True)
        self.assertIs(parse_online(False), False)
        self.assertIs(parse_online("offline"), False)
        self.assertIs(parse_online(0), False)

    def test_get_path_reads_nested_dicts_and_lists(self):
        payload = {"data": {"controllers": [{"id": "wb-1"}]}}

        self.assertEqual(get_path(payload, "data.controllers.0.id"), "wb-1")

    def test_find_changes_detects_transitions(self):
        previous = {"wb-1": {"online": True}}
        controllers = [Controller(controller_id="wb-1", name="WB 1", online=False)]

        self.assertEqual(
            find_changes(previous, controllers, first_run=False, notify_on_first_run=False),
            [(controllers[0], True)],
        )

    def test_find_changes_skips_new_controller_after_first_run(self):
        controllers = [Controller(controller_id="wb-1", name="WB 1", online=False)]

        self.assertEqual(find_changes({}, controllers, first_run=True, notify_on_first_run=False), [])

    def test_fetch_controller_payloads_follows_pagination(self):
        config = Config(
            api_url="https://wirenboard.cloud/api/v1/controllers/?page_size=1",
            auth_header="Authorization: Token test",
            controllers_path="results",
            id_field="serialNumber",
            name_field="description",
            online_field="isAgentOk",
            last_seen_field="lastAgentPingAt",
            state_file=None,
            poll_interval_seconds=60,
            notify_on_first_run=False,
            timeout_seconds=15,
            ntfy_server="https://ntfy.sh",
            ntfy_topic="",
            ntfy_token="",
            ntfy_priority="default",
        )
        pages = [
            {"results": [{"serialNumber": "wb-1"}], "next": "/api/v1/controllers/?page=2"},
            {"results": [{"serialNumber": "wb-2"}], "next": None},
        ]

        with patch("wb_cloud_watcher.cli.get_json", side_effect=pages) as get_json:
            self.assertEqual(fetch_controller_payloads(config), [{"serialNumber": "wb-1"}, {"serialNumber": "wb-2"}])

        self.assertEqual(get_json.call_count, 2)

    def test_format_notification_body_includes_friendly_name_and_remote_access_url(self):
        controller = Controller(
            controller_id="ABC123",
            name="Boiler room",
            online=False,
            last_seen="2026-06-10T12:00:00Z",
        )

        self.assertEqual(
            format_notification_body(controller, old_online=True),
            "\n".join(
                [
                    "Boiler room",
                    "Status: OFFLINE",
                    "Changed: online -> offline",
                    "Serial: ABC123",
                    "Last ping: 2026-06-10T12:00:00Z",
                    "Remote access: https://wirenboard.cloud/connect/http/ABC123/",
                ]
            ),
        )

    def test_remote_access_url_escapes_serial_number(self):
        self.assertEqual(remote_access_url("WB 1/2"), "https://wirenboard.cloud/connect/http/WB%201%2F2/")

    def test_request_token_posts_credentials_and_prints_env_value(self):
        with patch("wb_cloud_watcher.cli.getpass.getpass", return_value="secret"), patch(
            "wb_cloud_watcher.cli.post_json", return_value={"access": "access-token", "refresh": "refresh-token"}
        ) as post_json, patch("builtins.print") as print_:
            self.assertEqual(request_token("user@example.com", "123456", None), 0)

        post_json.assert_called_once_with(
            "https://wirenboard.cloud/api/v1/auth/token/",
            {"email": "user@example.com", "password": "secret", "totpCode": "123456"},
            timeout_seconds=30,
        )
        print_.assert_any_call("WB_TOKEN=access-token")


if __name__ == "__main__":
    unittest.main()
