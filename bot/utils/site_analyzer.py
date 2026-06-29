import aiohttp
import asyncio
import re
import ssl
import socket
import random
from html import escape
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import time


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def _browser_headers() -> dict:
    ua = random.choice(USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "DNT": "1",
    }


async def analyze_site(url: str) -> dict:
    result = {
        "url": url,
        "errors": [],
        "warnings": [],
        "info": [],
        "seo": [],
        "performance": {},
        "tech": [],
        "security_headers": {},
        "success": False,
        "score": 0,
    }

    last_error = None
    for attempt in range(2):
        headers = _browser_headers()
        try:
            start = time.monotonic()
            connector = aiohttp.TCPConnector(ssl=False, limit=10, force_close=False)
            async with aiohttp.ClientSession(
                headers=headers,
                connector=connector,
                cookie_jar=aiohttp.CookieJar(unsafe=True),
            ) as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=30, connect=10),
                    allow_redirects=True,
                    max_redirects=10,
                ) as resp:
                    elapsed = time.monotonic() - start
                    result["performance"]["response_time"] = round(elapsed * 1000, 0)
                    result["performance"]["status_code"] = resp.status
                    result["performance"]["final_url"] = str(resp.url)
                    result["performance"]["http_version"] = resp.version.major if resp.version else 1

                    encoding_hdr = resp.headers.get("Content-Encoding", "")
                    result["performance"]["compression"] = encoding_hdr if encoding_hdr else "none"

                    if resp.status == 403 and attempt == 0:
                        await asyncio.sleep(1)
                        last_error = f"HTTP 403 (попытка 1)"
                        continue

                    if resp.status >= 400:
                        result["errors"].append(f"🚫 HTTP ошибка: статус {resp.status}")
                        result["success"] = False
                        result["score"] = _calc_score(result)
                        return result

                    final_url = str(resp.url)
                    if final_url.rstrip("/") != url.rstrip("/"):
                        result["info"].append(f"↪️ Редирект: <code>{escape(final_url[:80])}</code>")

                    html = await resp.text(errors="replace")
                    soup = BeautifulSoup(html, "lxml")

                    _check_https(url, resp.headers, result)
                    _check_seo(soup, url, result)
                    _check_performance(soup, html, elapsed * 1000, resp.headers, result)
                    _check_security(resp.headers, result)
                    _check_accessibility(soup, result)
                    _check_links(soup, url, result)
                    _check_tech(soup, resp.headers, html, result)
                    _check_structured_data(soup, result)
                    _check_analytics(html, result)
                    _check_content_quality(soup, html, result)
                    _check_mobile(soup, result)
                    _check_social(soup, result)
                    await asyncio.gather(
                        _check_extras(session, url, resp.headers, result),
                        _check_vulnerabilities(session, url, html, resp.headers, result),
                    )

                score = _calc_score(result)
                result["score"] = score

            result["success"] = True
            return result

        except aiohttp.ClientConnectorError as e:
            last_error = f"🚫 Не удалось подключиться: {escape(str(e)[:120])}"
            if attempt == 0:
                await asyncio.sleep(1.5)
        except asyncio.TimeoutError:
            last_error = "⏱ Сайт не ответил за 30 секунд (слишком медленный или заблокировал запрос)"
            if attempt == 0:
                await asyncio.sleep(1)
        except aiohttp.TooManyRedirects:
            last_error = "🔄 Слишком много редиректов — возможна петля"
            break
        except Exception as e:
            last_error = f"❌ Ошибка анализа: {type(e).__name__}: {escape(str(e)[:120])}"
            break

    if last_error:
        result["errors"].append(last_error)
    if not result.get("score"):
        result["score"] = _calc_score(result)
    return result


def _check_https(url: str, headers, result: dict) -> None:
    if url.startswith("http://"):
        result["errors"].append("🔓 HTTPS отсутствует — данные передаются открыто")
    else:
        result["info"].append("🔒 HTTPS: защищённое соединение")

    hsts = headers.get("Strict-Transport-Security", "")
    if hsts:
        max_age = re.search(r"max-age=(\d+)", hsts)
        if max_age:
            days = int(max_age.group(1)) // 86400
            result["info"].append(f"🛡 HSTS активен (max-age: {days} дней)")
    else:
        if url.startswith("https://"):
            result["warnings"].append("⚠️ HSTS не установлен (браузеры могут использовать HTTP)")


def _check_seo(soup: BeautifulSoup, url: str, result: dict) -> None:
    title = soup.find("title")
    if not title or not title.get_text(strip=True):
        result["errors"].append("❌ SEO: тег &lt;title&gt; отсутствует")
    else:
        t = title.get_text(strip=True)
        tlen = len(t)
        if tlen < 10:
            result["warnings"].append(f"⚠️ SEO: title слишком короткий ({tlen} симв.)")
        elif tlen > 70:
            result["warnings"].append(f"⚠️ SEO: title слишком длинный ({tlen} симв., рекомендуется ≤70)")
        else:
            result["seo"].append(f"✅ Title ({tlen} симв.): «{escape(t[:60])}{'…' if tlen>60 else ''}»")

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if not meta_desc or not meta_desc.get("content", "").strip():
        result["warnings"].append("⚠️ SEO: meta description отсутствует")
    else:
        d = meta_desc["content"].strip()
        dlen = len(d)
        if dlen < 50:
            result["warnings"].append(f"⚠️ SEO: meta description слишком короткий ({dlen} симв.)")
        elif dlen > 160:
            result["warnings"].append(f"⚠️ SEO: meta description слишком длинный ({dlen} симв.)")
        else:
            result["seo"].append(f"✅ Meta description ({dlen} симв.): «{escape(d[:60])}…»")

    h1_tags = soup.find_all("h1")
    h2_tags = soup.find_all("h2")
    h3_tags = soup.find_all("h3")

    if not h1_tags:
        result["warnings"].append("⚠️ SEO: тег &lt;h1&gt; отсутствует")
    elif len(h1_tags) > 1:
        result["warnings"].append(f"⚠️ SEO: найдено {len(h1_tags)} тегов &lt;h1&gt; (рекомендуется один)")
    else:
        h1_text = h1_tags[0].get_text(strip=True)
        result["seo"].append(f"✅ H1: «{escape(h1_text[:50])}{'…' if len(h1_text)>50 else ''}»")

    result["info"].append(f"📐 Заголовки: H1×{len(h1_tags)}, H2×{len(h2_tags)}, H3×{len(h3_tags)}")

    canonical = soup.find("link", attrs={"rel": "canonical"})
    if not canonical:
        result["warnings"].append("⚠️ SEO: canonical-ссылка отсутствует")
    else:
        result["seo"].append(f"✅ Canonical: <code>{escape((canonical.get('href') or '')[:60])}</code>")

    og_title = soup.find("meta", attrs={"property": "og:title"})
    og_desc = soup.find("meta", attrs={"property": "og:description"})
    og_image = soup.find("meta", attrs={"property": "og:image"})
    og_url = soup.find("meta", attrs={"property": "og:url"})
    og_type = soup.find("meta", attrs={"property": "og:type"})
    og_count = sum(bool(x) for x in [og_title, og_desc, og_image, og_url, og_type])
    if og_count == 0:
        result["warnings"].append("⚠️ Open Graph теги отсутствуют (нет превью в соцсетях)")
    elif og_count < 3:
        result["warnings"].append(f"⚠️ Open Graph неполный ({og_count}/5 тегов)")
    else:
        result["seo"].append(f"✅ Open Graph: {og_count}/5 тегов")

    tw_card = soup.find("meta", attrs={"name": "twitter:card"})
    tw_title = soup.find("meta", attrs={"name": "twitter:title"})
    tw_img = soup.find("meta", attrs={"name": "twitter:image"})
    if not tw_card:
        result["warnings"].append("⚠️ Twitter Card отсутствует")
    else:
        tw_count = sum(bool(x) for x in [tw_card, tw_title, tw_img])
        result["seo"].append(f"✅ Twitter Card: {tw_count}/3 тегов")

    viewport = soup.find("meta", attrs={"name": "viewport"})
    if not viewport:
        result["errors"].append("❌ Meta viewport отсутствует (проблемы на мобильных)")
    else:
        result["seo"].append(f"✅ Viewport: <code>{escape(viewport.get('content','')[:60])}</code>")

    robots_meta = soup.find("meta", attrs={"name": "robots"})
    if robots_meta:
        content = robots_meta.get("content", "").lower()
        if "noindex" in content:
            result["errors"].append("❌ SEO: страница закрыта от индексации (noindex)!")
        elif "nofollow" in content:
            result["warnings"].append("⚠️ SEO: ссылки не передают вес (nofollow)")
        else:
            result["seo"].append(f"✅ Robots meta: <code>{escape(content[:40])}</code>")

    favicon = (
        soup.find("link", rel=lambda r: r and "icon" in r)
        or soup.find("link", attrs={"rel": "shortcut icon"})
    )
    if not favicon:
        result["warnings"].append("⚠️ Favicon (иконка) отсутствует")
    else:
        result["seo"].append("✅ Favicon присутствует")

    keywords = soup.find("meta", attrs={"name": "keywords"})
    if keywords and keywords.get("content", "").strip():
        result["info"].append(f"🔑 Keywords: <code>{escape(keywords['content'].strip()[:80])}</code>")

    lang_tag = soup.find("html", attrs={"lang": True})
    if lang_tag:
        result["info"].append(f"🌐 Язык страницы: <b>{escape(lang_tag.get('lang', ''))}</b>")
    else:
        result["warnings"].append("⚠️ Атрибут lang у &lt;html&gt; отсутствует")


def _check_performance(soup: BeautifulSoup, html: str, elapsed_ms: float, headers, result: dict) -> None:
    if elapsed_ms > 5000:
        result["errors"].append(f"🐢 Ответ: <b>{elapsed_ms:.0f}мс</b> — критически медленно (норма <1000мс)")
    elif elapsed_ms > 2000:
        result["warnings"].append(f"⚠️ Ответ: <b>{elapsed_ms:.0f}мс</b> — медленно (рекомендуется <1000мс)")
    elif elapsed_ms > 1000:
        result["info"].append(f"🟡 Ответ: <b>{elapsed_ms:.0f}мс</b> (допустимо, цель <1000мс)")
    else:
        result["info"].append(f"✅ Ответ: <b>{elapsed_ms:.0f}мс</b> — отлично")

    html_bytes = len(html.encode("utf-8"))
    html_kb = html_bytes / 1024
    if html_kb > 500:
        result["warnings"].append(f"⚠️ HTML: {html_kb:.0f} KB — слишком большой (рекомендуется <500 KB)")
    else:
        result["info"].append(f"📄 Размер HTML: <b>{html_kb:.1f} KB</b>")

    scripts = soup.find_all("script", src=True)
    async_scripts = [s for s in scripts if s.get("async")]
    defer_scripts = [s for s in scripts if s.get("defer")]
    blocking = [s for s in scripts if not s.get("async") and not s.get("defer")]
    inline_scripts = soup.find_all("script", src=False)

    if len(blocking) > 3:
        result["warnings"].append(
            f"⚠️ Скриптов без async/defer: <b>{len(blocking)}</b> из {len(scripts)} (блокируют рендеринг)"
        )
    result["info"].append(
        f"📜 Скрипты: <b>{len(scripts)}</b> внешних ({len(async_scripts)} async, {len(defer_scripts)} defer, {len(blocking)} блокирующих), <b>{len(inline_scripts)}</b> встроенных"
    )

    styles = soup.find_all("link", rel="stylesheet")
    inline_styles = soup.find_all("style")
    if len(styles) > 10:
        result["warnings"].append(f"⚠️ CSS файлов: <b>{len(styles)}</b> (рекомендуется объединять)")
    else:
        result["info"].append(f"🎨 CSS: <b>{len(styles)}</b> файлов + <b>{len(inline_styles)}</b> встроенных")

    images = soup.find_all("img")
    lazy_imgs = [i for i in images if i.get("loading") == "lazy"]
    no_alt = [i for i in images if not i.get("alt")]
    webp_imgs = [i for i in images if ".webp" in (i.get("src") or "")]
    result["info"].append(
        f"🖼 Изображения: <b>{len(images)}</b> шт. ({len(lazy_imgs)} lazy, {len(webp_imgs)} WebP, {len(no_alt)} без alt)"
    )

    iframes = soup.find_all("iframe")
    if iframes:
        result["info"].append(f"🖥 Iframe: <b>{len(iframes)}</b> (могут замедлять страницу)")

    preload = soup.find_all("link", rel="preload")
    prefetch = soup.find_all("link", rel="prefetch")
    preconnect = soup.find_all("link", rel="preconnect")
    if preload or prefetch or preconnect:
        result["info"].append(
            f"⚡ Resource hints: {len(preload)} preload, {len(prefetch)} prefetch, {len(preconnect)} preconnect"
        )
    else:
        result["warnings"].append("⚠️ Resource hints отсутствуют (preload/preconnect/prefetch)")

    ce = headers.get("Content-Encoding", "")
    if "gzip" in ce or "br" in ce or "deflate" in ce:
        result["info"].append(f"✅ Сжатие: <b>{ce}</b>")
    else:
        result["warnings"].append("⚠️ HTTP-сжатие не обнаружено (gzip/brotli не активны)")

    cache_ctrl = headers.get("Cache-Control", "")
    if cache_ctrl:
        result["info"].append(f"📦 Cache-Control: <code>{escape(cache_ctrl[:60])}</code>")
    else:
        result["warnings"].append("⚠️ Cache-Control заголовок отсутствует")

    etag = headers.get("ETag", "")
    last_mod = headers.get("Last-Modified", "")
    if etag or last_mod:
        cache_info = []
        if etag:
            cache_info.append(f"ETag: {escape(etag[:30])}")
        if last_mod:
            cache_info.append(f"Last-Modified: {last_mod[:30]}")
        result["info"].append("✅ Кэширование: " + " | ".join(cache_info))
    else:
        result["warnings"].append("⚠️ ETag/Last-Modified отсутствуют (браузерный кэш не настроен)")


def _check_security(headers, result: dict) -> None:
    sec = {
        "X-Content-Type-Options":     ("защита от MIME-sniffing", "nosniff"),
        "X-Frame-Options":             ("защита от clickjacking", None),
        "Content-Security-Policy":     ("политика безопасности (CSP)", None),
        "Referrer-Policy":             ("политика реферера", None),
        "Permissions-Policy":          ("политика разрешений", None),
        "X-XSS-Protection":            ("защита от XSS (старые браузеры)", None),
        "Cross-Origin-Opener-Policy":  ("COOP: изоляция вкладок", None),
        "Cross-Origin-Resource-Policy":("CORP: защита ресурсов", None),
    }
    present, missing = [], []
    for h, (desc, expected) in sec.items():
        val = headers.get(h, "")
        if val:
            present.append(h)
            if expected and val.lower() != expected.lower():
                result["warnings"].append(f"⚠️ {h}: значение <code>{escape(val[:40])}</code> (рекомендуется: {expected})")
        else:
            missing.append(f"{h} ({desc})")

    if missing:
        result["warnings"].append(
            f"⚠️ Отсутствуют заголовки безопасности ({len(missing)}/{len(sec)}):\n" +
            "\n".join(f"   • {m}" for m in missing)
        )
    else:
        result["info"].append(f"✅ Все {len(sec)} заголовков безопасности присутствуют")

    server = headers.get("Server", "")
    if server:
        if any(v in server.lower() for v in ["nginx/", "apache/", "iis/", "openresty/"]):
            result["warnings"].append(f"⚠️ Server раскрывает версию ПО: <code>{escape(server[:40])}</code>")
        else:
            result["info"].append(f"🖥 Server: <code>{escape(server[:40])}</code>")

    x_powered = headers.get("X-Powered-By", "")
    if x_powered:
        result["warnings"].append(f"⚠️ X-Powered-By раскрывает технологию: <code>{escape(x_powered[:40])}</code>")

    csp = headers.get("Content-Security-Policy", "")
    if csp:
        if "unsafe-inline" in csp:
            result["warnings"].append("⚠️ CSP содержит 'unsafe-inline' (XSS-уязвимость)")
        if "unsafe-eval" in csp:
            result["warnings"].append("⚠️ CSP содержит 'unsafe-eval' (XSS-уязвимость)")

    result["security_headers"] = {
        h: bool(headers.get(h)) for h in sec.keys()
    }


def _check_accessibility(soup: BeautifulSoup, result: dict) -> None:
    imgs = soup.find_all("img")
    no_alt = [i for i in imgs if not i.get("alt") and not i.get("role") == "presentation"]
    if no_alt:
        result["warnings"].append(f"⚠️ Доступность: <b>{len(no_alt)}</b> изображений без alt")
    elif imgs:
        result["info"].append(f"✅ Все {len(imgs)} изображений имеют alt")

    for inp in soup.find_all("input"):
        if inp.get("type") in ("text", "email", "password", "number", "tel"):
            inp_id = inp.get("id")
            has_label = bool(inp_id and soup.find("label", attrs={"for": inp_id}))
            has_aria = bool(inp.get("aria-label") or inp.get("aria-labelledby") or inp.get("placeholder"))
            if not has_label and not has_aria:
                result["warnings"].append("⚠️ Доступность: поля ввода без label или aria-label")
                break

    aria_roles = soup.find_all(attrs={"role": True})
    if not aria_roles:
        result["warnings"].append("⚠️ ARIA-роли отсутствуют (плохая доступность для скринридеров)")
    else:
        result["info"].append(f"✅ ARIA-атрибуты: <b>{len(aria_roles)}</b> элементов")

    tabindex = soup.find_all(attrs={"tabindex": True})
    if tabindex:
        result["info"].append(f"⌨️ Tabindex: <b>{len(tabindex)}</b> элементов с keyboard-навигацией")


def _check_links(soup: BeautifulSoup, base_url: str, result: dict) -> None:
    parsed = urlparse(base_url)
    base_domain = parsed.netloc

    all_links = soup.find_all("a", href=True)
    external, broken_text, nofollow, sponsored = [], [], [], []

    for a in all_links:
        href = a["href"].strip()
        rel = a.get("rel", [])
        if isinstance(rel, str):
            rel = [rel]
        text_val = a.get_text(strip=True)

        if not href or href == "#":
            broken_text.append(a)
        elif href.startswith("http"):
            link_domain = urlparse(href).netloc
            if link_domain and link_domain != base_domain:
                external.append(href)
                if "nofollow" in rel:
                    nofollow.append(href)
                if "sponsored" in rel:
                    sponsored.append(href)

    result["info"].append(
        f"🔗 Ссылки: <b>{len(all_links)}</b> всего, <b>{len(external)}</b> внешних"
        f" ({len(nofollow)} nofollow, {len(sponsored)} sponsored)"
    )
    if broken_text:
        result["warnings"].append(f"⚠️ Пустые/якорные ссылки: <b>{len(broken_text)}</b> шт.")


def _check_tech(soup: BeautifulSoup, headers, html: str, result: dict) -> None:
    detected = []
    html_lower = html.lower()

    gen = soup.find("meta", attrs={"name": "generator"})
    if gen and gen.get("content"):
        detected.append(f"⚙️ Generator: {escape(gen['content'][:40])}")

    frameworks = [
        ("wp-content" in html_lower or "wp-includes" in html_lower, "📦 WordPress"),
        ("bitrix" in html_lower or "1c-bitrix" in html_lower, "📦 1C-Bitrix"),
        ("__next" in html or "/_next/" in html, "⚛️ Next.js"),
        ("nuxt" in html_lower and "__nuxt" in html_lower, "💚 Nuxt.js"),
        ("react" in html_lower and "react-dom" in html_lower, "⚛️ React"),
        ("vue.js" in html_lower or "vue.min.js" in html_lower or "__vue__" in html, "💚 Vue.js"),
        ("angular" in html_lower and "ng-version" in html_lower, "🔴 Angular"),
        ("svelte" in html_lower, "🟠 Svelte"),
        ("jquery" in html_lower, "🔧 jQuery"),
        ("bootstrap" in html_lower, "🅱️ Bootstrap"),
        ("tailwind" in html_lower, "🌊 Tailwind CSS"),
        ("shopify" in html_lower, "🛒 Shopify"),
        ("tilda" in html_lower, "🔵 Tilda"),
        ("wix.com" in html_lower, "🎨 Wix"),
        ("joomla" in html_lower, "🟤 Joomla"),
        ("drupal" in html_lower, "💧 Drupal"),
        ("laravel" in html_lower, "🔴 Laravel"),
        ("django" in html_lower, "🐍 Django"),
        ("gatsby" in html_lower, "💜 Gatsby"),
        ("astro" in html_lower and "astro-" in html_lower, "🚀 Astro"),
    ]
    for cond, label in frameworks:
        if cond:
            detected.append(label)

    server = headers.get("Server", "")
    if server:
        s_lower = server.lower()
        if "nginx" in s_lower:
            detected.append("🖥 Nginx")
        elif "apache" in s_lower:
            detected.append("🖥 Apache")
        elif "cloudflare" in s_lower:
            detected.append("🌩 Cloudflare Server")
        elif "iis" in s_lower:
            detected.append("🪟 IIS (Windows Server)")
        elif "openresty" in s_lower:
            detected.append("🖥 OpenResty (Nginx+Lua)")

    x_powered = headers.get("X-Powered-By", "")
    if "php" in x_powered.lower():
        detected.append(f"🐘 PHP ({escape(x_powered[:30])})")
    elif "asp.net" in x_powered.lower():
        detected.append("🔷 ASP.NET")

    cdn_headers = {
        "CF-RAY": "🌩 Cloudflare CDN",
        "X-Served-By": "🚀 Fastly CDN",
        "X-Cache": "📡 Cache Layer",
        "X-Akamai-Transformed": "🌐 Akamai CDN",
        "X-CDN": "📡 CDN активен",
    }
    for h, label in cdn_headers.items():
        if headers.get(h):
            detected.append(label)
            break

    if detected:
        result["tech"] = detected


def _check_structured_data(soup: BeautifulSoup, result: dict) -> None:
    import json
    json_ld = soup.find_all("script", attrs={"type": "application/ld+json"})
    microdata = soup.find_all(attrs={"itemtype": True})
    rdfa = soup.find_all(attrs={"vocab": True})

    types_found = []
    for block in json_ld:
        try:
            data = json.loads(block.string or "")
            t = data.get("@type", "")
            if t:
                types_found.append(t)
        except Exception:
            pass

    if json_ld:
        type_str = ", ".join(types_found[:5]) if types_found else "неизвестный тип"
        result["seo"].append(f"✅ JSON-LD разметка: <b>{len(json_ld)}</b> блоков ({type_str})")
    elif microdata:
        result["seo"].append(f"✅ Microdata разметка: <b>{len(microdata)}</b> элементов")
    elif rdfa:
        result["seo"].append(f"✅ RDFa разметка: <b>{len(rdfa)}</b> элементов")
    else:
        result["warnings"].append("⚠️ Структурированные данные отсутствуют (Schema.org/JSON-LD)")


def _check_analytics(html: str, result: dict) -> None:
    analytics = []
    html_lower = html.lower()

    checks = [
        ("google-analytics.com" in html_lower or "gtag(" in html_lower, "📊 Google Analytics"),
        ("googletagmanager.com" in html_lower, "🏷 Google Tag Manager"),
        ("yandex.ru/metrika" in html_lower or "ym(" in html_lower, "📈 Яндекс.Метрика"),
        ("mc.yandex.ru" in html_lower, "📈 Яндекс.Метрика (alt)"),
        ("pixel" in html_lower and "facebook" in html_lower, "🎯 Facebook Pixel"),
        ("hotjar" in html_lower, "🔥 Hotjar"),
        ("amplitude" in html_lower, "📉 Amplitude"),
        ("mixpanel" in html_lower, "🧪 Mixpanel"),
        ("segment.com" in html_lower, "🔀 Segment"),
        ("clarity.ms" in html_lower, "💡 Microsoft Clarity"),
        ("plausible.io" in html_lower, "📊 Plausible"),
    ]
    for cond, label in checks:
        if cond:
            analytics.append(label)

    if analytics:
        result["info"].append("📊 Аналитика: " + ", ".join(analytics))
    else:
        result["warnings"].append("⚠️ Аналитика не обнаружена")


def _check_content_quality(soup: BeautifulSoup, html: str, result: dict) -> None:
    body = soup.find("body")
    if not body:
        return

    text = body.get_text(separator=" ", strip=True)
    words = text.split()
    word_count = len(words)

    if word_count < 100:
        result["warnings"].append(f"⚠️ Контент: <b>{word_count}</b> слов — маловато (рекомендуется 300+)")
    elif word_count > 3000:
        result["info"].append(f"📝 Контент: <b>{word_count}</b> слов — объёмная страница")
    else:
        result["info"].append(f"📝 Контент: <b>{word_count}</b> слов")

    forms = soup.find_all("form")
    if forms:
        https_forms = []
        for form in forms:
            action = form.get("action", "")
            if action.startswith("https://") or not action or action.startswith("/"):
                https_forms.append(form)
        result["info"].append(f"📋 Форм: <b>{len(forms)}</b> шт.")
        if len(forms) - len(https_forms) > 0:
            result["warnings"].append(f"⚠️ Форм без HTTPS action: <b>{len(forms) - len(https_forms)}</b>")


def _check_mobile(soup: BeautifulSoup, result: dict) -> None:
    viewport = soup.find("meta", attrs={"name": "viewport"})
    if viewport:
        content = viewport.get("content", "")
        if "width=device-width" in content:
            result["info"].append("📱 Responsive: viewport настроен корректно")
        else:
            result["warnings"].append(f"⚠️ Viewport: нестандартный ({escape(content[:50])})")

    apple_icon = soup.find("link", rel=lambda r: r and "apple-touch-icon" in (r if isinstance(r, list) else [r]))
    if apple_icon:
        result["info"].append("🍎 Apple Touch Icon присутствует")

    manifest = soup.find("link", rel=lambda r: r and "manifest" in (r if isinstance(r, list) else [r]))
    if manifest:
        result["info"].append("📦 Web App Manifest: PWA поддержка")


def _check_social(soup: BeautifulSoup, result: dict) -> None:
    og_image = soup.find("meta", attrs={"property": "og:image"})
    tw_image = soup.find("meta", attrs={"name": "twitter:image"})

    if og_image or tw_image:
        imgs = []
        if og_image:
            imgs.append(f"OG: {escape((og_image.get('content') or '')[:50])}")
        if tw_image:
            imgs.append(f"TW: {escape((tw_image.get('content') or '')[:50])}")
        result["seo"].append("✅ Превью соцсетей: " + " | ".join(imgs))


async def _check_extras(session: aiohttp.ClientSession, url: str, headers, result: dict) -> None:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    checks = [
        (f"{base}/robots.txt", "robots.txt"),
        (f"{base}/sitemap.xml", "sitemap.xml"),
        (f"{base}/security.txt", "security.txt"),
        (f"{base}/.well-known/security.txt", "security.txt (.well-known)"),
    ]

    for check_url, label in checks:
        try:
            async with session.get(
                check_url,
                timeout=aiohttp.ClientTimeout(total=8),
                allow_redirects=True,
                ssl=False,
            ) as r:
                if r.status == 200:
                    content = await r.text(errors="replace")
                    if label.startswith("robots"):
                        result["seo"].append(f"✅ robots.txt найден ({len(content)} байт)")
                        if "Disallow: /" in content and len(content) < 50:
                            result["warnings"].append("⚠️ robots.txt запрещает индексацию всего сайта!")
                    elif label.startswith("sitemap"):
                        result["seo"].append(f"✅ sitemap.xml найден ({len(content)} байт)")
                    elif "security" in label:
                        result["info"].append(f"✅ {label} найден")
                else:
                    if label.startswith("robots"):
                        result["warnings"].append("⚠️ robots.txt не найден")
                    elif label.startswith("sitemap"):
                        result["warnings"].append("⚠️ sitemap.xml не найден")
        except Exception:
            pass


async def _check_vulnerabilities(session: aiohttp.ClientSession, url: str, html: str, headers, result: dict) -> None:
    vulns = []
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    if "unsafe-inline" in headers.get("Content-Security-Policy", "") or not headers.get("Content-Security-Policy"):
        if "<script" in html.lower() and ("document.write" in html or "eval(" in html):
            vulns.append(("HIGH", "Потенциальная XSS уязвимость: обнаружен небезопасный JS (document.write/eval)"))

    server = headers.get("Server", "")
    if re.search(r"nginx/1\.[0-9]\.", server) or re.search(r"Apache/2\.[0-3]\.", server):
        vulns.append(("MEDIUM", f"Устаревшая версия сервера: {escape(server[:60])}"))

    if not headers.get("X-Frame-Options") and not "frame-ancestors" in headers.get("Content-Security-Policy", ""):
        vulns.append(("MEDIUM", "Clickjacking: X-Frame-Options и frame-ancestors CSP отсутствуют"))

    if not headers.get("X-Content-Type-Options"):
        vulns.append(("LOW", "MIME-sniffing: заголовок X-Content-Type-Options отсутствует"))

    if url.startswith("http://") and not headers.get("Strict-Transport-Security"):
        vulns.append(("HIGH", "Нет принудительного HTTPS (HSTS отсутствует, соединение не защищено)"))

    sensitive_paths = [
        ("/wp-login.php", "WordPress"), ("/.env", ".env файл"), ("/admin", "Admin панель"),
        ("/phpmyadmin", "phpMyAdmin"), ("/.git/config", "Git config"),
        ("/backup.zip", "Backup файл"), ("/config.php", "Config файл"),
    ]
    for path, label in sensitive_paths:
        try:
            async with session.get(
                f"{base}{path}",
                timeout=aiohttp.ClientTimeout(total=5),
                allow_redirects=False,
                ssl=False,
                headers={"User-Agent": random.choice(USER_AGENTS)},
            ) as r:
                if r.status in (200, 301, 302, 403):
                    if r.status == 200:
                        vulns.append(("HIGH", f"Открытый доступ: {label} ({path}) — HTTP {r.status}"))
                    elif r.status == 403:
                        vulns.append(("LOW", f"Обнаружен, но закрыт: {label} ({path}) — HTTP 403"))
        except Exception:
            pass

    if "sql" in html.lower() and ("error" in html.lower() or "syntax" in html.lower()):
        if re.search(r"(sql syntax|mysql_fetch|ORA-[0-9]+|pg_query)", html, re.I):
            vulns.append(("CRITICAL", "Утечка SQL ошибок в HTML — возможна SQL-инъекция"))

    if "phpinfo()" in html or "PHP Version" in html and "System" in html:
        vulns.append(("HIGH", "phpinfo() открыт публично — критическая утечка конфигурации"))

    if "stack trace" in html.lower() or "traceback" in html.lower():
        vulns.append(("MEDIUM", "Стек-трейс виден в HTML — утечка внутренней структуры"))

    result["vulnerabilities"] = vulns


def _check_content_quality(soup: BeautifulSoup, html: str, result: dict) -> None:
    body = soup.find("body")
    if not body:
        return
    text = body.get_text(separator=" ", strip=True)
    words = text.split()
    word_count = len(words)
    if word_count < 100:
        result["warnings"].append(f"⚠️ Контент: <b>{word_count}</b> слов — маловато (рекомендуется 300+)")
    elif word_count > 3000:
        result["info"].append(f"📝 Контент: <b>{word_count}</b> слов — объёмная страница")
    else:
        result["info"].append(f"📝 Контент: <b>{word_count}</b> слов")
    forms = soup.find_all("form")
    if forms:
        result["info"].append(f"📋 Форм: <b>{len(forms)}</b> шт.")


def _calc_score(result: dict) -> int:
    score = 100
    score -= len(result.get("errors", [])) * 10
    score -= len(result.get("warnings", [])) * 3
    for sev, _ in result.get("vulnerabilities", []):
        if sev == "CRITICAL":
            score -= 20
        elif sev == "HIGH":
            score -= 10
        elif sev == "MEDIUM":
            score -= 5
        elif sev == "LOW":
            score -= 2
    score += len(result.get("seo", [])) * 1
    return max(0, min(100, score))


def format_report(result: dict) -> str:
    lines = []
    url = result.get("url", "")
    score = result.get("score", 0)
    perf = result.get("performance", {})

    emoji = "🟢" if score >= 70 else ("🟡" if score >= 45 else "🔴")
    lines.append(f"<b>Анализ сайта</b> {emoji}\n")
    lines.append(f"🌐 <code>{escape(url)}</code>")
    lines.append(f"⭐ Оценка: <b>{score}/100</b>")

    if perf.get("response_time"):
        rt = int(perf["response_time"])
        lines.append(f"⚡ Ответ: <b>{rt} мс</b>")
    if perf.get("status_code"):
        lines.append(f"📡 HTTP статус: <b>{perf['status_code']}</b>")
    if perf.get("http_version"):
        lines.append(f"🔌 Протокол: HTTP/<b>{perf['http_version']}</b>")
    if perf.get("compression") and perf["compression"] != "none":
        lines.append(f"🗜 Сжатие: <b>{perf['compression']}</b>")

    vulns = result.get("vulnerabilities", [])
    if vulns:
        lines.append(f"\n🔓 <b>Уязвимости ({len(vulns)}):</b>")
        for sev, msg in vulns[:4]:
            sev_emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(sev, "⚪")
            lines.append(f"  {sev_emoji} [{sev}] {msg[:80]}")
        if len(vulns) > 4:
            lines.append(f"  <i>...и ещё {len(vulns) - 4}</i>")

    errors = result.get("errors", [])
    if errors:
        lines.append(f"\n❌ <b>Ошибки ({len(errors)}):</b>")
        for e in errors[:3]:
            lines.append(f"  • {e[:80]}")

    warnings = result.get("warnings", [])
    if warnings:
        lines.append(f"\n⚠️ <b>Предупреждения ({len(warnings)}):</b>")
        for w in warnings[:4]:
            lines.append(f"  • {w[:80]}")

    tech = result.get("tech", [])
    if tech:
        lines.append(f"\n🛠 <b>Технологии:</b> {' | '.join(tech[:5])}")

    seo = result.get("seo", [])
    if seo:
        lines.append(f"\n🔍 <b>SEO ({len(seo)} OK):</b>")
        for s in seo[:3]:
            lines.append(f"  {s[:80]}")

    return "\n".join(lines)
