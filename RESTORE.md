# Restore From Backup

The installer can email a backup archive named like:

```text
wb-cloud-watcher-backup-YYYYMMDDTHHMMSSZ.tar.gz
```

The archive contains:

- full project code
- `.env` with tokens and runtime settings
- installed systemd service file
- README and deployment notes

Keep the archive private because `.env` contains access tokens.

## Restore

On a new server:

```bash
sudo apt update
sudo apt install -y python3 git
sudo mkdir -p /opt/wb-cloud-watcher
sudo tar -xzf wb-cloud-watcher-backup-YYYYMMDDTHHMMSSZ.tar.gz -C /opt
```

If the backup was created by `install.py`, it contains the project code and configuration needed to run the bot.

Create the service user:

```bash
sudo useradd --system --home /opt/wb-cloud-watcher --shell /usr/sbin/nologin wbwatcher
sudo chown -R wbwatcher:wbwatcher /opt/wb-cloud-watcher
sudo chmod 600 /opt/wb-cloud-watcher/.env
```

Install and start systemd service:

```bash
sudo cp /opt/wb-cloud-watcher/deploy/wb-cloud-watcher-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wb-cloud-watcher-bot
sudo systemctl status wb-cloud-watcher-bot
```
