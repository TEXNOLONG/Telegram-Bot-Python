# LoadTest Pro — Инструкция по установке на VPS (Ubuntu 22.04 / 24.04)

## Требования

| Параметр | Минимум | Рекомендуется |
|----------|---------|---------------|
| CPU | 2 vCPU | 4 vCPU |
| RAM | 2 GB | 4 GB |
| Диск | 20 GB SSD | 40 GB SSD |
| ОС | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |
| Порты | 22, 80, 443 | — |

---

## Шаг 1 — Подключитесь к серверу

```bash
ssh root@ВАШ_IP
```

---

## Шаг 2 — Загрузите код на сервер

**Вариант A: через Git (если у вас репозиторий)**
```bash
git clone https://github.com/ВАШ_АККАУНТ/loadtest-pro /opt/loadtest-pro
```

**Вариант B: загрузить архив**
```bash
# На локальном компьютере:
zip -r loadtest-pro.zip . -x "*.pyc" -x "__pycache__/*" -x ".git/*"
scp loadtest-pro.zip root@ВАШ_IP:/root/

# На сервере:
cd /root && unzip loadtest-pro.zip -d /opt/loadtest-pro
```

**Вариант C: rsync (самый быстрый)**
```bash
rsync -avz --exclude='.git' --exclude='__pycache__' \
  ./ root@ВАШ_IP:/opt/loadtest-pro/
```

---

## Шаг 3 — Запустите установочный скрипт

```bash
cd /opt/loadtest-pro
chmod +x vps_setup.sh
sudo bash vps_setup.sh
```

Скрипт автоматически:
- Установит Python 3.11, PostgreSQL, Nginx, Supervisor, Fail2ban
- Создаст базу данных и пользователя
- Настроит автозапуск через Supervisor
- Откроет порты 80/443 через UFW
- Оптимизирует ядро Linux для большого числа соединений

---

## Шаг 4 — Заполните токены

```bash
nano /opt/loadtest-pro/.env
```

Укажите:
```env
BOT_TOKEN=токен_от_BotFather
ADMIN_ID=ваш_telegram_id
CRYPTO_BOT_TOKEN=токен_CryptoBot
DATABASE_URL=postgresql://loadtest:ПАРОЛЬ@localhost:5432/loadtest_db
SESSION_SECRET=случайная_строка_32_символа
REPLIT_DEV_DOMAIN=ВАШ_IP_ИЛИ_ДОМЕН
```

> **Где взять токены:**
> - `BOT_TOKEN` — у [@BotFather](https://t.me/BotFather) → /newbot
> - `ADMIN_ID` — у [@userinfobot](https://t.me/userinfobot)
> - `CRYPTO_BOT_TOKEN` — у [@CryptoBot](https://t.me/CryptoBot) → /start → API

Пароль от БД был показан при установке. Также его можно найти в `/opt/loadtest-pro/.env` после скрипта.

---

## Шаг 5 — Перезапустите бота

```bash
supervisorctl restart loadtest-pro
```

Проверьте статус:
```bash
supervisorctl status loadtest-pro
```

---

## Шаг 6 (опционально) — HTTPS через Let's Encrypt

Нужен домен, привязанный к IP сервера.

```bash
certbot --nginx -d ВАШ_ДОМЕН.COM
```

После этого обновите `.env`:
```env
REPLIT_DEV_DOMAIN=ВАШ_ДОМЕН.COM
```

И перезапустите:
```bash
supervisorctl restart loadtest-pro
```

---

## Управление ботом

```bash
# Статус
supervisorctl status loadtest-pro

# Перезапуск
supervisorctl restart loadtest-pro

# Остановить
supervisorctl stop loadtest-pro

# Логи в реальном времени
tail -f /var/log/loadtest-pro/app.log

# Логи ошибок
tail -f /var/log/loadtest-pro/error.log

# Логи тестов с IP-адресами пользователей
grep "LOAD_TEST_LOG" /var/log/loadtest-pro/app.log
```

---

## Логирование тестов

Каждый запущенный тест записывается в лог:

```
LOAD_TEST_LOG | task=<uuid> | user=<telegram_id> | ip=<ip_адрес> | url=<цель> | mode=<lite|pro|flood>
```

Быстрый поиск по IP:
```bash
grep "LOAD_TEST_LOG" /var/log/loadtest-pro/app.log | grep "ip=1.2.3.4"
```

Поиск по пользователю:
```bash
grep "LOAD_TEST_LOG" /var/log/loadtest-pro/app.log | grep "user=123456789"
```

---

## Обновление бота

```bash
cd /opt/loadtest-pro

# Если используете Git:
git pull

# Обновить зависимости (если изменился requirements.txt):
./venv/bin/pip install -r requirements.txt -q

# Перезапустить
supervisorctl restart loadtest-pro
```

---

## Устранение проблем

### Бот не запускается
```bash
tail -50 /var/log/loadtest-pro/error.log
# Проверьте токены в .env
# Убедитесь, что PostgreSQL работает:
systemctl status postgresql
```

### Порт 5000 не открыт (Nginx не проксирует)
```bash
curl http://localhost:5000/
nginx -t
systemctl status nginx
```

### Ошибка подключения к БД
```bash
sudo -u postgres psql -c "\l"
# Пересоздать пользователя:
sudo -u postgres psql -c "ALTER USER loadtest WITH PASSWORD 'НОВЫЙ_ПАРОЛЬ';"
```

### Слишком много открытых файлов
```bash
ulimit -n 65536
sysctl -p
supervisorctl restart loadtest-pro
```

---

## Безопасность

- Fail2ban автоматически блокирует брутфорс SSH
- UFW открывает только порты 22, 80, 443
- Приложение запускается от непривилегированного пользователя `loadtest`
- .env файл имеет права `600` (только владелец)
- IP-адреса всех пользователей логируются при каждом тесте

---

## Структура на сервере

```
/opt/loadtest-pro/      — код приложения
/opt/loadtest-pro/.env  — секреты (chmod 600)
/opt/loadtest-pro/venv/ — Python окружение
/var/log/loadtest-pro/  — логи (app.log, error.log)
/etc/supervisor/conf.d/loadtest-pro.conf — автозапуск
/etc/nginx/sites-available/loadtest-pro  — веб-прокси
```
