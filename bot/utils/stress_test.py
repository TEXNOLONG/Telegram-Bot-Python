import asyncio
import time
import random
import string
import aiohttp
from html import escape

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.82 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; WOW64; Trident/7.0; rv:11.0) like Gecko",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Linux; Android 13; Samsung Galaxy S23) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/24.0 Chrome/117.0.0.0 Mobile Safari/537.36",
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "curl/8.7.1",
    "python-requests/2.31.0",
    "Go-http-client/2.0",
    "okhttp/4.12.0",
    "Apache-HttpClient/4.5.14 (Java/17)",
    "axios/1.6.8",
]

REFERERS = [
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://yandex.ru/",
    "https://t.me/",
    "https://vk.com/",
    "https://twitter.com/",
    "https://reddit.com/",
    "https://dzen.ru/",
    "",
]

ACCEPT_LANGS = [
    "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-US,en;q=0.9",
    "de-DE,de;q=0.9,en;q=0.8",
    "zh-CN,zh;q=0.9",
    "fr-FR,fr;q=0.9,en;q=0.8",
    "uk-UA,uk;q=0.9,ru;q=0.8",
]


def _rand_str(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _rand_headers() -> dict:
    ua = random.choice(USER_AGENTS)
    ref = random.choice(REFERERS)
    lang = random.choice(ACCEPT_LANGS)
    h = {
        "User-Agent": ua,
        "Accept": random.choice([
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "text/html,application/xhtml+xml,*/*;q=0.9",
            "*/*",
            "application/json, text/plain, */*",
        ]),
        "Accept-Language": lang,
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": random.choice(["no-cache", "no-store", "max-age=0", "must-revalidate"]),
        "Pragma": "no-cache",
        "Connection": random.choice(["keep-alive", "close"]),
        "Upgrade-Insecure-Requests": "1",
    }
    if ref:
        h["Referer"] = ref
    if random.random() < 0.4:
        h["X-Forwarded-For"] = f"{random.randint(1,254)}.{random.randint(0,254)}.{random.randint(0,254)}.{random.randint(1,254)}"
    if random.random() < 0.3:
        h["X-Real-IP"] = f"{random.randint(1,254)}.{random.randint(0,254)}.{random.randint(0,254)}.{random.randint(1,254)}"
    if random.random() < 0.3:
        h["X-Request-ID"] = _rand_str(16)
    return h


def _bust_url(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_={_rand_str(12)}&r={random.randint(10000,99999)}"


def _rand_post_body() -> bytes:
    size = random.randint(64, 512)
    return ("&".join(f"{_rand_str(6)}={_rand_str(random.randint(4,16))}" for _ in range(size // 16))).encode()


async def run_stress_test(
    url: str,
    total: int = 100,
    concurrency: int = 50,
    progress_cb=None,
) -> dict:
    results = []
    semaphore = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    start_wall = time.monotonic()

    live = {"done": 0, "success": 0, "failed": 0}

    # Methods mix: mostly GET, some POST, some HEAD
    method_pool = ["GET"] * 6 + ["POST"] * 3 + ["HEAD"] * 1

    async def one_request(session: aiohttp.ClientSession) -> dict:
        async with semaphore:
            method = random.choice(method_pool)
            req_url = _bust_url(url)
            headers = _rand_headers()
            t0 = time.monotonic()
            try:
                kwargs: dict = {
                    "headers": headers,
                    "timeout": aiohttp.ClientTimeout(total=12, connect=4),
                    "allow_redirects": True,
                    "max_redirects": 5,
                    "ssl": False,
                }
                if method == "POST":
                    kwargs["data"] = _rand_post_body()
                    headers["Content-Type"] = "application/x-www-form-urlencoded"

                async with session.request(method, req_url, **kwargs) as resp:
                    await resp.read()
                    elapsed = time.monotonic() - t0
                    result = {"ok": resp.status < 500, "status": resp.status, "time": elapsed, "method": method}
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "timeout", "time": time.monotonic() - t0, "method": method}
            except aiohttp.ClientConnectorError:
                result = {"ok": False, "error": "connect_error", "time": time.monotonic() - t0, "method": method}
            except aiohttp.TooManyRedirects:
                result = {"ok": False, "error": "redirect_loop", "time": time.monotonic() - t0, "method": method}
            except (aiohttp.ClientError, Exception) as e:
                result = {"ok": False, "error": type(e).__name__, "time": time.monotonic() - t0, "method": method}

            async with lock:
                live["done"] += 1
                if result.get("ok"):
                    live["success"] += 1
                else:
                    live["failed"] += 1
                step = max(50, total // 25)
                if progress_cb and live["done"] % step == 0:
                    elapsed_wall = time.monotonic() - start_wall
                    rps = live["done"] / elapsed_wall if elapsed_wall > 0 else 0
                    await progress_cb(live["done"], total, live["success"], live["failed"], rps)

            return result

    # Use force_close=True to exhaust target's connection pool faster
    connector = aiohttp.TCPConnector(
        limit=concurrency,
        limit_per_host=concurrency,
        ttl_dns_cache=60,
        enable_cleanup_closed=True,
        force_close=True,
    )

    async with aiohttp.ClientSession(connector=connector, connector_owner=True) as session:
        tasks = [one_request(session) for _ in range(total)]
        results = list(await asyncio.gather(*tasks, return_exceptions=False))

    wall_time = time.monotonic() - start_wall

    times = sorted(r["time"] for r in results)
    success = [r for r in results if r.get("ok")]
    failed = [r for r in results if not r.get("ok")]
    n = len(times)

    def pct(p: float) -> float:
        idx = min(int(n * p / 100), n - 1)
        return times[idx]

    status_counts: dict[int, int] = {}
    for r in results:
        s = r.get("status")
        if s:
            status_counts[s] = status_counts.get(s, 0) + 1

    error_types: dict[str, int] = {}
    for r in failed:
        e = r.get("error", "unknown")
        error_types[e] = error_types.get(e, 0) + 1

    method_counts: dict[str, int] = {}
    for r in results:
        m = r.get("method", "GET")
        method_counts[m] = method_counts.get(m, 0) + 1

    return {
        "total": total,
        "concurrency": concurrency,
        "success": len(success),
        "failed": len(failed),
        "success_rate": len(success) / total * 100 if total else 0,
        "wall_time": wall_time,
        "rps": total / wall_time if wall_time > 0 else 0,
        "avg_time": sum(times) / n if n else 0,
        "min_time": times[0] if times else 0,
        "max_time": times[-1] if times else 0,
        "p50": pct(50),
        "p75": pct(75),
        "p95": pct(95),
        "p99": pct(99),
        "status_counts": status_counts,
        "error_types": error_types,
        "method_counts": method_counts,
    }


def format_stress_report(url: str, data: dict) -> str:
    sr = data["success_rate"]
    rps = data["rps"]
    wall = data["wall_time"]
    avg = data["avg_time"] * 1000
    p95 = data["p95"] * 1000

    if sr >= 99 and p95 < 500:
        verdict = "🟢 Сервер уверенно держит нагрузку"
        pressure = "🛡 Защита: высокая"
    elif sr >= 95 and p95 < 1000:
        verdict = "🟡 Незначительные просадки под нагрузкой"
        pressure = "⚙️ Защита: средняя"
    elif sr >= 80:
        verdict = "🟠 Заметные замедления, часть запросов теряется"
        pressure = "⚠️ Защита: слабая"
    elif sr >= 50:
        verdict = "🔴 Сервер с трудом справляется"
        pressure = "💥 Защита: критическая"
    else:
        verdict = "💀 Сервер не справился с нагрузкой"
        pressure = "☠️ Защита: отсутствует"

    methods_str = "  ".join(
        f"{m}: {c:,}" for m, c in sorted(data.get("method_counts", {}).items())
    )

    lines = [
        f"🔥 <b>СТРЕСС-ТЕСТ ЗАВЕРШЁН</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 <code>{escape(url)}</code>\n",
        f"⚙️ <b>Нагрузка:</b>  {data['total']:,} запросов  •  {data['concurrency']} потоков",
        f"🕐 <b>Время теста:</b>  {wall:.1f} сек\n",
        f"<b>📊 Результат:</b>",
        f"  ✅ Успешных:  <b>{data['success']:,}</b>  ({sr:.1f}%)",
        f"  ❌ Ошибок:    <b>{data['failed']:,}</b>",
        f"  🚀 RPS:       <b>{rps:.0f}</b> запр/сек",
        f"  📡 Методы:    <b>{methods_str}</b>\n",
        f"<b>⏱ Время отклика:</b>",
        f"  ⚡ Мин:   <b>{data['min_time']*1000:.0f} мс</b>",
        f"  📊 Ср:    <b>{avg:.0f} мс</b>",
        f"  📊 P50:   <b>{data['p50']*1000:.0f} мс</b>",
        f"  📊 P95:   <b>{p95:.0f} мс</b>",
        f"  📊 P99:   <b>{data['p99']*1000:.0f} мс</b>",
        f"  🐢 Макс:  <b>{data['max_time']*1000:.0f} мс</b>",
    ]

    if data["status_counts"]:
        lines.append("\n<b>🔢 HTTP-статусы:</b>")
        for code, cnt in sorted(data["status_counts"].items()):
            if code < 300:
                icon = "🟢"
            elif code < 400:
                icon = "🔵"
            elif code == 429:
                icon = "🟡"
            elif code < 500:
                icon = "🟠"
            else:
                icon = "🔴"
            lines.append(f"  {icon} {code}: {cnt:,}×")

    if data["error_types"]:
        lines.append("\n<b>⚠️ Типы ошибок:</b>")
        for err, cnt in sorted(data["error_types"].items(), key=lambda x: -x[1]):
            lines.append(f"  • {escape(err)}: {cnt:,}×")

    lines += [
        f"\n━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{verdict}",
        f"{pressure}",
    ]

    if 429 in data.get("status_counts", {}):
        lines.append("🚧 Обнаружен rate-limit (429) — сервер защищается")
    if data.get("error_types", {}).get("connect_error", 0) > data["total"] * 0.3:
        lines.append("🔌 Много ошибок подключения — возможна блокировка IP")

    return "\n".join(lines)
