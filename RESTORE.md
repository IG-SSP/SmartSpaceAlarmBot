# Восстановление из backup

Backup создается командой бота:

```text
/backup
```

Команда доступна только админам.

Архив называется примерно так:

```text
wb-cloud-watcher-backup-YYYYMMDDTHHMMSSZ.tar.gz
```

Внутри:

- код проекта;
- `.env` с токенами;
- `.state/telegram_users.json` со списком пользователей;
- systemd service;
- документация.

Архив содержит токены. Хранить и пересылать его нужно аккуратно.

## Восстановление

На новом сервере:

```bash
sudo apt update
sudo apt install -y python3 git
sudo mkdir -p /opt
sudo tar -xzf wb-cloud-watcher-backup-YYYYMMDDTHHMMSSZ.tar.gz -C /opt
```

Проверить структуру:

```bash
ls -la /opt/wb-cloud-watcher
```

Создать service user, если его еще нет:

```bash
sudo useradd --system --home /opt/wb-cloud-watcher --shell /usr/sbin/nologin wbwatcher || true
```

Выставить права:

```bash
sudo chown -R wbwatcher:wbwatcher /opt/wb-cloud-watcher
sudo chmod 600 /opt/wb-cloud-watcher/.env
```

Установить systemd service:

```bash
sudo cp /opt/wb-cloud-watcher/deploy/wb-cloud-watcher-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wb-cloud-watcher-bot
```

Проверить:

```bash
sudo systemctl status wb-cloud-watcher-bot
sudo journalctl -u wb-cloud-watcher-bot -f
```

Если service-файл в архиве называется иначе, посмотри содержимое директории:

```bash
ls -la /opt/wb-cloud-watcher/deploy
```
