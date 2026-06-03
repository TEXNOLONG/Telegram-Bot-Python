import asyncio
import time
import aiohttp
from html import escape


async def run_stress_test(
    url: str,
    total: int = 100,
    concurrency: int = 50,
    progress_cb=None,
) -> dict:
    results = []
    semaphore = asyncio.Semaphore(concurrency)
    done_count = 0
    lock = asyncio.Lock()
    start_wall = time.monotonic()

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; LoadBot/2.0)",
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }

    # shared live counters (mutable dict so closures see updates)
    live = {"done": 0, "success": 0, "failed": 0}

    async def one_request(session: aiohttp.ClientSession) -> dict:
        async with semaphore:
            t0 = time.monotonic()
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=15, connect=5),
                    allow_redirects=True,
                    max_redirects=3,
                ) as resp:
                    await resp.read()
                    elapsed = time.monotonic() - t0
                    result = {"ok": resp.status < 400, "status": resp.status, "time": elapsed}
            except asyncio.TimeoutError:
                result = {"ok": False, "error": "timeout", "time": time.monotonic() - t0}
            except aiohttp.ClientConnectorError:
                result = {"ok": False, "error": "connect_error", "time": time.monotonic() - t0}
            except (aiohttp.ClientError, Exception) as e:
                result = {"ok": False, "error": type(e).__name__, "time": time.monotonic() - t0}

            async with lock:
                live["done"] += 1
                if result.get("ok"):
                    live["success"] += 1
                else:
                    live["failed"] += 1
                step = max(100, total // 20)
                if progress_cb and live["done"] % step == 0:
                    elapsed_wall = time.monotonic() - start_wall
                    rps = live["done"] / elapsed_wall if elapsed_wall > 0 else 0
                    await progress_cb(live["done"], total, live["success"], live["failed"], rps)

            return result

    connector = aiohttp.TCPConnector(
        limit=concurrency,
        limit_per_host=concurrency,
        ttl_dns_cache=300,
        enable_cleanup_closed=True,
        force_close=False,
    )

    async with aiohttp.ClientSession(
        headers=headers,
        connector=connector,
        connector_owner=True,
    ) as session:
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
    }


def format_stress_report(url: str, data: dict) -> str:
    sr = data["success_rate"]
    if sr >= 99:
        health = "🟢 Сервер держит нагрузку отлично"
    elif sr >= 90:
        health = "🟡 Небольшие потери под нагрузкой"
    elif sr >= 70:
        health = "🟠 Сервер справляется с трудом"
    else:
        health = "🔴 Сервер не справился с нагрузкой"

    rps = data["rps"]
    wall = data["wall_time"]

    lines = [
        f"🔥 <b>Стресс-тест завершён</b>",
        f"<code>{escape(url)}</code>\n",
        f"⚙️ <b>Параметры:</b> {data['total']:,} запросов • {data['concurrency']} потоков",
        f"⏱ <b>Время теста:</b> {wall:.1f} сек\n",
        f"<b>Результат:</b>",
        f"  ✅ Успешных:  <b>{data['success']:,}</b>  ({sr:.1f}%)",
        f"  ❌ Ошибок:    <b>{data['failed']:,}</b>",
        f"  🚀 RPS:       <b>{rps:.0f}</b> запр/сек\n",
        f"<b>Время отклика:</b>",
        f"  ⚡ Мин:   <b>{data['min_time']*1000:.0f} мс</b>",
        f"  📊 P50:   <b>{data['p50']*1000:.0f} мс</b>",
        f"  📊 P75:   <b>{data['p75']*1000:.0f} мс</b>",
        f"  📊 P95:   <b>{data['p95']*1000:.0f} мс</b>",
        f"  📊 P99:   <b>{data['p99']*1000:.0f} мс</b>",
        f"  🐢 Макс:  <b>{data['max_time']*1000:.0f} мс</b>",
    ]

    if data["status_counts"]:
        lines.append("\n<b>HTTP статусы:</b>")
        for code, cnt in sorted(data["status_counts"].items()):
            icon = "🟢" if code < 300 else "🟡" if code < 400 else "🔴"
            lines.append(f"  {icon} {code}: {cnt:,}×")

    if data["error_types"]:
        lines.append("\n<b>Ошибки:</b>")
        for err, cnt in sorted(data["error_types"].items(), key=lambda x: -x[1]):
            lines.append(f"  ⚠️ {escape(err)}: {cnt:,}×")

    lines.append(f"\n{health}")
    return "\n".join(lines)
