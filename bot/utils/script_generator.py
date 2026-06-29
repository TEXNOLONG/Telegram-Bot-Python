import os
import textwrap

REPORT_ENDPOINT = os.getenv("REPLIT_DEV_DOMAIN", "localhost:5000")


def generate_lite_script(
    target_url: str,
    user_id: int,
    report_token: str,
    max_rps: int = 100,
    duration: int = 60,
) -> str:
    report_url = f"https://{REPORT_ENDPOINT}/api/report"
    script = textwrap.dedent(f"""\
        #!/usr/bin/env python3
        \"\"\"
        LITE Load Test Script
        Target : {target_url}
        Duration: {duration}s  |  Max RPS: {max_rps}
        Generated for user ID: {user_id}

        Run: python3 lite_test.py
        Requirements: pip install aiohttp
        \"\"\"
        import asyncio
        import time
        import random
        import statistics
        import json
        import urllib.request

        TARGET_URL  = "{target_url}"
        MAX_RPS     = {max_rps}
        DURATION    = {duration}
        USER_ID     = {user_id}
        REPORT_TOKEN = "{report_token}"
        REPORT_URL  = "{report_url}"

        USER_AGENTS = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
        ]

        results = []
        start_ts = None

        async def single_request(session):
            import aiohttp
            t0 = time.monotonic()
            try:
                headers = {{"User-Agent": random.choice(USER_AGENTS)}}
                async with session.get(TARGET_URL, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    elapsed = (time.monotonic() - t0) * 1000
                    results.append({{"ok": r.status < 400, "ms": elapsed, "status": r.status}})
            except Exception:
                elapsed = (time.monotonic() - t0) * 1000
                results.append({{"ok": False, "ms": elapsed, "status": 0}})

        async def run():
            import aiohttp
            global start_ts
            start_ts = time.monotonic()
            interval = 1.0 / MAX_RPS
            async with aiohttp.ClientSession() as session:
                tasks = []
                while time.monotonic() - start_ts < DURATION:
                    tasks.append(asyncio.create_task(single_request(session)))
                    await asyncio.sleep(interval)
                await asyncio.gather(*tasks)

        def send_report():
            if not results:
                return
            latencies = [r["ms"] for r in results]
            latencies.sort()
            ok = sum(1 for r in results if r["ok"])
            elapsed = time.monotonic() - start_ts
            p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
            p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0
            payload = {{
                "user_id": USER_ID,
                "report_token": REPORT_TOKEN,
                "target_url": TARGET_URL,
                "duration": round(elapsed, 1),
                "total_requests": len(results),
                "rps": round(len(results) / elapsed, 1) if elapsed else 0,
                "success_rate": round(ok / len(results) * 100, 1) if results else 0,
                "p95": round(p95, 1),
                "p99": round(p99, 1),
                "mode": "LITE",
            }}
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                REPORT_URL, data=data,
                headers={{"Content-Type": "application/json"}}, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = json.loads(resp.read())
                    print("\\n✅ Отчёт отправлен!")
                    print(f"🔗 Ссылка на отчёт: {{body.get('report_url', 'N/A')}}")
            except Exception as e:
                print(f"\\n⚠️  Не удалось отправить отчёт: {{e}}")

        if __name__ == "__main__":
            print(f"🚀 Запуск нагрузочного теста...")
            print(f"   Цель   : {{TARGET_URL}}")
            print(f"   Время  : {{DURATION}}с | Max RPS: {{MAX_RPS}}")
            try:
                asyncio.run(run())
            finally:
                send_report()
    """)
    return script
