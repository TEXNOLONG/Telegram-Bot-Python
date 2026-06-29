from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.config import CHANNEL_LINK, SUBSCRIPTION_PLANS, USERS_PER_PAGE, PAYMENTS_PER_PAGE


# ─── User keyboards ───────────────────────────────────────────────────────────

def main_menu_kb(has_sub: bool = False, is_registered: bool = False) -> InlineKeyboardMarkup:
    sub_label = "💎 PRO ✅" if has_sub else "💎 PRO-подписка"
    rows = [
        [InlineKeyboardButton(text="🔍 Анализировать сайт", callback_data="analyze")],
        [
            InlineKeyboardButton(text="🔐 SSL-сертификат", callback_data="ssl"),
            InlineKeyboardButton(text="🌐 DNS / IP", callback_data="dns"),
        ],
    ]
    if is_registered:
        if has_sub:
            rows.append([InlineKeyboardButton(text="⚡ PRO нагрузочный тест", callback_data="stress_pro")])
        else:
            rows.append([InlineKeyboardButton(text="🔧 LITE нагрузочный тест", callback_data="stress_lite")])
    rows.append([InlineKeyboardButton(text="🛡️ Проверка DDoS-защиты", callback_data="ddos_check")])
    rows.append([InlineKeyboardButton(text="📋 История", callback_data="history")])
    rows.append([
        InlineKeyboardButton(text=sub_label, callback_data="sub"),
        InlineKeyboardButton(text="👤 Статус", callback_data="status"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def register_kb(user_id: int, domain: str) -> InlineKeyboardMarkup:
    url = f"https://{domain}/register/{user_id}"
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔗 Зарегистрироваться", url=url)
    ]])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel")],
    ])


def back_to_menu_kb(has_sub: bool = False, is_registered: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="🔍 Анализировать снова", callback_data="analyze")],
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def stress_lite_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="60 сек  /  100 RPS", callback_data="lite:60:100")],
        [InlineKeyboardButton(text="30 сек  /   50 RPS", callback_data="lite:30:50")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu")],
    ])


def stress_pro_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Низкий   — 200 RPS / 60с",  callback_data="pro:60:low")],
        [InlineKeyboardButton(text="💥 Средний  — 500 RPS / 120с", callback_data="pro:120:medium")],
        [InlineKeyboardButton(text="⚡ Высокий  — 1000 RPS / 180с", callback_data="pro:180:high")],
        [InlineKeyboardButton(text="🌪️ Ультра   — 2000 RPS / 300с", callback_data="pro:300:ultra")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu")],
    ])


def admin_test_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔧 Кастомный тест", callback_data="admin_custom_test")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="adm")],
    ])


# ─── Subscription / Payment keyboards ────────────────────────────────────────

def subscription_menu_kb(prices: dict, has_sub: bool, expires: str | None) -> InlineKeyboardMarkup:
    rows = []
    plan_map = {
        "week":    ("⚡", "PRO 7 дней"),
        "month":   ("💎", "PRO 30 дней"),
        "quarter": ("👑", "PRO 90 дней"),
    }
    for key, (emoji, label) in plan_map.items():
        price = prices.get(key, SUBSCRIPTION_PLANS[key]["price"])
        rows.append([InlineKeyboardButton(
            text=f"{emoji} {label} — ${price} USDT",
            callback_data=f"plan:{key}",
        )])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def payment_kb(pay_url: str, invoice_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить через CryptoBot", url=pay_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"paychk:{invoice_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="sub")],
    ])


# ─── Admin keyboards ──────────────────────────────────────────────────────────

def admin_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats"),
            InlineKeyboardButton(text="👥 Пользователи", callback_data="adm_users:0"),
        ],
        [
            InlineKeyboardButton(text="💰 Платежи", callback_data="adm_pay:0"),
            InlineKeyboardButton(text="📢 Рассылка", callback_data="adm_bcast"),
        ],
        [
            InlineKeyboardButton(text="🖼 Баннер", callback_data="adm_banner"),
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="adm_set"),
        ],
        [
            InlineKeyboardButton(text="📋 Логи", callback_data="adm_logs"),
            InlineKeyboardButton(text="🚀 Тест (admin)", callback_data="adm_test"),
        ],
    ])


def admin_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ В админ-меню", callback_data="adm")],
    ])


def admin_users_kb(users: list, page: int, total: int) -> InlineKeyboardMarkup:
    rows = []
    for u in users:
        fn = (u.get("first_name") or "—")[:20]
        un = f"@{u['username']}" if u.get("username") else f"#{u['id']}"
        ban = "🚫" if u.get("banned") else ""
        sub = "💎" if _has_active_sub(u) else ""
        rows.append([InlineKeyboardButton(
            text=f"{ban}{sub} {fn} {un}",
            callback_data=f"adm_u:{u['id']}",
        )])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm_users:{page - 1}"))
    nav.append(InlineKeyboardButton(
        text=f"{page + 1}/{max(1, (total - 1) // USERS_PER_PAGE + 1)}", callback_data="noop"
    ))
    if (page + 1) * USERS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm_users:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="adm")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_user_kb(user_id: int, is_banned: bool, has_sub: bool) -> InlineKeyboardMarkup:
    rows = []
    if is_banned:
        rows.append([InlineKeyboardButton(text="✅ Разбанить", callback_data=f"adm_uban:{user_id}")])
    else:
        rows.append([InlineKeyboardButton(text="🚫 Забанить", callback_data=f"adm_ban:{user_id}")])
    rows.append([InlineKeyboardButton(text="💎 Выдать подписку", callback_data=f"adm_gs:{user_id}")])
    rows.append([InlineKeyboardButton(text="◀️ К списку", callback_data="adm_users:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_give_sub_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ 7 дней",  callback_data=f"adm_gsp:{user_id}:week")],
        [InlineKeyboardButton(text="💎 30 дней", callback_data=f"adm_gsp:{user_id}:month")],
        [InlineKeyboardButton(text="👑 90 дней", callback_data=f"adm_gsp:{user_id}:quarter")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data=f"adm_u:{user_id}")],
    ])


def admin_broadcast_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Только текст", callback_data="adm_btext")],
        [InlineKeyboardButton(text="🖼 Фото + текст", callback_data="adm_bphoto")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="adm")],
    ])


def admin_banner_kb(has_banner: bool) -> InlineKeyboardMarkup:
    rows = []
    if has_banner:
        rows.append([InlineKeyboardButton(text="🗑 Удалить баннер", callback_data="adm_banner_del")])
    rows.append([InlineKeyboardButton(text="🖼 Загрузить новый баннер", callback_data="adm_banner_set")])
    rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="adm")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_settings_kb(prices: dict, free_limit: int) -> InlineKeyboardMarkup:
    pw = prices.get("week", 2.99)
    pm = prices.get("month", 7.99)
    pq = prices.get("quarter", 19.99)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⚡ Цена 7 дней: ${pw}", callback_data="adm_price:week")],
        [InlineKeyboardButton(text=f"💎 Цена 30 дней: ${pm}", callback_data="adm_price:month")],
        [InlineKeyboardButton(text=f"👑 Цена 90 дней: ${pq}", callback_data="adm_price:quarter")],
        [InlineKeyboardButton(text=f"🆓 Бесплатных тестов в день: {free_limit}", callback_data="adm_setlim")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="adm")],
    ])


def admin_payments_kb(page: int, total: int) -> InlineKeyboardMarkup:
    nav = []
    pages = max(1, (total - 1) // PAYMENTS_PER_PAGE + 1)
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"adm_pay:{page - 1}"))
    nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"))
    if (page + 1) * PAYMENTS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"adm_pay:{page + 1}"))
    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="◀️ В меню", callback_data="adm")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_kb(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да", callback_data=yes_cb),
        InlineKeyboardButton(text="❌ Нет", callback_data=no_cb),
    ]])


# ─── Helper ───────────────────────────────────────────────────────────────────

def _has_active_sub(user: dict) -> bool:
    from datetime import datetime
    exp = user.get("sub_expires")
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp) > datetime.utcnow()
    except Exception:
        return False
