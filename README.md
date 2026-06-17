# Lumen Bots

Рабочий репозиторий Telegram-ботов сети «Люмен».

## Что внутри

- `bots/business360_daily_bot.py` — основной LumenBot: ежедневный срез Business360, план-факт, сравнение 3 месяца, оборачиваемость, потенциал фильмов, заполняемость.
- `bots/cinema_news_bot.py` — Cinema News: ежедневный дайджест кинобизнеса и сборы.
- `bots/datalens_daily_bot.py` — старый DataLens/Work info бот, сейчас не используется в проде.
- `systemd/` — unit/timer файлы для VPS.
- `config/*.env.example` — шаблоны переменных окружения без секретов.

## Продакшен на VPS

- Код: `/opt/lumen-bots`
- Секреты: `/etc/lumen-bots/*.env`
- Состояние/cookie/history: `/var/lib/lumen-bots`

Секреты, cookies, токены и выгрузки не хранятся в Git.

## Базовые команды

```bash
python3 -m py_compile bots/business360_daily_bot.py bots/cinema_news_bot.py bots/datalens_daily_bot.py
systemctl status lumen-business360-button.service
systemctl list-timers 'lumen-*' 'cinema-news-*'
```
