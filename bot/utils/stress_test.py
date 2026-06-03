import asyncio
import time
import hashlib
import aiohttp
from bs4 import BeautifulSoup
from urllib.parse import urlparse

from bot.config import BOT_TOKEN


def get_verify_code(user_id: int) -> str:
    raw = f"{user_id}:{BOT_TOKEN}:verify"
    return hashlib.sha256(raw.encode()).hexdigest()[:14]


async def check_ownership(url: str, code: str) -> tuple[bool, str]:
    """Check if user has placed the verify meta tag on the given URL."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SiteBot/1.0)"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as resp:
                if resp.status >= 400:
                    return False, f"Сайт вернул статус {resp.status}"
                html = await resp.text(errors="replace")
                soup = BeautifulSoup(html, "lxml")
                meta = soup.find("meta", attrs={"name": "site-owner", "content": code})
                if meta:
                    return True, "ok"
                return False, "Тег не найден"
    except aiohttp.ClientConnectorError:
        return False, "Не удалось подключиться к сайту"
    except asyncio.TimeoutError:
        return False, "Сайт не отвечает (таймаут 10с)"
    except Exception as e:
        return False, f"Ошибка: {type(e).__name__}"


async def run_stress_test(
    url: str,
    total: int = 50,
    concurrency: int = 10,
    progress_cb=None,
) -> dict:
    """
    Send `total` requests with up to `concurrency` at once.
    Calls progress_cb(done, total) every 10 requests if provided.
    """
    results = []
    semaphore = asyncio.Semaphore(concurrency)
    done_count = 0
    lock = asyncio.Lock()

    headers = {"User-Agent": "Mozilla/5.0 (compatible; StressBot/1.0; +check-ownership)"}

    async def one_request(session: aiohttp.ClientSession, idx: int):
        nonlocal done_count
        async with semaphore:
            t0 = time.monotonic()
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10),
                    allow_redirects=True,
                ) as resp:
                    await resp.read()
                    elapsed = time.monotonic() - t0
                    return {"ok": True, "status": resp.status, "time": elapsed}
            except asyncio.TimeoutError:
                return {"ok": False, "error": "timeout", "time": time.monotonic() - t0}
            except Exception as e:
                return {"ok": False, "error": type(e).__name__, "time": time.monotonic() - t0}
            finally:
                async with lock:
                    done_count += 1
                    if progress_cb and done_count % 10 == 0:
                        await progress_cb(done_count, total)

    connector = aiohttp.TCPConnector(limit=concurrency)
    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        tasks = [one_request(session, i) for i in range(total)]
        results = await asyncio.gather(*tasks)

    times = sorted(r["time"] for r in results)
    success = [r for r in results if r.get("ok") and r.get("status", 0) < 400]
    failed = [r for r in results if not r.get("ok") or r.get("status", 0) >= 400]
    n = len(times)

    def percentile(data, p):
        idx = min(int(len(data) * p / 100), len(data) - 1)
        return data[idx]

    total_time = sum(times)
    avg = total_time / n if n else 0
    rps = n / total_time * concurrency if total_time > 0 else 0

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
        "avg_time": avg,
        "min_time": min(times) if times else 0,
        "max_time": max(times) if times else 0,
        "p50": percentile(times, 50),
        "p95": percentile(times, 95),
        "p99": percentile(times, 99),
        "rps": rps,
        "status_counts": status_counts,
        "error_types": error_types,
    }


def format_stress_report(url: str, data: dict) -> str:
    from html import escape
    sr = data["success_rate"]
    if sr >= 99:
        health = "🟢 Отлично"
    elif sr >= 90:
        health = "🟡 Хорошо"
    elif sr >= 70:
        health = "🟠 Слабо"
    else:
        health = "🔴 Критично"

    lines = [
        f"🔥 <b>Стресс-тест завершён</b>",
        f"<code>{escape(url)}</code>\n",
        f"📊 <b>Нагрузка:</b> {data['total']} запросов, {data['concurrency']} параллельно\n",
        f"<b>Результаты:</b>",
        f"  ✅ Успешных: <b>{data['success']}</b> ({sr:.1f}%)",
        f"  ❌ Ошибок:   <b>{data['failed']}</b>",
        f"  🏃 RPS:       <b>~{data['rps']:.1f}</b> запросов/сек\n",
        f"<b>Время ответа:</b>",
        f"  ⚡ Минимум:  <b>{data['min_time']*1000:.0f} мс</b>",
        f"  📈 Среднее:  <b>{data['avg_time']*1000:.0f} мс</b>",
        f"  📊 P50:      <b>{data['p50']*1000:.0f} мс</b>",
        f"  📊 P95:      <b>{data['p95']*1000:.0f} мс</b>",
        f"  📊 P99:      <b>{data['p99']*1000:.0f} мс</b>",
        f"  🐢 Максимум: <b>{data['max_time']*1000:.0f} мс</b>",
    ]

    if data["status_counts"]:
        lines.append("\n<b>HTTP статусы:</b>")
        for code, cnt in sorted(data["status_counts"].items()):
            lines.append(f"  • {code}: {cnt}×")

    if data["error_types"]:
        lines.append("\n<b>Типы ошибок:</b>")
        for err, cnt in data["error_types"].items():
            lines.append(f"  • {err}: {cnt}×")

    lines.append(f"\n{health} — итоговая оценка нагрузки")
    return "\n".join(lines)
