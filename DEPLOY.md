# Deployment Guide

This guide assumes a clean Ubuntu/Debian VPS.

For an interactive setup that asks for all required values, writes `.env`, installs systemd and emails a backup archive, run:

```bash
sudo python3 install.py
```

Manual steps are below.

## 1. Install Runtime

```bash
sudo apt update
sudo apt install -y python3 git
```

## 2. Create Service User

```bash
sudo useradd --system --home /opt/wb-cloud-watcher --shell /usr/sbin/nologin wbwatcher
```

## 3. Put Project On The Server

Use any method you prefer: `git clone`, `scp`, or an archive.

Example with git:

```bash
sudo git clone YOUR_REPO_URL /opt/wb-cloud-watcher
sudo chown -R wbwatcher:wbwatcher /opt/wb-cloud-watcher
```

If you copy files manually, the final structure should look like:

```text
/opt/wb-cloud-watcher/
  wb_cloud_watcher/
  deploy/
  README.md
  DEPLOY.md
  .env
```

## 4. Create `.env`

Create the config file:

```bash
cd /opt/wb-cloud-watcher
sudo cp .env.example .env
sudo nano .env
```

Minimum useful config:

```dotenv
WB_API_URL=https://wirenboard.cloud/api/v1/controllers/?page_size=100
WB_TOKEN=REPLACE_ME

CONTROLLERS_PATH=results
ID_FIELD=serialNumber
NAME_FIELD=description
ONLINE_FIELD=isAgentOk
LAST_SEEN_FIELD=lastAgentPingAt

STATE_FILE=.state/controllers.json
POLL_INTERVAL_SECONDS=60
NOTIFY_ON_FIRST_RUN=false
WB_TIMEOUT_SECONDS=15

TELEGRAM_BOT_TOKEN=REPLACE_ME
TELEGRAM_ALLOWED_USER_IDS=123456789,987654321
TELEGRAM_TIMEOUT_SECONDS=30
```

Secure it:

```bash
sudo chown wbwatcher:wbwatcher .env
sudo chmod 600 .env
```

## 5. Telegram Bot Token

Create a Telegram bot:

1. Open `@BotFather` in Telegram.
2. Send `/newbot`.
3. Choose name and username.
4. Copy the token.

Put it into `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=123456789:AA...
```

Do not commit this token.

## 6. Approved Telegram Users

Allowed users are configured by numeric Telegram user id:

```dotenv
TELEGRAM_ALLOWED_USER_IDS=111111111,222222222
```

To find your id before allowlisting yourself, start the bot temporarily with a permissive temporary id is not useful because the bot needs your real id. The bot intentionally replies to unauthorized `/id` requests with:

```text
Access denied.
Your Telegram user id: 111111111
```

So the practical flow is:

1. Put the bot token into `.env`.
2. Put a placeholder into `TELEGRAM_ALLOWED_USER_IDS`, for example `0`.
3. Start the bot.
4. Send `/id` to the bot from your Telegram account.
5. Copy the shown numeric id into `.env`.
6. Restart the bot.

Multiple users:

```dotenv
TELEGRAM_ALLOWED_USER_IDS=111111111,222222222,333333333
```

## 7. Wirenboard.cloud Token

Do not send the Wirenboard.cloud password in chat.

Run token login directly on the server over SSH:

```bash
cd /opt/wb-cloud-watcher
sudo -u wbwatcher python3 -m wb_cloud_watcher token --email ig@gilpert.ru
```

The password prompt is hidden. If TOTP is enabled:

```bash
sudo -u wbwatcher python3 -m wb_cloud_watcher token --email ig@gilpert.ru --totp-code 123456
```

The command prints:

```dotenv
WB_TOKEN=...
```

Put that value into `/opt/wb-cloud-watcher/.env`.

## 8. Test Manually

Check Wirenboard.cloud access:

```bash
cd /opt/wb-cloud-watcher
sudo -u wbwatcher python3 -m wb_cloud_watcher dump
```

Start bot manually:

```bash
sudo -u wbwatcher python3 -m wb_cloud_watcher bot
```

In Telegram, send:

```text
/start
/status
/status SERIAL
/id
```

Stop the manual process with `Ctrl+C`.

## 9. Install systemd Service

The service template is in:

```text
deploy/wb-cloud-watcher-bot.service
```

Install it:

```bash
cd /opt/wb-cloud-watcher
sudo cp deploy/wb-cloud-watcher-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wb-cloud-watcher-bot
```

Check status:

```bash
sudo systemctl status wb-cloud-watcher-bot
```

View logs:

```bash
sudo journalctl -u wb-cloud-watcher-bot -f
```

Restart after `.env` changes:

```bash
sudo systemctl restart wb-cloud-watcher-bot
```

## 10. Important Security Notes

- Store `WB_TOKEN` and `TELEGRAM_BOT_TOKEN` only in `.env`.
- Keep `.env` permissions at `600`.
- Do not put the Wirenboard.cloud password into `.env`.
- Do not expose this bot through a public web port. It uses Telegram long polling and does not need inbound firewall rules.
- The server only needs outbound HTTPS access to `wirenboard.cloud` and `api.telegram.org`.
