import aiohttp
import asyncio
import re
from html import escape
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import time


async def analyze_site(url: str) -> dict:
    result = {
        "url": url,
        "errors": [],
        "warnings": [],
        "info": [],
        "seo": [],
        "performance": {},
        "tech": [],
        "success": False,
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    try:
        start = time.monotonic()
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True) as resp:
                elapsed = time.monotonic() - start
                result["performance"]["response_time"] = round(elapsed, 2)
                result["performance"]["status_code"] = resp.status
                result["performance"]["final_url"] = str(resp.url)

                if resp.status >= 400:
                    result["errors"].append(f"🚫 HTTP ошибка: статус {resp.status}")
                    return result

                if str(resp.url) != url and not url.rstrip("/").endswith(urlparse(str(resp.url)).path.rstrip("/")):
                    result["info"].append(f"↪️ Редирект → {escape(str(resp.url))}")

                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    result["warnings"].append(f"⚠️ Тип контента: {escape(content_type)} (не HTML)")

                html = await resp.text(errors="replace")
                soup = BeautifulSoup(html, "lxml")

                _check_https(url, result)
                _check_seo(soup, url, result)
                _check_performance(soup, html, elapsed, result)
                _check_security(resp.headers, result)
                _check_accessibility(soup, result)
                _check_links(soup, url, result)
                _check_tech(soup, resp.headers, html, result)
                _check_structured_data(soup, result)
                _check_analytics(html, result)
                await _check_extras(session, url, result)

        result["success"] = True
    except aiohttp.ClientConnectorError:
        result["errors"].append("🚫 Не удалось подключиться к сайту. Проверь URL.")
    except asyncio.TimeoutError:
        result["errors"].append("⏱ Сайт не ответил за 15 секунд (слишком медленный).")
    except Exception as e:
        result["errors"].append(f"❌ Ошибка при анализе: {type(e).__name__}: {escape(str(e))}")

    return result


def _check_https(url: str, result: dict) -> None:
    if url.startswith("http://"):
        result["errors"].append("🔓 Сайт не использует HTTPS — данные передаются незащищённо!")
    else:
        result["info"].append("🔒 HTTPS: защищённое соединение")


def _check_seo(soup: BeautifulSoup, url: str, result: dict) -> None:
    title = soup.find("title")
    if not title or not title.get_text(strip=True):
        result["errors"].append("❌ SEO: отсутствует тег &lt;title&gt;")
    else:
        t = escape(title.get_text(strip=True))
        tlen = len(title.get_text(strip=True))
        if tlen < 10:
            result["warnings"].append(f"⚠️ SEO: title слишком короткий ({tlen} симв.)")
        elif tlen > 70:
            result["warnings"].append(f"⚠️ SEO: title слишком длинный ({tlen} симв., рекомендуется ≤70)")
        else:
            result["seo"].append(f"✅ Title ({tlen} симв.): «{t[:55]}{'…' if tlen > 55 else ''}»")

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if not meta_desc or not meta_desc.get("content", "").strip():
        result["warnings"].append("⚠️ SEO: отсутствует meta description")
    else:
        desc_raw = meta_desc["content"].strip()
        dlen = len(desc_raw)
        if dlen < 50:
            result["warnings"].append(f"⚠️ SEO: meta description слишком короткий ({dlen} симв.)")
        elif dlen > 160:
            result["warnings"].append(f"⚠️ SEO: meta description слишком длинный ({dlen} симв., рекомендуется ≤160)")
        else:
            result["seo"].append(f"✅ Meta description ({dlen} симв.)")

    h1_tags = soup.find_all("h1")
    if not h1_tags:
        result["warnings"].append("⚠️ SEO: нет тега &lt;h1&gt;")
    elif len(h1_tags) > 1:
        result["warnings"].append(f"⚠️ SEO: найдено {len(h1_tags)} тегов &lt;h1&gt; (рекомендуется 1)")
    else:
        h1_text = escape(h1_tags[0].get_text(strip=True))
        result["seo"].append(f"✅ H1: «{h1_text[:50]}{'…' if len(h1_text) > 50 else ''}»")

    canonical = soup.find("link", attrs={"rel": "canonical"})
    if not canonical:
        result["warnings"].append("⚠️ SEO: отсутствует canonical ссылка")
    else:
        result["seo"].append("✅ Canonical ссылка есть")

    og_title = soup.find("meta", attrs={"property": "og:title"})
    og_desc = soup.find("meta", attrs={"property": "og:description"})
    og_image = soup.find("meta", attrs={"property": "og:image"})
    og_count = sum([bool(og_title), bool(og_desc), bool(og_image)])
    if og_count == 0:
        result["warnings"].append("⚠️ SEO: отсутствуют Open Graph теги")
    elif og_count < 3:
        result["warnings"].append(f"⚠️ SEO: Open Graph неполный ({og_count}/3 тегов)")
    else:
        result["seo"].append("✅ Open Graph теги (og:title, og:description, og:image)")

    tw_card = soup.find("meta", attrs={"name": "twitter:card"})
    if not tw_card:
        result["warnings"].append("⚠️ SEO: нет Twitter Card тегов")
    else:
        result["seo"].append("✅ Twitter Card присутствует")

    viewport = soup.find("meta", attrs={"name": "viewport"})
    if not viewport:
        result["errors"].append("❌ Адаптивность: нет meta viewport (плохо на мобильных)")
    else:
        result["seo"].append("✅ Meta viewport есть")

    robots_meta = soup.find("meta", attrs={"name": "robots"})
    if robots_meta and "noindex" in robots_meta.get("content", "").lower():
        result["warnings"].append("⚠️ SEO: страница закрыта от индексации (noindex)")

    favicon = (
        soup.find("link", rel=lambda r: r and "icon" in r)
        or soup.find("link", attrs={"rel": "shortcut icon"})
    )
    if not favicon:
        result["warnings"].append("⚠️ Нет favicon (иконки сайта)")
    else:
        result["seo"].append("✅ Favicon присутствует")

    keywords = soup.find("meta", attrs={"name": "keywords"})
    if keywords and keywords.get("content", "").strip():
        result["info"].append(f"🔑 Keywords: {escape(keywords['content'].strip()[:80])}")


def _check_performance(soup: BeautifulSoup, html: str, elapsed: float, result: dict) -> None:
    if elapsed > 5:
        result["errors"].append(f"🐢 Скорость: ответ {elapsed:.1f}с (критично медленно, норма &lt;3с)")
    elif elapsed > 3:
        result["warnings"].append(f"⚠️ Скорость: ответ {elapsed:.1f}с (рекомендуется &lt;3с)")
    else:
        result["info"].append(f"✅ Скорость ответа: {elapsed:.2f}с")

    html_size_kb = len(html.encode("utf-8")) / 1024
    if html_size_kb > 500:
        result["warnings"].append(f"⚠️ Размер HTML: {html_size_kb:.0f} KB (рекомендуется &lt;500 KB)")
    else:
        result["info"].append(f"📄 Размер HTML: {html_size_kb:.1f} KB")

    scripts = soup.find_all("script", src=True)
    render_blocking = [s for s in scripts if not s.get("async") and not s.get("defer")]
    total_scripts = len(scripts)
    if len(render_blocking) > 3:
        result["warnings"].append(
            f"⚠️ Производительность: {len(render_blocking)}/{total_scripts} скриптов блокируют рендеринг (нет async/defer)"
        )
    else:
        result["info"].append(f"📜 Скрипты: {total_scripts} ({len(render_blocking)} блокирующих)")

    styles = soup.find_all("link", rel="stylesheet")
    if len(styles) > 10:
        result["warnings"].append(f"⚠️ Производительность: {len(styles)} CSS-файлов (стоит объединить)")
    else:
        result["info"].append(f"🎨 CSS-файлов: {len(styles)}")

    images = soup.find_all("img")
    lazy_images = [img for img in images if img.get("loading") == "lazy"]
    result["info"].append(f"🖼 Изображений: {len(images)} ({len(lazy_images)} с lazy loading)")

    text = soup.get_text(separator=" ", strip=True)
    words = len(text.split())
    result["info"].append(f"📝 Слов на странице: {words}")


def _check_security(headers, result: dict) -> None:
    security_headers = {
        "X-Content-Type-Options": "защита от MIME-sniffing",
        "X-Frame-Options": "защита от clickjacking",
        "Strict-Transport-Security": "принудительный HTTPS (HSTS)",
        "Content-Security-Policy": "политика безопасности (CSP)",
        "Referrer-Policy": "политика реферера",
        "Permissions-Policy": "политика разрешений браузера",
    }
    missing = []
    present = []
    for h, desc in security_headers.items():
        if h not in headers:
            missing.append(f"{h}")
        else:
            present.append(h)

    if missing:
        result["warnings"].append(
            f"⚠️ Безопасность: нет заголовков ({len(missing)}/{len(security_headers)}):\n"
            + "\n".join(f"   • {h}" for h in missing)
        )
    else:
        result["info"].append("✅ Все заголовки безопасности присутствуют")

    server = headers.get("Server", "")
    if server:
        result["warnings"].append(f"⚠️ Server раскрывает версию: <code>{escape(server)}</code>")

    x_powered = headers.get("X-Powered-By", "")
    if x_powered:
        result["warnings"].append(f"⚠️ X-Powered-By раскрывает технологию: <code>{escape(x_powered)}</code>")


def _check_accessibility(soup: BeautifulSoup, result: dict) -> None:
    imgs_no_alt = [img for img in soup.find_all("img") if not img.get("alt")]
    if imgs_no_alt:
        result["warnings"].append(
            f"⚠️ Доступность: {len(imgs_no_alt)} изображений без атрибута alt"
        )
    else:
        total_imgs = len(soup.find_all("img"))
        if total_imgs > 0:
            result["info"].append(f"✅ Все {total_imgs} изображений имеют alt")

    inputs_no_label = []
    for inp in soup.find_all("input"):
        if inp.get("type") in ("text", "email", "password", "number", "tel", None):
            inp_id = inp.get("id")
            has_label = bool(inp_id and soup.find("label", attrs={"for": inp_id}))
            has_aria = bool(inp.get("aria-label") or inp.get("aria-labelledby"))
            if not has_label and not has_aria:
                inputs_no_label.append(inp)
    if inputs_no_label:
        result["warnings"].append(
            f"⚠️ Доступность: {len(inputs_no_label)} полей ввода без label/aria-label"
        )

    lang = soup.find("html", attrs={"lang": True})
    if not lang:
        result["warnings"].append("⚠️ Доступность: у тега &lt;html&gt; нет атрибута lang")
    else:
        result["info"].append(f"🌐 Язык страницы: {escape(lang.get('lang', ''))}")

    skip_link = soup.find("a", href="#main") or soup.find("a", href="#content")
    if not skip_link:
        result["info"].append("ℹ️ Доступность: нет skip-link (необязательно, но улучшает доступность)")


def _check_links(soup: BeautifulSoup, base_url: str, result: dict) -> None:
    parsed = urlparse(base_url)
    base_domain = parsed.netloc

    all_links = soup.find_all("a", href=True)
    external_links = []
    empty_links = []
    nofollow_links = []

    for a in all_links:
        href = a["href"].strip()
        rel = a.get("rel", [])
        if not href or href == "#":
            empty_links.append(a)
        elif href.startswith("http"):
            link_domain = urlparse(href).netloc
            if link_domain and link_domain != base_domain:
                external_links.append(href)
                if "nofollow" in rel:
                    nofollow_links.append(href)

    result["info"].append(
        f"🔗 Ссылки: {len(all_links)} всего, {len(external_links)} внешних, {len(nofollow_links)} nofollow"
    )
    if empty_links:
        result["warnings"].append(f"⚠️ Пустые/якорные ссылки (#): {len(empty_links)} шт.")


def _check_tech(soup: BeautifulSoup, headers, html: str, result: dict) -> None:
    detected = []

    generator = soup.find("meta", attrs={"name": "generator"})
    if generator and generator.get("content"):
        detected.append(f"⚙️ {escape(generator['content'])}")

    html_lower = html.lower()

    if "wp-content" in html_lower or "wp-includes" in html_lower:
        detected.append("📦 WordPress")
    if "bitrix" in html_lower or "1c-bitrix" in html_lower:
        detected.append("📦 1C-Bitrix")
    if "__next" in html or "/_next/" in html:
        detected.append("⚛️ Next.js")
    if "nuxt" in html_lower:
        detected.append("💚 Nuxt.js")
    if "react" in html_lower and "react-dom" in html_lower:
        detected.append("⚛️ React")
    if "vue.js" in html_lower or "vue.min.js" in html_lower:
        detected.append("💚 Vue.js")
    if "angular" in html_lower and "ng-version" in html_lower:
        detected.append("🔴 Angular")
    if "jquery" in html_lower:
        detected.append("🔧 jQuery")
    if "bootstrap" in html_lower:
        detected.append("🅱️ Bootstrap")
    if "tailwind" in html_lower:
        detected.append("🌊 Tailwind CSS")
    if "shopify" in html_lower:
        detected.append("🛒 Shopify")
    if "tilda" in html_lower:
        detected.append("🔵 Tilda")
    if "wix.com" in html_lower:
        detected.append("🎨 Wix")

    server = headers.get("Server", "")
    if server:
        if "nginx" in server.lower():
            detected.append("🖥 Nginx")
        elif "apache" in server.lower():
            detected.append("🖥 Apache")
        elif "cloudflare" in server.lower():
            detected.append("🌩 Cloudflare")

    x_powered = headers.get("X-Powered-By", "")
    if x_powered:
        if "php" in x_powered.lower():
            detected.append(f"🐘 PHP ({escape(x_powered)})")
        elif "asp.net" in x_powered.lower():
            detected.append(f"🔷 ASP.NET")

    if detected:
        result["tech"] = detected


def _check_structured_data(soup: BeautifulSoup, result: dict) -> None:
    json_ld = soup.find_all("script", attrs={"type": "application/ld+json"})
    microdata = soup.find_all(attrs={"itemtype": True})

    if json_ld:
        result["seo"].append(f"✅ Структурированные данные (JSON-LD): {len(json_ld)} блоков")
    elif microdata:
        result["seo"].append(f"✅ Структурированные данные (Microdata): {len(microdata)} элементов")
    else:
        result["warnings"].append("⚠️ SEO: нет структурированных данных (Schema.org/JSON-LD)")


def _check_analytics(html: str, result: dict) -> None:
    analytics = []
    html_lower = html.lower()

    if "google-analytics.com" in html_lower or "gtag(" in html_lower or "ga(" in html_lower:
        analytics.append("📊 Google Analytics")
    if "googletagmanager.com" in html_lower:
        analytics.append("📊 Google Tag Manager")
    if "metrika.yandex" in html_lower or "mc.yandex" in html_lower:
        analytics.append("📊 Яндекс.Метрика")
    if "connect.facebook.net" in html_lower or "fbq(" in html_lower:
        analytics.append("📘 Facebook Pixel")
    if "vk.com/js/api" in html_lower:
        analytics.append("🔵 VK Pixel")

    if analytics:
        result["info"].append("📡 Системы аналитики: " + ", ".join(analytics))
    else:
        result["warnings"].append("⚠️ Не найдено систем веб-аналитики")


async def _check_extras(session: aiohttp.ClientSession, url: str, result: dict) -> None:
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    checks = [
        (f"{base}/robots.txt", "🤖 robots.txt"),
        (f"{base}/sitemap.xml", "🗺 sitemap.xml"),
    ]

    for check_url, label in checks:
        try:
            async with session.get(
                check_url,
                timeout=aiohttp.ClientTimeout(total=5),
                allow_redirects=True,
            ) as r:
                if r.status == 200:
                    result["seo"].append(f"✅ {label} найден")
                else:
                    result["warnings"].append(f"⚠️ {label} не найден (статус {r.status})")
        except Exception:
            result["warnings"].append(f"⚠️ {label} недоступен")


def _calc_score(data: dict) -> tuple[int, str]:
    errors = len(data["errors"])
    warnings = len(data["warnings"])
    score = max(0, 100 - errors * 15 - warnings * 5)
    if score >= 85:
        emoji = "🟢"
    elif score >= 65:
        emoji = "🟡"
    elif score >= 45:
        emoji = "🟠"
    else:
        emoji = "🔴"
    return score, emoji


def format_report(data: dict) -> str:
    score, emoji = _calc_score(data)
    perf = data.get("performance", {})
    lines = [
        f"🌐 <b>Отчёт по сайту</b>",
        f"<code>{escape(data['url'])}</code>",
        "",
    ]

    if perf.get("status_code"):
        lines.append(f"📡 HTTP-статус: <b>{perf['status_code']}</b>")
    if perf.get("response_time"):
        lines.append(f"⏱ Время ответа: <b>{perf['response_time']}с</b>")
    lines.append(f"{emoji} Оценка: <b>{score}/100</b>  ({len(data['errors'])} ошибок, {len(data['warnings'])} предупреждений)")

    if data["errors"]:
        lines.append("\n<b>🔴 Ошибки:</b>")
        lines.extend(f"  {e}" for e in data["errors"])

    if data["warnings"]:
        lines.append("\n<b>🟡 Предупреждения:</b>")
        lines.extend(f"  {w}" for w in data["warnings"])

    if data["seo"]:
        lines.append("\n<b>🔎 SEO:</b>")
        lines.extend(f"  {s}" for s in data["seo"])

    if data.get("tech"):
        lines.append("\n<b>🛠 Технологии:</b>")
        lines.extend(f"  {t}" for t in data["tech"])

    if data["info"]:
        lines.append("\n<b>📊 Информация:</b>")
        lines.extend(f"  {i}" for i in data["info"])

    if not data["errors"] and not data["warnings"]:
        lines.append("\n🎉 <b>Серьёзных проблем не найдено!</b>")

    return "\n".join(lines)
