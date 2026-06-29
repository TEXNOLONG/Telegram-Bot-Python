# LoadTest Pro — Telegram Bot

Профессиональный Telegram-бот для нагрузочного тестирования сайтов с веб-отчётами.

## Run & Operate

- `python main.py` — запуск бота + Flask веб-сервер (порт 5000)
- Бот: `@Hayder_projectx_bot`
- Веб-панель: `/` — статус, `/register/<id>` — регистрация, `/report/<id>` — отчёт

## Stack

- Python 3.11, aiogram 3.x (Telegram Bot)
- Flask (веб-сервер: регистрация + отчёты)
- PostgreSQL + SQLAlchemy ORM (хранилище)
- aiohttp (нагрузочные тесты, HTTP-клиент)
- CryptoBot API (USDT-платежи)

## Where things live

- `main.py` — точка входа: Flask + bot + task queue worker
- `flask_app.py` — Flask маршруты (register, report, api/report)
- `bot/` — модули бота
  - `handlers/user.py` — пользовательские команды
  - `handlers/admin.py` — админ-панель
  - `handlers/payment.py` — оплата CryptoBot
  - `utils/stress_profile.py` — LITE/PRO нагрузочное тестирование
  - `utils/traffic_worker.py` — очередь задач (PostgreSQL)
  - `utils/protection_bypass.py` — 100+ User-Agent, Cloudflare detection
  - `utils/url_validator.py` — проверка целевого URL
  - `utils/script_generator.py` — генерация LITE-скриптов
  - `storage.py` — PostgreSQL-backed хранилище
  - `models.py` — SQLAlchemy модели
  - `db.py` — сессии БД
- `templates/` — HTML шаблоны (register.html, report.html, index.html)

## Architecture decisions

- Один процесс: Flask (daemon thread) + asyncio bot + task_queue_worker в одном asyncio.gather
- Все результаты (анализ + тест) сохраняются как Report с UUID → ссылка `/report/<uuid>`
- LITE-тест: скрипт скачивается и запускается локально, результат POST на `/api/report`
- PRO-тест: задача в PostgreSQL очереди, воркер запускает async TrafficWorker
- Защитный обход PRO: ротация User-Agent + session cookie pool + human jitter
- URL валидация: блокируются RFC1918, localhost, .gov/.mil домены

## Product

- **LITE**: бесплатно, 3 теста/день, скрипт запускается локально (100 RPS, 60с, HTTP GET)
- **PRO**: платная подписка (USDT), тест на серверах, до 2000 RPS, ротация сессий
- **Анализ сайта**: SEO, SSL, производительность, безопасность, стек технологий
- **Отчёты**: тёмный SPA с Chart.js, AOS.js анимации, Font Awesome иконки

## Required Secrets

- `BOT_TOKEN` — токен Telegram бота от @BotFather
- `ADMIN_ID` — Telegram ID администратора
- `CRYPTO_BOT_TOKEN` — токен CryptoBot (для платежей)
- `DATABASE_URL` — PostgreSQL строка подключения (Replit DB)
- `SESSION_SECRET` — Flask secret key

## User preferences

- Термины: load_test, stress_profile, traffic_worker (не "атака", "жертва", "взлом")
- Уведомление при регистрации: только "Регистрация завершена" (без IP)
- Результаты всегда как веб-ссылка на отчёт `/report/<uuid>`
- Тёмная тема с AOS.js + Chart.js + Font Awesome на страницах отчётов
