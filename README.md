# SmartSpaceAlarmBot

Telegram-бот для мониторинга контроллеров Wirenboard.cloud.

Бот опрашивает `GET /api/v1/controllers/`, смотрит поле `isAgentOk`, показывает текущий статус контроллеров и присылает уведомления, когда контроллер упал или снова поднялся.

Репозиторий:

```text
https://github.com/IG-SSP/SmartSpaceAlarmBot
```

## Что умеет

- Проверяет контроллеры Wirenboard.cloud через API.
- Полностью русскоязычный Telegram-интерфейс.
- `/start` показывает стартовую анимацию и ведет пользователя в Mini App.
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
- Делает постраничные inline-кнопки, чтобы нормально работать с большим числом контроллеров.
- Поддерживает Telegram Mini App: сводка, поиск, фильтры и быстрые ссылки на веб-интерфейс контроллеров.
- В Mini App есть история падений и восстановлений.
- Каждый пользователь сам выбирает тему уведомлений и задержку offline-оповещения в настройках.
- Админы могут добавлять и удалять пользователей из Mini App по Telegram ID или известному username.
- Для упавших объектов умеет показывать локальный HTTP-доступ по IP из заметки `IP`.
- При старте и по админской кнопке создает отсутствующие заметки `IP=CHANGE_ME` в Wirenboard.cloud.
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
WB_REFRESH_TOKEN=...
WB_AUTH_SCHEME=Bearer
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ADMIN_USER_IDS=111111111
TELEGRAM_ALLOWED_USER_IDS=111111111,222222222
TELEGRAM_USERS_FILE=.state/telegram_users.json
BACKUP_DIR=.backups
WEBAPP_PUBLIC_URL=https://176.124.201.26.sslip.io
WEBAPP_HOST=127.0.0.1
WEBAPP_PORT=8088
WEBAPP_AUTH_MAX_AGE_SECONDS=86400
POLL_INTERVAL_SECONDS=60
```

`WB_TOKEN`, `WB_REFRESH_TOKEN` и `TELEGRAM_BOT_TOKEN` нельзя коммитить в GitHub.

Для JWT access token, который выдает `/api/v1/auth/token/`, используется:

```dotenv
WB_AUTH_SCHEME=Bearer
```

Если Wirenboard.cloud вернет `401`, клиент автоматически попробует второй вариант `Authorization: Token ...`.
Если access token истек, бот использует `WB_REFRESH_TOKEN`, получает новый access token и обновляет `.env`.

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
WB_REFRESH_TOKEN=...
WB_AUTH_SCHEME=Bearer
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

Список объектов выводится постранично: по 12 объектов на страницу. Если объектов больше, бот показывает кнопки `Назад` и `Вперед`.

Админов нельзя удалить через `/deluser`. Чтобы изменить список админов, нужно поменять `TELEGRAM_ADMIN_USER_IDS` в `.env` и перезапустить сервис.

## Telegram Mini App

Если задан `WEBAPP_PUBLIC_URL`, бот:

- запускает встроенный HTTP-сервер на `WEBAPP_HOST:WEBAPP_PORT`;
- добавляет кнопку `Открыть приложение` в `/start`;
- настраивает Telegram menu button `Статус объектов`;
- отдает Mini App с поиском, фильтрами и ссылками на удаленный доступ.

Telegram открывает Mini App только по публичному HTTPS. На VPS можно поставить Caddy и проксировать домен на локальный порт:

```caddyfile
176.124.201.26.sslip.io {
    reverse_proxy 127.0.0.1:8088
}
```

API приложения защищен Telegram `initData`: пользователь должен открыть приложение из Telegram, подпись должна быть валидной, а Telegram ID должен быть в approved/admin списке.

## Локальный доступ по IP

Для каждого контроллера бот смотрит `userDefinedData` в Wirenboard.cloud и ищет заметку:

```json
{"label": "IP", "value": "192.168.1.1/16"}
```

Маска `/16` допускается, но для ссылки используется только IP:

```text
http://192.168.1.1/
```

Если заметки `IP` нет, сервис при старте попробует создать ее сам со значением `CHANGE_ME`. Админ может повторить проверку из бота кнопкой `Проверить IP-заметки`. После этого значение удобно поправить прямо в веб-интерфейсе Wirenboard.cloud.

Для offline-контроллера Mini App показывает локальную кнопку и кнопку `Пинг`. Перед локальным переходом или проверкой нужно включить VPN до объекта.

Кнопка локального доступа просто открывает `http://<IP>/`. Бот не проверяет локальную сеть с VPS: локальный доступ должен быть доступен с устройства пользователя после включения VPN.

## История и настройки

Mini App содержит разделы:

- `Статус` - текущие состояния объектов.
- `История` - последние падения и восстановления.
- `Настройки` - тема уведомлений и задержка offline-оповещения.

Админам в `Настройках` доступно управление пользователями. Добавление по `@username` работает только если пользователь уже открывал бота или Mini App: Telegram Bot API не дает получить ID произвольного username без предварительного взаимодействия. Для неизвестных пользователей используйте Telegram ID.

Тема `Матрица` доступна только админам. Обычные пользователи могут выбрать светлую или темную тему.

## Несколько организаций

Бот работает от одного Wirenboard.cloud токена и видит все контроллеры, доступные этому аккаунту. Если аккаунт имеет доступ к нескольким организациям, в Mini App будет показан `organization_id`, а проверки IP-заметок будут выполняться для всех доступных контроллеров.

Сейчас approved users получают общий список объектов. Разделение пользователей по организациям можно добавить отдельным слоем прав по `organization_id`.

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

Пользователь должен хотя бы один раз написать боту сам. Telegram не позволяет боту первым создать приватный чат. Если в логах есть `chat not found`, значит пользователь добавлен в список, но еще не нажал Start/не написал боту.

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

## Уведомления при запуске

Если включено:

```dotenv
NOTIFY_ON_FIRST_RUN=true
```

бот при старте отправит уведомления по контроллерам, которые уже находятся в `OFFLINE`. Если какому-то пользователю нельзя отправить сообщение, ошибка будет записана в лог, но бот продолжит работать.

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
