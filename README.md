# SmartSpaceAlarmBot

Telegram-бот для мониторинга контроллеров Wirenboard.cloud.

Бот опрашивает `GET /api/v1/controllers/`, смотрит поле `isAgentOk`, показывает текущий статус контроллеров и присылает уведомления, когда контроллер упал или снова поднялся.

Репозиторий:

```text
https://github.com/IG-SSP/SmartSpaceAlarmBot
```

## Что умеет

- Проверяет контроллеры Wirenboard.cloud через API.
- Показывает статус всех объектов.
- Показывает статус конкретного контроллера по serial number или части friendly name.
- Использует `description` как понятное имя объекта.
- Добавляет кнопку удаленного доступа:

```text
https://wirenboard.cloud/connect/http/<serialNumber>/
```

- Поддерживает approved users.
- Поддерживает admins.
- Позволяет админам добавлять и удалять пользователей прямо из бота.
- Позволяет админам скачать backup-архив через бота.
- Хранит состояние и список пользователей локально в `.state`.
- Работает без внешних Python-зависимостей.

## Быстрый деплой

На чистом Ubuntu/Debian VPS:

```bash
sudo apt update
sudo apt install -y python3 git
sudo git clone https://github.com/IG-SSP/SmartSpaceAlarmBot.git /opt/wb-cloud-watcher
cd /opt/wb-cloud-watcher
sudo python3 install.py
```

Установщик можно запускать повторно. Он не должен ломаться, если уже существуют:

- `/opt/wb-cloud-watcher`
- пользователь `wbwatcher`
- `.env`
- systemd service
- backup-директория

Подробности: [DEPLOY.md](DEPLOY.md).

## Конфиг

Основной конфиг лежит тут:

```text
/opt/wb-cloud-watcher/.env
```

Минимально важные переменные:

```dotenv
WB_TOKEN=...
WB_AUTH_SCHEME=Bearer
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_USER_IDS=111111111
TELEGRAM_ALLOWED_USER_IDS=111111111,222222222
TELEGRAM_USERS_FILE=.state/telegram_users.json
BACKUP_DIR=.backups
POLL_INTERVAL_SECONDS=60
```

`WB_TOKEN` и `TELEGRAM_BOT_TOKEN` нельзя коммитить в GitHub.

Для JWT access token, который выдает `/api/v1/auth/token/`, используется:

```dotenv
WB_AUTH_SCHEME=Bearer
```

Если Wirenboard.cloud вернет `401`, клиент автоматически попробует второй вариант `Authorization: Token ...`.

## Получение WB_TOKEN

Пароль от Wirenboard.cloud не нужно писать в чат и не нужно хранить в `.env`.

Получить токен можно интерактивно:

```bash
cd /opt/wb-cloud-watcher
sudo -u wbwatcher python3 -m wb_cloud_watcher token --email ig@gilpert.ru
```

Если включен TOTP:

```bash
sudo -u wbwatcher python3 -m wb_cloud_watcher token --email ig@gilpert.ru --totp-code 123456
```

Команда напечатает:

```dotenv
WB_TOKEN=...
```

## Команды бота

Для всех approved users:

```text
/start
/help
/id
/status
/status SERIAL
```

Только для admins:

```text
/users
/adduser 123456789
/deluser 123456789
/backup
```

Админов нельзя удалить через `/deluser`. Чтобы изменить список админов, нужно поменять `TELEGRAM_ADMIN_USER_IDS` в `.env` и перезапустить сервис.

## Как узнать Telegram user id

Если пользователь еще не разрешен, он все равно может написать боту:

```text
/id
```

Бот ответит:

```text
Access denied.
Your Telegram user id: 111111111
```

После этого админ может добавить его:

```text
/adduser 111111111
```

## Backup

Email backup больше не используется.

Админ может скачать свежий backup прямо из Telegram:

```text
/backup
```

Архив содержит:

- код проекта;
- `.env`;
- systemd service;
- инструкции.

Архив содержит токены, поэтому его нельзя пересылать в чужие чаты.

Локально backup сохраняется в:

```text
/opt/wb-cloud-watcher/.backups
```

## Service

Статус:

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

## Проверка

Локально:

```bash
python -m unittest discover -s tests
```

Вручную проверить доступ к Wirenboard.cloud:

```bash
cd /opt/wb-cloud-watcher
sudo -u wbwatcher python3 -m wb_cloud_watcher dump
```
