import aiohttp
import asyncio
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

                if resp.status in (301, 302):
                    result["warnings"].append(f"↪️ Редирект: {resp.status} → {resp.url}")

                content_type = resp.headers.get("Content-Type", "")
                if "text/html" not in content_type:
                    result["warnings"].append(f"⚠️ Тип контента: {content_type} (не HTML)")

                html = await resp.text(errors="replace")
                soup = BeautifulSoup(html, "lxml")

                _check_seo(soup, url, result)
                _check_performance(soup, html, elapsed, result)
                _check_security(resp.headers, result)
                _check_accessibility(soup, result)
                _check_links(soup, url, result)

        result["success"] = True
    except aiohttp.ClientConnectorError:
        result["errors"].append("🚫 Не удалось подключиться к сайту. Проверь URL.")
    except asyncio.TimeoutError:
        result["errors"].append("⏱ Сайт не ответил за 15 секунд (слишком медленный).")
    except Exception as e:
        result["errors"].append(f"❌ Ошибка при анализе: {type(e).__name__}: {e}")

    return result


def _check_seo(soup: BeautifulSoup, url: str, result: dict) -> None:
    title = soup.find("title")
    if not title or not title.get_text(strip=True):
        result["errors"].append("❌ SEO: отсутствует тег <title>")
    else:
        t = title.get_text(strip=True)
        if len(t) < 10:
            result["warnings"].append(f"⚠️ SEO: title слишком короткий ({len(t)} символов)")
        elif len(t) > 70:
            result["warnings"].append(f"⚠️ SEO: title слишком длинный ({len(t)} символов, рекомендуется ≤70)")
        else:
            result["seo"].append(f"✅ Title: «{t[:60]}{'...' if len(t)>60 else ''}»")

    meta_desc = soup.find("meta", attrs={"name": "description"})
    if not meta_desc or not meta_desc.get("content", "").strip():
        result["warnings"].append("⚠️ SEO: отсутствует meta description")
    else:
        desc = meta_desc["content"].strip()
        if len(desc) < 50:
            result["warnings"].append(f"⚠️ SEO: meta description слишком короткий ({len(desc)} симв.)")
        elif len(desc) > 160:
            result["warnings"].append(f"⚠️ SEO: meta description слишком длинный ({len(desc)} симв., рекомендуется ≤160)")
        else:
            result["seo"].append("✅ Meta description присутствует")

    h1_tags = soup.find_all("h1")
    if not h1_tags:
        result["warnings"].append("⚠️ SEO: нет тега <h1>")
    elif len(h1_tags) > 1:
        result["warnings"].append(f"⚠️ SEO: найдено {len(h1_tags)} тегов <h1> (рекомендуется 1)")
    else:
        result["seo"].append(f"✅ H1: «{h1_tags[0].get_text(strip=True)[:50]}»")

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
        result["warnings"].append("⚠️ SEO: отсутствуют Open Graph теги (og:title, og:description, og:image)")
    elif og_count < 3:
        result["warnings"].append(f"⚠️ SEO: Open Graph неполный (найдено {og_count}/3 основных тегов)")
    else:
        result["seo"].append("✅ Open Graph теги присутствуют")

    viewport = soup.find("meta", attrs={"name": "viewport"})
    if not viewport:
        result["errors"].append("❌ Адаптивность: нет meta viewport — сайт может плохо выглядеть на мобильных")
    else:
        result["seo"].append("✅ Meta viewport присутствует")

    robots = soup.find("meta", attrs={"name": "robots"})
    if robots and "noindex" in robots.get("content", "").lower():
        result["warnings"].append("⚠️ SEO: страница закрыта от индексации (noindex)")


def _check_performance(soup: BeautifulSoup, html: str, elapsed: float, result: dict) -> None:
    if elapsed > 5:
        result["errors"].append(f"🐢 Скорость: сайт отвечает {elapsed:.1f}с (критично медленно)")
    elif elapsed > 3:
        result["warnings"].append(f"⚠️ Скорость: ответ {elapsed:.1f}с (рекомендуется <3с)")
    else:
        result["info"].append(f"✅ Скорость ответа: {elapsed:.2f}с")

    html_size_kb = len(html.encode("utf-8")) / 1024
    if html_size_kb > 500:
        result["warnings"].append(f"⚠️ Размер HTML: {html_size_kb:.0f} KB (рекомендуется <500 KB)")
    else:
        result["info"].append(f"📄 Размер HTML: {html_size_kb:.1f} KB")

    scripts = soup.find_all("script", src=True)
    render_blocking = [s for s in scripts if not s.get("async") and not s.get("defer")]
    if len(render_blocking) > 3:
        result["warnings"].append(
            f"⚠️ Производительность: {len(render_blocking)} скриптов блокируют рендеринг (без async/defer)"
        )

    styles = soup.find_all("link", rel="stylesheet")
    if len(styles) > 10:
        result["warnings"].append(f"⚠️ Производительность: {len(styles)} CSS-файлов (стоит объединить)")

    images = soup.find_all("img")
    result["info"].append(f"🖼 Изображений: {len(images)}")


def _check_security(headers, result: dict) -> None:
    security_headers = {
        "X-Content-Type-Options": "защита от MIME-sniffing",
        "X-Frame-Options": "защита от clickjacking",
        "X-XSS-Protection": "защита от XSS",
        "Strict-Transport-Security": "принудительный HTTPS (HSTS)",
        "Content-Security-Policy": "политика безопасности контента (CSP)",
    }
    missing = []
    for h, desc in security_headers.items():
        if h not in headers:
            missing.append(f"{h} ({desc})")

    if missing:
        result["warnings"].append(
            "⚠️ Безопасность: отсутствуют заголовки:\n   • " + "\n   • ".join(missing)
        )
    else:
        result["info"].append("✅ Основные заголовки безопасности присутствуют")


def _check_accessibility(soup: BeautifulSoup, result: dict) -> None:
    imgs_no_alt = [img for img in soup.find_all("img") if not img.get("alt")]
    if imgs_no_alt:
        result["warnings"].append(
            f"⚠️ Доступность: {len(imgs_no_alt)} изображений без атрибута alt"
        )

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
        result["warnings"].append("⚠️ Доступность: у тега <html> нет атрибута lang")


def _check_links(soup: BeautifulSoup, base_url: str, result: dict) -> None:
    parsed = urlparse(base_url)
    base_domain = parsed.netloc

    all_links = soup.find_all("a", href=True)
    external_links = []
    empty_links = []

    for a in all_links:
        href = a["href"].strip()
        if not href or href == "#":
            empty_links.append(a)
        elif href.startswith("http"):
            link_domain = urlparse(href).netloc
            if link_domain and link_domain != base_domain:
                external_links.append(href)

    result["info"].append(f"🔗 Ссылок всего: {len(all_links)}, внешних: {len(external_links)}")
    if empty_links:
        result["warnings"].append(f"⚠️ Пустые/якорные ссылки: {len(empty_links)} шт.")


def format_report(data: dict) -> str:
    lines = [f"🌐 <b>Анализ сайта:</b> <code>{data['url']}</code>\n"]

    perf = data.get("performance", {})
    if perf.get("status_code"):
        lines.append(f"📡 Статус: <b>{perf['status_code']}</b>")
    if perf.get("response_time"):
        lines.append(f"⏱ Время ответа: <b>{perf['response_time']}с</b>")
    if perf.get("final_url") and perf["final_url"] != data["url"]:
        lines.append(f"↪️ Финальный URL: <code>{perf['final_url']}</code>")

    if data["errors"]:
        lines.append("\n<b>🔴 Ошибки:</b>")
        lines.extend(f"  {e}" for e in data["errors"])

    if data["warnings"]:
        lines.append("\n<b>🟡 Предупреждения:</b>")
        lines.extend(f"  {w}" for w in data["warnings"])

    if data["seo"]:
        lines.append("\n<b>🔎 SEO:</b>")
        lines.extend(f"  {s}" for s in data["seo"])

    if data["info"]:
        lines.append("\n<b>📊 Общая информация:</b>")
        lines.extend(f"  {i}" for i in data["info"])

    if not data["errors"] and not data["warnings"]:
        lines.append("\n🎉 <b>Серьёзных проблем не найдено!</b>")

    total_issues = len(data["errors"]) + len(data["warnings"])
    if total_issues == 0:
        score = "🟢 Отлично"
    elif total_issues <= 3:
        score = "🟡 Хорошо"
    elif total_issues <= 6:
        score = "🟠 Удовлетворительно"
    else:
        score = "🔴 Требует доработки"

    lines.append(f"\n<b>Итоговая оценка: {score}</b> ({total_issues} проблем)")
    return "\n".join(lines)
