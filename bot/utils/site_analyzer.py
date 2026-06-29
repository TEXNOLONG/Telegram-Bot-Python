import aiohttp
import asyncio
import re
import ssl
import socket
from html import escape
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import time


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


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

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
    }

    try:
        start = time.monotonic()
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=20),
                allow_redirects=True,
            ) as resp:
                elapsed = time.monotonic() - start
                result["performance"]["response_time"] = round(elapsed * 1000, 0)
                result["performance"]["status_code"] = resp.status
                result["performance"]["final_url"] = str(resp.url)
                result["performance"]["http_version"] = resp.version.major if resp.version else 1

                encoding_hdr = resp.headers.get("Content-Encoding", "")
                result["performance"]["compression"] = encoding_hdr if encoding_hdr else "none"

                if resp.status >= 400:
                    result["errors"].append(f"🚫 HTTP ошибка: статус {resp.status}")
                    return result

                final_url = str(resp.url)
                if final_url.rstrip("/") != url.rstrip("/"):
                    result["info"].append(f"↪️ Редирект: <code>{escape(final_url[:80])}</code>")

                content_type = resp.headers.get("Content-Type", "")
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
    except aiohttp.ClientConnectorError as e:
        result["errors"].append(f"🚫 Не удалось подключиться: {escape(str(e)[:100])}")
    except asyncio.TimeoutError:
        result["errors"].append("⏱ Сайт не ответил за 20 секунд (слишком медленный)")
    except Exception as e:
        result["errors"].append(f"❌ Ошибка анализа: {type(e).__name__}: {escape(str(e)[:100])}")

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
    ]
    for cond, label in frameworks:
        if cond:
            detected.append(label)

    server = headers.get("Server", "")
    if server:
        s_lower = server.lower()
        if "nginx" in s_lower:
            detected.append(f"🖥 Nginx")
        elif "apache" in s_lower:
            detected.append(f"🖥 Apache")
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
        ("googletagmanager.com" in html_lower, "📊 Google Tag Manager"),
        ("metrika.yandex" in html_lower or "mc.yandex" in html_lower, "📊 Яндекс.Метрика"),
        ("connect.facebook.net" in html_lower or "fbq(" in html_lower, "📘 Facebook Pixel"),
        ("vk.com/js/api" in html_lower, "🔵 VK Pixel"),
        ("hotjar.com" in html_lower, "🟠 Hotjar"),
        ("clarity.ms" in html_lower, "💙 Microsoft Clarity"),
        ("mixpanel.com" in html_lower, "📈 Mixpanel"),
        ("segment.com" in html_lower or "segment.io" in html_lower, "📡 Segment"),
        ("amplitude.com" in html_lower, "📊 Amplitude"),
    ]
    for cond, label in checks:
        if cond:
            analytics.append(label)

    if analytics:
        result["info"].append("📡 Аналитика: " + ", ".join(analytics))
    else:
        result["warnings"].append("⚠️ Системы веб-аналитики не обнаружены")


def _check_content_quality(soup: BeautifulSoup, html: str, result: dict) -> None:
    text = soup.get_text(separator=" ", strip=True)
    words = len(text.split())
    chars = len(text)

    if words < 100:
        result["warnings"].append(f"⚠️ Мало контента: <b>{words}</b> слов (рекомендуется >300 для SEO)")
    elif words < 300:
        result["info"].append(f"📝 Контент: <b>{words}</b> слов (рекомендуется >300 для SEO)")
    else:
        result["info"].append(f"✅ Контент: <b>{words}</b> слов ({chars} символов)")

    paragraphs = soup.find_all("p")
    result["info"].append(f"📄 Абзацев: <b>{len(paragraphs)}</b>")

    forms = soup.find_all("form")
    if forms:
        for form in forms:
            action = form.get("action", "")
            method = form.get("method", "get").upper()
            result["info"].append(f"📋 Форма: method={method}, action={escape(action[:40] or '(none)')}")

    has_mixed = re.search(r'src=["\']http://', html, re.IGNORECASE)
    if has_mixed and html.startswith("<!DOCTYPE html"):
        result["warnings"].append("⚠️ Смешанный контент (Mixed Content): HTTP-ресурсы на HTTPS-странице")


def _check_mobile(soup: BeautifulSoup, result: dict) -> None:
    viewport = soup.find("meta", attrs={"name": "viewport"})
    if viewport:
        content = viewport.get("content", "")
        if "width=device-width" in content:
            result["info"].append("✅ Адаптивный: viewport корректно настроен")
        else:
            result["warnings"].append("⚠️ Viewport задан, но без width=device-width")

    media_queries = sum(1 for s in soup.find_all("style") if "@media" in (s.string or ""))
    link_styles = soup.find_all("link", rel="stylesheet")
    if media_queries > 0 or len(link_styles) > 0:
        result["info"].append(f"📱 Media queries в <style>: <b>{media_queries}</b> блоков")

    touch_icon = soup.find("link", rel=lambda r: r and "apple-touch-icon" in r)
    manifest = soup.find("link", rel="manifest")
    pwa_parts = []
    if touch_icon:
        pwa_parts.append("apple-touch-icon")
    if manifest:
        pwa_parts.append("web manifest")
    if pwa_parts:
        result["info"].append(f"📱 PWA-элементы: {', '.join(pwa_parts)}")


def _check_social(soup: BeautifulSoup, result: dict) -> None:
    socials = {
        "vk.com": "ВКонтакте",
        "t.me": "Telegram",
        "youtube.com": "YouTube",
        "instagram.com": "Instagram",
        "facebook.com": "Facebook",
        "twitter.com": "Twitter/X",
        "linkedin.com": "LinkedIn",
        "tiktok.com": "TikTok",
        "ok.ru": "Одноклассники",
    }
    found_socials = []
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        for domain, name in socials.items():
            if domain in href and name not in found_socials:
                found_socials.append(name)

    if found_socials:
        result["info"].append("📣 Соцсети на странице: " + ", ".join(found_socials))


async def _check_vulnerabilities(session: aiohttp.ClientSession, url: str, html: str, headers, result: dict) -> None:
    """Active vulnerability scan: exposed files, misconfigs, dangerous methods, CORS, etc."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    vulns = []

    # 1. Sensitive file exposure
    sensitive_paths = [
        ("/.git/HEAD",         "Открыт .git — исходный код может быть скачан"),
        ("/.env",              "Открыт .env — утечка переменных окружения / паролей"),
        ("/.htaccess",         "Открыт .htaccess — конфигурация сервера видна"),
        ("/config.php",        "Открыт config.php — возможна утечка данных БД"),
        ("/wp-config.php",     "Открыт wp-config.php — критическая утечка WordPress"),
        ("/phpinfo.php",       "phpinfo() доступен публично — раскрыта конфигурация PHP"),
        ("/server-status",     "Apache server-status открыт — утечка запросов"),
        ("/adminer.php",       "Adminer БД-менеджер доступен публично"),
        ("/phpmyadmin/",       "phpMyAdmin открыт публично"),
        ("/.DS_Store",         "Открыт .DS_Store — структура директорий раскрыта"),
        ("/backup.sql",        "Открыт backup.sql — дамп базы данных"),
        ("/dump.sql",          "Открыт dump.sql — дамп базы данных"),
        ("/composer.json",     "Открыт composer.json — зависимости проекта раскрыты"),
        ("/package.json",      "Открыт package.json — зависимости проекта раскрыты"),
        ("/.well-known/security.txt", None),
    ]

    tasks = [_probe_path(session, base, path, label) for path, label in sensitive_paths]
    probe_results = await asyncio.gather(*tasks, return_exceptions=True)
    for item in probe_results:
        if isinstance(item, str):
            vulns.append(("CRITICAL", item))

    # 2. Admin panel detection
    admin_paths = [
        "/admin", "/admin/", "/administrator", "/wp-admin/",
        "/panel", "/dashboard", "/cpanel", "/manage",
    ]
    admin_tasks = [_probe_admin(session, base, p) for p in admin_paths]
    admin_results = await asyncio.gather(*admin_tasks, return_exceptions=True)
    found_admins = [r for r in admin_results if isinstance(r, str)]
    if found_admins:
        vulns.append(("HIGH", f"Панели управления открыты: {', '.join(found_admins[:3])}"))

    # 3. Dangerous HTTP methods
    try:
        async with session.options(
            url,
            timeout=aiohttp.ClientTimeout(total=5),
            ssl=False,
        ) as resp:
            allow = resp.headers.get("Allow", "") or resp.headers.get("Access-Control-Allow-Methods", "")
            dangerous = [m for m in ["PUT", "DELETE", "TRACE", "CONNECT", "PATCH"] if m in allow]
            if dangerous:
                vulns.append(("HIGH", f"Опасные HTTP-методы разрешены: {', '.join(dangerous)}"))
    except Exception:
        pass

    # 4. CORS misconfiguration
    try:
        async with session.get(
            url,
            headers={"Origin": "https://evil.com"},
            timeout=aiohttp.ClientTimeout(total=5),
            ssl=False,
        ) as resp:
            acao = resp.headers.get("Access-Control-Allow-Origin", "")
            acac = resp.headers.get("Access-Control-Allow-Credentials", "")
            if acao == "*":
                vulns.append(("MEDIUM", "CORS: Access-Control-Allow-Origin: * — любой сайт может читать ответы"))
            elif acao == "https://evil.com":
                sev = "CRITICAL" if acac.lower() == "true" else "HIGH"
                vulns.append((sev, f"CORS уязвим: отражает Origin запроса{'+ credentials' if acac else ''}"))
    except Exception:
        pass

    # 5. Clickjacking
    xfo = headers.get("X-Frame-Options", "")
    csp = headers.get("Content-Security-Policy", "")
    has_frame_guard = xfo or ("frame-ancestors" in csp.lower())
    if not has_frame_guard:
        vulns.append(("MEDIUM", "Clickjacking: X-Frame-Options отсутствует — страницу можно встроить в iframe"))

    # 6. Mixed content
    if url.startswith("https://"):
        html_lower = html.lower()
        if re.search(r'src=["\']http://', html_lower) or re.search(r'href=["\']http://', html_lower):
            vulns.append(("MEDIUM", "Mixed Content: HTTP ресурсы на HTTPS странице"))

    # 7. Server version disclosure
    server = headers.get("Server", "")
    if re.search(r"/([\d.]+)", server):
        vulns.append(("LOW", f"Версия сервера раскрыта в заголовке Server: {escape(server[:40])}"))

    x_powered = headers.get("X-Powered-By", "")
    if x_powered:
        vulns.append(("LOW", f"Технология раскрыта в X-Powered-By: {escape(x_powered[:40])}"))

    # 8. Missing security headers (as vulns)
    if not headers.get("Content-Security-Policy"):
        vulns.append(("MEDIUM", "Отсутствует Content-Security-Policy — возможен XSS"))
    if not headers.get("X-Content-Type-Options"):
        vulns.append(("LOW", "Отсутствует X-Content-Type-Options — MIME-sniffing уязвимость"))

    # 9. Cookie flags
    set_cookie = headers.get("Set-Cookie", "")
    if set_cookie:
        if "httponly" not in set_cookie.lower():
            vulns.append(("MEDIUM", "Cookie без HttpOnly — доступны через JavaScript (XSS риск)"))
        if "secure" not in set_cookie.lower() and url.startswith("https://"):
            vulns.append(("LOW", "Cookie без Secure флага — могут передаваться по HTTP"))
        if "samesite" not in set_cookie.lower():
            vulns.append(("LOW", "Cookie без SameSite — возможна CSRF атака"))

    result["vulnerabilities"] = vulns


async def _probe_path(session, base: str, path: str, label) -> str | None:
    if label is None:
        return None
    try:
        async with session.get(
            base + path,
            timeout=aiohttp.ClientTimeout(total=5),
            allow_redirects=False,
            ssl=False,
        ) as resp:
            if resp.status == 200:
                body = await resp.read()
                if len(body) > 10:
                    return label
    except Exception:
        pass
    return None


async def _probe_admin(session, base: str, path: str) -> str | None:
    try:
        async with session.get(
            base + path,
            timeout=aiohttp.ClientTimeout(total=4),
            allow_redirects=True,
            ssl=False,
        ) as resp:
            if resp.status in (200, 401, 403):
                return path
    except Exception:
        pass
    return None


async def _check_extras(session: aiohttp.ClientSession, url: str, headers, result: dict) -> None:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    host = parsed.netloc.split(":")[0]

    checks = [
        (f"{base}/robots.txt", "🤖 robots.txt"),
        (f"{base}/sitemap.xml", "🗺 sitemap.xml"),
        (f"{base}/sitemap_index.xml", "🗺 sitemap_index.xml"),
        (f"{base}/.well-known/security.txt", "🔒 security.txt"),
        (f"{base}/favicon.ico", "🔖 favicon.ico"),
    ]

    tasks = []
    for check_url, label in checks:
        tasks.append(_check_url_exists(session, check_url, label))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for item in results:
        if isinstance(item, tuple):
            ok, msg = item
            if ok:
                result["seo"].append(msg)
            else:
                result["warnings"].append(msg)

    try:
        ctx = ssl.create_default_context()
        conn = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                lambda: ssl.SSLContext.wrap_socket(ctx, socket.create_connection((host, 443), timeout=5), server_hostname=host)
            ),
            timeout=6
        )
        cert = conn.getpeercert()
        conn.close()
        if cert:
            import datetime
            expires_str = cert.get("notAfter", "")
            if expires_str:
                expires = datetime.datetime.strptime(expires_str, "%b %d %H:%M:%S %Y %Z")
                days_left = (expires - datetime.datetime.utcnow()).days
                if days_left < 14:
                    result["errors"].append(f"❌ SSL сертификат истекает через <b>{days_left}</b> дней!")
                elif days_left < 30:
                    result["warnings"].append(f"⚠️ SSL сертификат истекает через <b>{days_left}</b> дней")
                else:
                    result["info"].append(f"🔐 SSL сертификат действителен ещё <b>{days_left}</b> дней")

            issuer = dict(x[0] for x in cert.get("issuer", []))
            org = issuer.get("organizationName", "")
            if org:
                result["info"].append(f"🏢 Выдан: <b>{escape(org[:50])}</b>")
    except Exception:
        pass


async def _check_url_exists(session, url: str, label: str) -> tuple[bool, str]:
    try:
        async with session.get(
            url,
            timeout=aiohttp.ClientTimeout(total=6),
            allow_redirects=True,
        ) as r:
            if r.status == 200:
                size = len(await r.read())
                return True, f"✅ {label} найден ({size} байт)"
            else:
                return False, f"⚠️ {label} вернул статус {r.status}"
    except Exception:
        return False, f"⚠️ {label} недоступен"


def _calc_score(data: dict) -> int:
    score = 100
    score -= len(data.get("errors", [])) * 12
    score -= len(data.get("warnings", [])) * 4

    perf = data.get("performance", {})
    rt = perf.get("response_time", 0)
    if rt > 5000:
        score -= 15
    elif rt > 2000:
        score -= 8
    elif rt > 1000:
        score -= 3

    seo_count = len(data.get("seo", []))
    if seo_count >= 8:
        score += 5
    elif seo_count >= 5:
        score += 2

    return max(0, min(100, score))


def format_report(data: dict) -> str:
    score = data.get("score", 0)
    perf = data.get("performance", {})
    vulns = data.get("vulnerabilities", [])
    crit_vulns = [v for s, v in vulns if s == "CRITICAL"]
    high_vulns = [v for s, v in vulns if s == "HIGH"]

    lines = [
        f"<b>Анализ сайта</b>",
        f"<code>{escape(data['url'])}</code>",
        "",
        f"<b>Оценка: {score}/100</b>  —  {len(data.get('errors',[]))} ошибок, {len(data.get('warnings',[]))} предупреждений",
    ]

    if perf.get("status_code"):
        lines.append(f"HTTP: <b>{perf['status_code']}</b>  |  {perf.get('response_time', 0)} мс")

    if vulns:
        lines.append(f"\n<b>Уязвимости ({len(vulns)}):</b>")
        for sev, msg in vulns[:8]:
            tag = {"CRITICAL": "[КРИТ]", "HIGH": "[ВЫСОК]", "MEDIUM": "[СРЕДН]", "LOW": "[НЗК]"}.get(sev, "")
            lines.append(f"  {tag} {msg}")
        if len(vulns) > 8:
            lines.append(f"  ...ещё {len(vulns)-8} — в полном отчёте")

    if data.get("errors"):
        lines.append("\n<b>Ошибки:</b>")
        lines.extend(f"  {e}" for e in data["errors"][:5])

    if data.get("warnings"):
        lines.append("\n<b>Предупреждения:</b>")
        lines.extend(f"  {w}" for w in data["warnings"][:6])

    if data.get("seo"):
        lines.append("\n<b>SEO:</b>")
        lines.extend(f"  {s}" for s in data["seo"][:5])

    if data.get("tech"):
        lines.append("\n<b>Стек:</b>")
        lines.extend(f"  {t}" for t in data["tech"][:5])

    return "\n".join(lines)
