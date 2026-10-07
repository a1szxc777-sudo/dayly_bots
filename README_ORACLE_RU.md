# Dayly — запуск Telegram-бота на Oracle Cloud Always Free

Этот комплект рассчитан на Ubuntu VM в Oracle Cloud. Бот запускается через обычный Telegram long polling, как на твоём ПК, но под управлением systemd.

## 1. Создай VM в Oracle Cloud

Открой Oracle Cloud Console → Compute → Instances → Create instance.

Рекомендуемые параметры:
- Name: `dayly-bot`
- Image: Ubuntu (Always Free Eligible)
- Shape: `VM.Standard.A1.Flex` (Ampere, Always Free), если доступен
- OCPU: 1
- RAM: 6 GB
- Assign public IPv4: Yes
- SSH: Generate a key pair for me → обязательно сохрани Private Key

Если A1 нет из-за `Out of host capacity`, попробуй другой Availability Domain, если Oracle предлагает, или подожди и повтори.

## 2. Подключись по SSH

Для Ubuntu пользователь обычно `ubuntu`.

Windows PowerShell / macOS / Linux:

```bash
ssh -i "ПУТЬ_К_КЛЮЧУ" ubuntu@PUBLIC_IP
```

Например:
```bash
ssh -i "C:\Users\ТЫ\Downloads\ssh-key.key" ubuntu@123.123.123.123
```

На macOS/Linux перед подключением может понадобиться:
```bash
chmod 400 /path/to/key.key
```

## 3. Залей файлы на VM

Самый простой вариант — GitHub. Создай private repository и загрузи:
- `bot.py`
- `requirements.txt`
- `.env.example`
- `dayly.service`
- `install.sh`
- `update.sh`

Не загружай `.env` и не загружай токены.

После этого на VM:
```bash
sudo apt update
sudo apt install -y git
cd /home/ubuntu
git clone https://github.com/ТВОЙ_USERNAME/ТВОЙ_REPO.git dayly
cd dayly
bash install.sh
```

Если репозиторий private, удобнее использовать GitHub SSH или загрузить ZIP/SCP.

## 4. Заполни секреты

```bash
nano /home/ubuntu/dayly/.env
```

Минимально:
```env
BOT_TOKEN=ТВОЙ_ТОКЕН_BOTFATHER
OWNER_ID=ТВОЙ_TELEGRAM_ID
CITY=Konaev
TIMEZONE=Asia/Almaty
LAT=43.8667
LON=77.0667
DATA_DIR=/home/ubuntu/dayly/data
```

Если нужен Claude:
```env
ANTHROPIC_API_KEY=ТВОЙ_КЛЮЧ
AI_MODEL=claude-haiku-4-5-20251001
```

Сохранить nano: Ctrl+O → Enter → Ctrl+X.

## 5. Запусти

```bash
sudo systemctl start dayly
```

Проверка:
```bash
sudo systemctl status dayly
```

Должно быть `active (running)`.

Логи в реальном времени:
```bash
sudo journalctl -u dayly -f
```

## 6. Главное — автозапуск

Он уже включён командой `systemctl enable dayly` в install.sh.

После перезагрузки VM бот сам запустится:
```bash
sudo reboot
```

После повторного SSH:
```bash
sudo systemctl status dayly
```

## 7. Если бот не запускается

Покажи последние 100 строк:
```bash
sudo journalctl -u dayly -n 100 --no-pager
```

Проверить `.env`:
```bash
cat /home/ubuntu/dayly/.env
```

ВАЖНО: эту команду не отправляй мне, если там есть реальные секреты. Если присылаешь лог, замени токены на `***`.

## 8. Если видишь Telegram 409 Conflict

Это означает, что тот же BOT_TOKEN одновременно используется другим polling-процессом.

Останови бота на ПК (`Ctrl+C`) и убедись, что старый сервер/хостинг с этим токеном тоже остановлен.

На Oracle должен работать только один экземпляр:
```bash
sudo systemctl restart dayly
```

## 9. SQLite

База бота будет здесь:
```text
/home/ubuntu/dayly/data/dayly.db
```

Она находится на boot volume VM, а не во временной папке контейнера.

## 10. Важное ограничение Oracle Free

Oracle называет эти ресурсы Always Free, но официально оставляет за собой право reclaim idle Compute instances. Для Always Free VM Oracle проверяет CPU, сеть и память за 7-дневный период. Поэтому бесплатная VM — не абсолютная гарантия 24/7.

Также при создании A1 может быть `Out of host capacity`: это означает временную нехватку бесплатной ARM-ёмкости.

## 11. Обновление бота

Если код лежит в GitHub:
```bash
cd /home/ubuntu/dayly
git pull
bash update.sh
```

Если меняешь только `.env`, достаточно:
```bash
sudo systemctl restart dayly
```

## 12. Остановка / запуск

Остановить:
```bash
sudo systemctl stop dayly
```

Запустить:
```bash
sudo systemctl start dayly
```

Перезапустить:
```bash
sudo systemctl restart dayly
```

Проверить:
```bash
sudo systemctl status dayly
```
