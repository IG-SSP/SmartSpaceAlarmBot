# Деплой SmartSpaceAlarmBot

Инструкция для чистого Ubuntu/Debian VPS.

Репозиторий:

```text
https://github.com/IG-SSP/SmartSpaceAlarmBot
```

Бот работает через Telegram long polling. Открывать входящий HTTP-порт на сервере не нужно.

Серверу нужен только исходящий HTTPS-доступ к:

- `wirenboard.cloud`
- `api.telegram.org`

## Быстрая установка

```bash
sudo apt update
sudo apt install -y python3 git
sudo git clone https://github.com/IG-SSP/SmartSpaceAlarmBot.git /opt/wb-cloud-watcher
cd /opt/wb-cloud-watcher
sudo python3 install.py
```

Установщик спросит:

- директорию установки, по умолчанию `/opt/wb-cloud-watcher`;
- системного пользователя, по умолчанию `wbwatcher`;
- имя systemd service, по умолчанию `wb-cloud-watcher-bot`;
- email Wirenboard.cloud, по умолчанию `ig@gilpert.ru`;
- существующие `WB_TOKEN`/`WB_REFRESH_TOKEN` или пароль для получения токенов;
- TOTP/recovery code, если нужен;
- Telegram bot token от `@BotFather`;
- Telegram user IDs админов;
- Telegram user IDs разрешенных пользователей;
- публичный HTTPS URL для Telegram Mini App, если приложение нужно включить сразу;
- локальный порт Mini App, по умолчанию `8088`;
- интервал проверки;
- отправлять ли уведомления об уже упавших контроллерах при первом запуске.

SMTP больше не нужен. Backup скачивается через Telegram-команду `/backup`.
Интерфейс бота полностью на русском. Списки объектов постраничные, по 12 объектов на страницу.
Если задан `WEBAPP_PUBLIC_URL`, бот также запускает Telegram Mini App.

## Повторный запуск установщика

`install.py` можно запускать несколько раз.

Повторный запуск:

- не падает, если пользователь `wbwatcher` уже существует;
- не требует удалять `/opt/wb-cloud-watcher`;
- перезаписывает `.env` новыми ответами;
- обновляет systemd service;
- делает новый локальный backup;
- повторно запускает/перезапускает service через systemd.

Повторный запуск полезен, если нужно заменить:

- `WB_TOKEN`;
- `WB_REFRESH_TOKEN`;
- `TELEGRAM_BOT_TOKEN`;
- список админов;
- интервал проверки;
- systemd service.

## Где лежит конфиг

```text
/opt/wb-cloud-watcher/.env
```

Основные переменные:

```dotenv
WB_API_URL=https://wirenboard.cloud/api/v1/controllers/?page_size=100
WB_TOKEN=...
WB_REFRESH_TOKEN=...
WB_AUTH_SCHEME=Bearer

CONTROLLERS_PATH=results
ID_FIELD=serialNumber
NAME_FIELD=description
ONLINE_FIELD=isAgentOk
LAST_SEEN_FIELD=lastAgentPingAt

STATE_FILE=.state/controllers.json
POLL_INTERVAL_SECONDS=60
NOTIFY_ON_FIRST_RUN=false
WB_TIMEOUT_SECONDS=15

TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_USER_IDS=111111111
TELEGRAM_ALLOWED_USER_IDS=111111111,222222222
TELEGRAM_USERS_FILE=.state/telegram_users.json
TELEGRAM_TIMEOUT_SECONDS=30

BACKUP_DIR=.backups

WEBAPP_PUBLIC_URL=https://176.124.201.26.sslip.io
WEBAPP_HOST=127.0.0.1
WEBAPP_PORT=8088
WEBAPP_AUTH_MAX_AGE_SECONDS=86400
```

Права на `.env`:

```bash
sudo chown wbwatcher:wbwatcher /opt/wb-cloud-watcher/.env
sudo chmod 600 /opt/wb-cloud-watcher/.env
```

## Telegram bot token

1. Открыть `@BotFather`.
2. Выполнить `/newbot`.
3. Задать имя и username.
4. Скопировать token.
5. Вставить token в установщик или в `.env`:

```dotenv
TELEGRAM_BOT_TOKEN=123456789:AA...
```

## Telegram Mini App

Mini App включается переменной:

```dotenv
WEBAPP_PUBLIC_URL=https://176.124.201.26.sslip.io
```

Встроенный web-сервер слушает только localhost:

```dotenv
WEBAPP_HOST=127.0.0.1
WEBAPP_PORT=8088
```

Telegram требует публичный HTTPS URL. Если отдельного домена нет, для VPS можно использовать `sslip.io`:

```text
https://176.124.201.26.sslip.io
```

Это имя автоматически резолвится в `176.124.201.26`. Для TLS и reverse proxy удобно поставить Caddy:

```bash
sudo apt install -y caddy
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
176.124.201.26.sslip.io {
    reverse_proxy 127.0.0.1:8088
}
EOF
sudo systemctl reload caddy
```

После изменения `.env` перезапустить сервис:

```bash
sudo systemctl restart wb-cloud-watcher-bot
```

Бот добавит кнопку `Открыть приложение` в `/start` и настроит кнопку меню Telegram `Статус объектов`.

Доступ к `/api/controllers` проверяется по Telegram `initData`, поэтому открыть API из обычного браузера без запуска из Telegram нельзя. После проверки подписи дополнительно проверяется approved/admin список пользователей.

## Админы и пользователи

Админы задаются в `.env`:

```dotenv
TELEGRAM_ADMIN_USER_IDS=111111111
```

Approved users задаются стартовым списком:

```dotenv
TELEGRAM_ALLOWED_USER_IDS=111111111,222222222
```

После первого запуска бот создает файл:

```text
/opt/wb-cloud-watcher/.state/telegram_users.json
```

Дальше список пользователей можно менять командами в Telegram.

Команды админа:

```text
/users
/adduser 123456789
/deluser 123456789
/backup
```

Админы всегда имеют доступ. `/deluser` не удаляет админов; чтобы поменять админов, нужно отредактировать `TELEGRAM_ADMIN_USER_IDS` в `.env` и перезапустить service.

## Как узнать Telegram user id

Можно временно указать:

```dotenv
TELEGRAM_ADMIN_USER_IDS=0
TELEGRAM_ALLOWED_USER_IDS=0
```

Запустить бота и написать:

```text
/id
```

Бот ответит даже неразрешенному пользователю:

```text
Access denied.
Your Telegram user id: 111111111
```

После этого поменять `.env`:

```bash
sudo nano /opt/wb-cloud-watcher/.env
sudo systemctl restart wb-cloud-watcher-bot
```

Важно: пользователь должен сам написать боту хотя бы один раз. Иначе Telegram вернет `chat not found`, когда бот попробует отправить ему уведомление.

## WB_TOKEN

Лучше дать установщику получить токен интерактивно.

Вручную:

```bash
cd /opt/wb-cloud-watcher
sudo -u wbwatcher python3 -m wb_cloud_watcher token --email ig@gilpert.ru
```

Если включен TOTP:

```bash
sudo -u wbwatcher python3 -m wb_cloud_watcher token --email ig@gilpert.ru --totp-code 123456
```

Пароль вводится скрыто. В `.env` сохраняется только токен:

```dotenv
WB_TOKEN=...
WB_REFRESH_TOKEN=...
WB_AUTH_SCHEME=Bearer
```

Если API вернет `401`, клиент автоматически попробует fallback-схему `Token`.
Если access token истек, бот использует `WB_REFRESH_TOKEN`, получает новый access token и обновляет `.env`.

## Backup через бота

Email backup отключен.

Админ пишет боту:

```text
/backup
```

Бот создает свежий архив и отправляет его как файл.

Локальная директория backup:

```text
/opt/wb-cloud-watcher/.backups
```

Архив содержит `.env` с токенами. Его нельзя пересылать посторонним.

## Уведомления при старте

Если указано:

```dotenv
NOTIFY_ON_FIRST_RUN=true
```

бот при запуске отправит уведомления по контроллерам, которые уже лежат. Это не зависит от того, был ли раньше создан `.state/controllers.json`.

Если в `TELEGRAM_ALLOWED_USER_IDS` остался `0` или пользователь еще не писал боту, в логах может появиться `chat not found`. Такая ошибка не должна останавливать service.

## Systemd

Проверить статус:

```bash
sudo systemctl status wb-cloud-watcher-bot
```

Логи:

```bash
sudo journalctl -u wb-cloud-watcher-bot -f
```

Перезапуск:

```bash
sudo systemctl restart wb-cloud-watcher-bot
```

Остановка:

```bash
sudo systemctl stop wb-cloud-watcher-bot
```

## Ручная проверка

Проверить Wirenboard.cloud:

```bash
cd /opt/wb-cloud-watcher
sudo -u wbwatcher python3 -m wb_cloud_watcher dump
```

Запустить бота в foreground:

```bash
sudo systemctl stop wb-cloud-watcher-bot
cd /opt/wb-cloud-watcher
sudo -u wbwatcher python3 -m wb_cloud_watcher bot
```

В Telegram:

```text
/start
/status
/status SERIAL
/id
/users
/backup
```

Списки контроллеров в боте постраничные: по 12 объектов на страницу. Кнопки `Назад`/`Вперед` появляются автоматически, если объектов больше одной страницы.

Остановить foreground: `Ctrl+C`.

Вернуть service:

```bash
sudo systemctl start wb-cloud-watcher-bot
```

## Обновление

```bash
cd /opt/wb-cloud-watcher
sudo git pull
sudo chown -R wbwatcher:wbwatcher /opt/wb-cloud-watcher
sudo chmod 600 /opt/wb-cloud-watcher/.env
sudo systemctl restart wb-cloud-watcher-bot
```

## Безопасность

- Не хранить пароль Wirenboard.cloud в `.env`.
- Не отправлять пароль Wirenboard.cloud в чат.
- Не коммитить `.env`.
- Держать права `.env` как `600`.
- Backup содержит токены.
- Доступ к `/backup`, `/adduser`, `/deluser`, `/users` есть только у админов.
- Входящие порты для бота открывать не нужно.
