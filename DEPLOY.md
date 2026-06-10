# Deployment Guide

This guide is for deploying SmartSpaceAlarmBot on a clean Ubuntu/Debian VPS.

Repository:

```text
https://github.com/IG-SSP/SmartSpaceAlarmBot
```

The bot uses Telegram long polling, so the server does not need any inbound public web port. It only needs outbound HTTPS access to:

- `wirenboard.cloud`
- `api.telegram.org`
- your SMTP server, if email backup is enabled

## Quick Install

Run on the server over SSH:

```bash
sudo apt update
sudo apt install -y python3 git
sudo git clone https://github.com/IG-SSP/SmartSpaceAlarmBot.git /opt/wb-cloud-watcher
cd /opt/wb-cloud-watcher
sudo python3 install.py
```

The installer will ask for:

- Wirenboard.cloud email, default: `ig@gilpert.ru`
- existing Wirenboard.cloud access token, or password for interactive token login
- TOTP code or recovery code, if enabled
- Telegram bot token from `@BotFather`
- allowed Telegram user IDs
- polling interval
- whether to send first-run offline alerts
- SMTP settings for backup email

The Wirenboard.cloud password is not written to `.env`. It is used once to request an access token.

## Where Config Lives

Runtime config is stored here:

```text
/opt/wb-cloud-watcher/.env
```

Important values:

```dotenv
WB_TOKEN=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_IDS=111111111,222222222
POLL_INTERVAL_SECONDS=60
```

The installer sets secure permissions:

```bash
sudo chmod 600 /opt/wb-cloud-watcher/.env
sudo chown wbwatcher:wbwatcher /opt/wb-cloud-watcher/.env
```

## Telegram Bot Token

Create a bot:

1. Open `@BotFather` in Telegram.
2. Send `/newbot`.
3. Choose name and username.
4. Copy the token.

Use that token when the installer asks:

```text
Telegram bot token from @BotFather:
```

Or edit manually:

```dotenv
TELEGRAM_BOT_TOKEN=123456789:AA...
```

Do not commit this token.

## Approved Telegram Users

Access is controlled by numeric Telegram user IDs:

```dotenv
TELEGRAM_ALLOWED_USER_IDS=111111111,222222222
```

To find your ID:

1. Set a temporary placeholder during install, for example `0`.
2. Start the service.
3. Send `/id` to the bot.
4. The bot replies even to unauthorized users:

```text
Access denied.
Your Telegram user id: 111111111
```

Then update `.env`:

```bash
sudo nano /opt/wb-cloud-watcher/.env
sudo systemctl restart wb-cloud-watcher-bot
```

## Wirenboard.cloud Token

Preferred: let the installer get it.

Manual token request:

```bash
cd /opt/wb-cloud-watcher
sudo -u wbwatcher python3 -m wb_cloud_watcher token --email ig@gilpert.ru
```

If TOTP is enabled:

```bash
sudo -u wbwatcher python3 -m wb_cloud_watcher token --email ig@gilpert.ru --totp-code 123456
```

Put the printed value into `.env`:

```dotenv
WB_TOKEN=...
```

## Backup Email

At the end of install, the installer can send a backup archive by email.

The archive contains:

- project code
- `.env`
- systemd service file
- restore instructions

Important: the archive contains access tokens. Send it only to a mailbox you control.

SMTP values requested by the installer:

- recipient email
- SMTP host
- SMTP port, usually `587`
- STARTTLS yes/no
- SMTP username
- SMTP password or app password
- From address

Restore instructions are in `RESTORE.md`.

## Service Commands

Check service:

```bash
sudo systemctl status wb-cloud-watcher-bot
```

View logs:

```bash
sudo journalctl -u wb-cloud-watcher-bot -f
```

Restart:

```bash
sudo systemctl restart wb-cloud-watcher-bot
```

Stop:

```bash
sudo systemctl stop wb-cloud-watcher-bot
```

## Manual Test

Check Wirenboard.cloud access:

```bash
cd /opt/wb-cloud-watcher
sudo -u wbwatcher python3 -m wb_cloud_watcher dump
```

Run bot in foreground:

```bash
sudo systemctl stop wb-cloud-watcher-bot
cd /opt/wb-cloud-watcher
sudo -u wbwatcher python3 -m wb_cloud_watcher bot
```

In Telegram:

```text
/start
/status
/status SERIAL
/id
```

Stop foreground mode with `Ctrl+C`, then start service again:

```bash
sudo systemctl start wb-cloud-watcher-bot
```

## Update Deployment

Pull new code:

```bash
cd /opt/wb-cloud-watcher
sudo git pull
sudo chown -R wbwatcher:wbwatcher /opt/wb-cloud-watcher
sudo chmod 600 /opt/wb-cloud-watcher/.env
sudo systemctl restart wb-cloud-watcher-bot
```

## Security Notes

- Store `WB_TOKEN` and `TELEGRAM_BOT_TOKEN` only in `.env`.
- Keep `.env` permissions at `600`.
- Do not store the Wirenboard.cloud password on the server.
- Do not send the Wirenboard.cloud password in chat.
- Do not expose a public HTTP port for this bot; it uses Telegram long polling.
- Keep the email backup private because it contains `.env`.
