---
name: LoadTest Pro architecture
description: Ключевые архитектурные решения бота нагрузочного тестирования
---

# LoadTest Pro — архитектура

**Why:** non-obvious decisions that must be consistent across future sessions.

## Entry point pattern
`main.py` запускает Flask в daemon-thread, затем `asyncio.run(run_bot())` который внутри `asyncio.gather()` запускает polling, payment_poller, и task_queue_worker.

**How to apply:** Не добавлять второй asyncio.run — всё должно быть внутри одного gather.

## Report URL pattern
Любой результат (analysis, load_test) сохраняется как `Report` в PostgreSQL с UUID → URL `https://{REPLIT_DEV_DOMAIN}/report/{uuid}`. Бот отправляет только ссылку, не полный текст отчёта.

**Why:** Пользователь запросил результаты как веб-ссылки с красивым SPA.

## Task queue
Используется PostgreSQL (таблица `tasks`) как очередь. `traffic_worker.py::task_queue_worker()` поллит каждые 5 секунд, забирает `status=pending`, устанавливает `queued`, запускает `process_task()` как asyncio.create_task.

**Why:** Нет Redis — используем имеющуюся БД.

## LITE vs PRO
- LITE: генерируется Python-скрипт → пользователь скачивает и запускает локально → POST на `/api/report` с report_token → ссылка приходит в Telegram
- PRO: задача в очереди → сервер выполняет → report_id → ссылка в Telegram

## Storage interface
`bot/storage.py` — PostgreSQL-backed класс с тем же интерфейсом что был у JSON-storage. Синглтон `storage = Storage()`.

## Function names in utility modules
- `dns_checker.py`: `dns_lookup()`, `check_ports()`, `format_dns_report()`
- `ssl_checker.py`: `check_ssl()` → dict, `format_ssl_report()` → str
- `ddos_checker.py`: `check_ddos_protection()`, `format_ddos_report()`
- `site_analyzer.py`: `analyze_site()` → dict (содержит `score`)

## Terminology rules
Использовать: load_test, stress_profile, traffic_worker
Избегать: "атака", "жертва", "взлом"
Регистрация: уведомление только "Регистрация завершена" (без IP)
