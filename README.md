# Wirenboard Cloud Watcher

Small monitor for Wirenboard.cloud controllers. It polls `GET /api/v1/controllers/`, detects `isAgentOk` state changes, and sends push notifications when a controller goes down or comes back.

The defaults follow the public Wirenboard.cloud OpenAPI schema at `https://wirenboard.cloud/api/v1/docs/swagger/`. The field mapping is still configurable in case the API changes.

## Features

- Polls Wirenboard.cloud API by HTTP.
- Follows paginated `next` links from `/api/v1/controllers/`.
- Detects `online -> offline` and `offline -> online` transitions.
- Sends push notifications through [ntfy](https://ntfy.sh/) with a clickable remote-access link.
- Runs a Telegram bot with approved users, status commands and inline buttons.
- Stores previous state in a local JSON file.
- Can run once from cron/systemd timer or continuously as a loop.
- Uses Python standard library only.

## Quick Start

1. Copy config:

```powershell
Copy-Item .env.example .env
```

2. Edit `.env`:

```dotenv
WB_API_URL=https://wirenboard.cloud/api/v1/controllers/?page_size=100
WB_TOKEN=YOUR_TOKEN
CONTROLLERS_PATH=results
ID_FIELD=serialNumber
NAME_FIELD=description
ONLINE_FIELD=isAgentOk
NTFY_TOPIC=your-private-topic
```

3. Run one check:

```powershell
python -m wb_cloud_watcher once
```

4. Run continuously:

```powershell
python -m wb_cloud_watcher loop
```

5. Or run the Telegram bot:

```powershell
python -m wb_cloud_watcher bot
```

## Configuration

## Getting A Wirenboard.cloud Token

The API login endpoint is `POST /api/v1/auth/token/`. It requires:

- `email`
- `password`
- `totpCode`, if TOTP is enabled
- or `recoveryCode`, if TOTP is unavailable

Use the local helper so the password is entered with hidden input:

```powershell
python -m wb_cloud_watcher token --email you@example.com
```

If TOTP is enabled:

```powershell
python -m wb_cloud_watcher token --email you@example.com --totp-code 123456
```

Then put the printed access token into `.env`:

```dotenv
WB_TOKEN=...
```

| Variable | Default | Description |
| --- | --- | --- |
| `WB_API_URL` | `https://wirenboard.cloud/api/v1/controllers/?page_size=100` | Wirenboard.cloud endpoint returning paginated controllers. |
| `WB_TOKEN` | empty | Wirenboard.cloud token. Sent as `Authorization: Token ...`. |
| `WB_AUTH_HEADER` | empty | Optional raw HTTP auth header. Overrides `WB_TOKEN` style when set. |
| `WB_TIMEOUT_SECONDS` | `15` | HTTP timeout. |
| `CONTROLLERS_PATH` | `results` | Dot path to controllers list in the paginated JSON. |
| `ID_FIELD` | `serialNumber` | Controller unique id field. |
| `NAME_FIELD` | `description` | Human-readable controller name field. Falls back to serial number when empty. |
| `ONLINE_FIELD` | `isAgentOk` | Boolean controller agent health field. |
| `LAST_SEEN_FIELD` | `lastAgentPingAt` | Last successful agent ping timestamp. Used only in notification text. |
| `STATE_FILE` | `.state/controllers.json` | Local state file. |
| `POLL_INTERVAL_SECONDS` | `60` | Loop polling interval. |
| `NOTIFY_ON_FIRST_RUN` | `false` | Send notifications for already-offline controllers on first run. |
| `NTFY_SERVER` | `https://ntfy.sh` | ntfy server URL. |
| `NTFY_TOPIC` | required for notifications | ntfy topic name. |
| `NTFY_TOKEN` | empty | Optional ntfy bearer token. |
| `NTFY_PRIORITY` | `default` | ntfy priority, for example `high`. |
| `TELEGRAM_BOT_TOKEN` | empty | Telegram bot token from BotFather. |
| `TELEGRAM_ALLOWED_USER_IDS` | empty | Comma-separated approved Telegram user IDs. |
| `TELEGRAM_TIMEOUT_SECONDS` | `30` | Telegram HTTP timeout. |

## Telegram Bot

The bot supports multiple approved users. Access is checked by Telegram user id, not by username.

Commands:

```text
/start
/status
/status SERIAL
/id
```

The `/status` response includes a summary, controller statuses, serial numbers, last ping timestamps and inline buttons. Controller details include an `Open remote access` button for:

```text
https://wirenboard.cloud/connect/http/<serialNumber>/
```

Setup:

1. Create a bot through `@BotFather` and put the token into `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=123456:...
```

2. Start the bot temporarily:

```bash
python -m wb_cloud_watcher bot
```

3. Send `/id` to the bot. If you are not approved yet, it will still reply with your Telegram user id.

4. Add approved users to `.env`:

```dotenv
TELEGRAM_ALLOWED_USER_IDS=111111111,222222222
```

5. Restart the bot.

The bot also performs background checks using `POLL_INTERVAL_SECONDS` and sends state-change notifications to all approved private chats.

## JSON Path Example

The documented controller list response is paginated:

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "serialNumber": "XXXXXXXX",
      "description": "Boiler room",
      "isAgentOk": true,
      "lastAgentPingAt": "2026-06-10T12:00:00Z"
    }
  ]
}
```

The monitor treats `isAgentOk=false` as a fallen controller.

Notification body example:

```text
Boiler room
Status: OFFLINE
Changed: online -> offline
Serial: ABC123
Last ping: 2026-06-10T12:00:00Z
Remote access: https://wirenboard.cloud/connect/http/ABC123/
```

For ntfy, the same URL is also sent in the `Click` header, so tapping the push opens the controller web access page.

## Deployment Notes

For a pet project, the simplest reliable setup is systemd on the server.

Detailed deployment instructions are in `DEPLOY.md`.
For a guided server setup, run:

```bash
sudo python3 install.py
```

Do not send the Wirenboard.cloud password in chat. Log in on the server over SSH and run the token helper interactively:

```bash
python3 -m wb_cloud_watcher token --email ig@gilpert.ru
```

The password is entered through hidden terminal input. Put only the printed access token into `.env`.

Recommended `.env` permissions on the server:

```bash
chmod 600 .env
```

A systemd template is provided at `deploy/wb-cloud-watcher-bot.service`. Adjust `WorkingDirectory`, `EnvironmentFile`, `ExecStart`, `User` and `Group` for your server, then install it as:

```bash
sudo cp deploy/wb-cloud-watcher-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wb-cloud-watcher-bot
sudo systemctl status wb-cloud-watcher-bot
```

If this will monitor real equipment, keep the polling interval conservative. Avoid aggressive polling of cloud APIs unless their limits are known.
