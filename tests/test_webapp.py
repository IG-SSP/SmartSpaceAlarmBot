import hashlib
import hmac
import json
import time
import unittest
from urllib.parse import urlencode

from wb_cloud_watcher.webapp import validate_init_data


class WebAppTests(unittest.TestCase):
    def test_validate_init_data_accepts_signed_telegram_payload(self):
        bot_token = "123456:test-token"
        init_data = signed_init_data(bot_token, {"id": 42, "first_name": "Igor"})

        user = validate_init_data(init_data, bot_token, max_age_seconds=60)

        self.assertEqual(user["id"], 42)

    def test_validate_init_data_rejects_tampered_payload(self):
        bot_token = "123456:test-token"
        init_data = signed_init_data(bot_token, {"id": 42}).replace("42", "43")

        with self.assertRaises(ValueError):
            validate_init_data(init_data, bot_token, max_age_seconds=60)


def signed_init_data(bot_token: str, user: dict) -> str:
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "test-query",
        "user": json.dumps(user, separators=(",", ":")),
    }
    data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


if __name__ == "__main__":
    unittest.main()
