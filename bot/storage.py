import json
import logging
from datetime import datetime, date, timedelta
from typing import Optional

from bot.db import get_session
from bot.models import (
    User, History, PendingInvoice, Payment, Setting, AdminLog, Report, SecurityLog
)

logger = logging.getLogger(__name__)

DEFAULT_PRICES = {"week": 2.99, "month": 7.99, "quarter": 19.99}
DEFAULT_FREE_LIMIT = 3


def _today() -> str:
    return date.today().isoformat()


def _now() -> datetime:
    return datetime.utcnow()


class Storage:
    def _get_setting(self, key: str, default=None):
        with get_session() as session:
            s = session.query(Setting).filter_by(key=key).first()
            return s.value if s else default

    def _set_setting(self, key: str, value: str):
        with get_session() as session:
            s = session.query(Setting).filter_by(key=key).first()
            if s:
                s.value = value
            else:
                session.add(Setting(key=key, value=value))

    # ─── Users ────────────────────────────────────────────────────────────────

    def upsert_user(self, user_id: int, first_name: str, username: Optional[str]):
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if not u:
                u = User(telegram_id=user_id, first_name=first_name, username=username)
                session.add(u)
            else:
                u.first_name = first_name
                u.username = username
                u.last_active = _now()

    def touch_user(self, user_id: int):
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if u:
                u.last_active = _now()

    def get_user(self, user_id: int) -> Optional[dict]:
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if not u:
                return None
            return self._user_to_dict(u)

    def _user_to_dict(self, u: User) -> dict:
        return {
            "id": u.telegram_id,
            "first_name": u.first_name or "",
            "username": u.username,
            "ip_address": u.ip_address,
            "first_seen": u.first_seen.isoformat() if u.first_seen else None,
            "last_active": u.last_active.isoformat() if u.last_active else None,
            "tier": u.tier or "lite",
            "banned": u.banned,
            "web_registered": u.web_registered,
            "sub_expires": u.subscription_until.isoformat() if u.subscription_until else None,
            "sub_plan": u.sub_plan,
            "free_uses_today": u.free_uses_today,
            "free_uses_date": u.free_uses_date or "",
            "total_analyses": u.total_analyses,
        }

    def get_all_users_list(self) -> list[dict]:
        with get_session() as session:
            users = session.query(User).order_by(User.last_active.desc()).all()
            return [self._user_to_dict(u) for u in users]

    def get_all_user_ids(self) -> list[int]:
        with get_session() as session:
            rows = session.query(User.telegram_id).filter_by(banned=False).all()
            return [r[0] for r in rows]

    def total_users(self) -> int:
        with get_session() as session:
            return session.query(User).count()

    # ─── Ban ──────────────────────────────────────────────────────────────────

    def is_banned(self, user_id: int) -> bool:
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            return bool(u and u.banned)

    def ban_user(self, user_id: int):
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if u:
                u.banned = True

    def unban_user(self, user_id: int):
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if u:
                u.banned = False

    def banned_count(self) -> int:
        with get_session() as session:
            return session.query(User).filter_by(banned=True).count()

    # ─── Registration ─────────────────────────────────────────────────────────

    def is_web_registered(self, user_id: int) -> bool:
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            return bool(u and u.web_registered)

    def complete_web_registration(self, user_id: int, ip_address: str):
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if u:
                u.web_registered = True
                u.ip_address = ip_address
                u.registration_date = _now()

    # ─── Subscription ─────────────────────────────────────────────────────────

    def has_active_sub(self, user_id: int) -> bool:
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if not u or not u.subscription_until:
                return False
            return u.subscription_until > datetime.utcnow()

    def is_pro(self, user_id: int) -> bool:
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if not u:
                return False
            if u.tier == "pro" and u.subscription_until and u.subscription_until > datetime.utcnow():
                return True
            return False

    def sub_expires_str(self, user_id: int) -> Optional[str]:
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if not u or not u.subscription_until:
                return None
            return u.subscription_until.strftime("%d.%m.%Y")

    def activate_subscription(self, user_id: int, plan: str, days: int):
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if not u:
                u = User(telegram_id=user_id)
                session.add(u)
            existing = u.subscription_until
            if existing and existing > datetime.utcnow():
                base = existing
            else:
                base = datetime.utcnow()
            u.subscription_until = base + timedelta(days=days)
            u.sub_plan = plan
            u.tier = "pro"

    def subscribed_count(self) -> int:
        with get_session() as session:
            return (
                session.query(User)
                .filter(User.subscription_until > datetime.utcnow())
                .count()
            )

    def manually_grant_subscription(self, user_id: int, plan: str, days: int):
        self.activate_subscription(user_id, plan, days)

    # ─── Free uses ────────────────────────────────────────────────────────────

    def get_free_limit(self) -> int:
        v = self._get_setting("free_limit")
        return int(v) if v else DEFAULT_FREE_LIMIT

    def set_free_limit(self, limit: int):
        self._set_setting("free_limit", str(limit))

    def get_free_uses_today(self, user_id: int) -> int:
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if not u or u.free_uses_date != _today():
                return 0
            return u.free_uses_today

    def use_free_analysis(self, user_id: int):
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if not u:
                return
            if u.free_uses_date != _today():
                u.free_uses_today = 0
                u.free_uses_date = _today()
            u.free_uses_today = (u.free_uses_today or 0) + 1
            u.total_analyses = (u.total_analyses or 0) + 1

    def can_analyze(self, user_id: int) -> bool:
        if self.has_active_sub(user_id):
            return True
        limit = self.get_free_limit()
        return self.get_free_uses_today(user_id) < limit

    def free_left(self, user_id: int) -> int:
        limit = self.get_free_limit()
        return max(0, limit - self.get_free_uses_today(user_id))

    def record_analysis(self, user_id: int):
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if u:
                u.total_analyses = (u.total_analyses or 0) + 1

    def total_analyses(self) -> int:
        with get_session() as session:
            from sqlalchemy import func
            result = session.query(func.sum(User.total_analyses)).scalar()
            return int(result or 0)

    # ─── History ──────────────────────────────────────────────────────────────

    def add_history(self, user_id: int, url: str, score: int, report_id: str = None):
        with get_session() as session:
            h = History(
                user_id=user_id,
                target_url=url,
                test_type="analysis",
                score=score,
                report_id=report_id,
            )
            session.add(h)

    def get_history(self, user_id: int) -> list[dict]:
        with get_session() as session:
            rows = (
                session.query(History)
                .filter_by(user_id=user_id)
                .order_by(History.start_time.desc())
                .limit(10)
                .all()
            )
            return [
                {
                    "url": h.target_url,
                    "score": h.score,
                    "date": h.start_time.strftime("%d.%m.%Y %H:%M") if h.start_time else "",
                    "report_id": h.report_id,
                }
                for h in rows
            ]

    def add_test_history(self, user_id: int, url: str, report_id: str, rps: float, success_rate: float, p95: float, p99: float):
        with get_session() as session:
            h = History(
                user_id=user_id,
                target_url=url,
                test_type="load_test",
                report_id=report_id,
                rps=rps,
                success_rate=success_rate,
                p95=p95,
                p99=p99,
                end_time=datetime.utcnow(),
            )
            session.add(h)

    # ─── Pending invoices ─────────────────────────────────────────────────────

    def add_pending_invoice(self, invoice_id: int, user_id: int, plan: str):
        with get_session() as session:
            inv = PendingInvoice(invoice_id=invoice_id, user_id=user_id, plan=plan)
            session.add(inv)

    def remove_pending_invoice(self, invoice_id: int):
        with get_session() as session:
            inv = session.query(PendingInvoice).filter_by(invoice_id=invoice_id).first()
            if inv:
                session.delete(inv)

    def get_pending_invoices(self) -> list[dict]:
        with get_session() as session:
            rows = session.query(PendingInvoice).all()
            return [
                {"invoice_id": r.invoice_id, "user_id": r.user_id, "plan": r.plan}
                for r in rows
            ]

    # ─── Payments ─────────────────────────────────────────────────────────────

    def add_payment(self, user_id: int, plan: str, amount: float, currency: str):
        with get_session() as session:
            p = Payment(user_id=user_id, plan=plan, amount=amount, currency=currency)
            session.add(p)

    def get_payments(self) -> list[dict]:
        with get_session() as session:
            rows = session.query(Payment).order_by(Payment.paid_at.desc()).all()
            return [
                {
                    "user_id": r.user_id,
                    "plan": r.plan,
                    "amount": r.amount,
                    "currency": r.currency,
                    "paid_at": r.paid_at.isoformat() if r.paid_at else "",
                }
                for r in rows
            ]

    def total_revenue(self) -> float:
        with get_session() as session:
            from sqlalchemy import func
            result = session.query(func.sum(Payment.amount)).scalar()
            return float(result or 0)

    # ─── Prices ───────────────────────────────────────────────────────────────

    def get_prices(self) -> dict:
        v = self._get_setting("prices")
        if v:
            try:
                return json.loads(v)
            except Exception:
                pass
        return DEFAULT_PRICES.copy()

    def set_price(self, plan: str, price: float):
        prices = self.get_prices()
        prices[plan] = price
        self._set_setting("prices", json.dumps(prices))

    # ─── Banner ───────────────────────────────────────────────────────────────

    def get_banner(self) -> Optional[str]:
        return self._get_setting("banner_file_id")

    def set_banner(self, file_id: Optional[str]):
        self._set_setting("banner_file_id", file_id or "")

    def get_settings(self) -> dict:
        return {
            "prices": self.get_prices(),
            "free_limit": self.get_free_limit(),
            "banner_file_id": self.get_banner(),
        }

    # ─── Stats ────────────────────────────────────────────────────────────────

    def new_users_today(self) -> int:
        today = date.today()
        with get_session() as session:
            return (
                session.query(User)
                .filter(User.first_seen >= datetime(today.year, today.month, today.day))
                .count()
            )

    # ─── Admin log ────────────────────────────────────────────────────────────

    def log_admin_action(self, admin_id: int, action: str):
        with get_session() as session:
            session.add(AdminLog(admin_id=admin_id, action=action))

    def get_admin_logs(self, limit: int = 50) -> list[dict]:
        with get_session() as session:
            rows = (
                session.query(AdminLog)
                .order_by(AdminLog.timestamp.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "admin_id": r.admin_id,
                    "action": r.action,
                    "timestamp": r.timestamp.strftime("%d.%m %H:%M") if r.timestamp else "",
                }
                for r in rows
            ]

    # ─── Referral system ──────────────────────────────────────────────────────

    def get_or_create_referral_code(self, user_id: int) -> str:
        import random, string
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if not u:
                return ""
            if u.referral_code:
                return u.referral_code
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            while session.query(User).filter_by(referral_code=code).first():
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            u.referral_code = code
            return code

    def get_user_by_referral_code(self, code: str) -> Optional[dict]:
        with get_session() as session:
            u = session.query(User).filter_by(referral_code=code.upper()).first()
            return self._user_to_dict(u) if u else None

    def apply_referral(self, new_user_id: int, referrer_id: int) -> bool:
        """Give referrer +2 bonus tests when a new user joins via their link."""
        with get_session() as session:
            new_u = session.query(User).filter_by(telegram_id=new_user_id).first()
            ref_u = session.query(User).filter_by(telegram_id=referrer_id).first()
            if not new_u or not ref_u:
                return False
            if new_u.referred_by:
                return False
            new_u.referred_by = referrer_id
            ref_u.bonus_tests = (ref_u.bonus_tests or 0) + 2
            ref_u.referral_count = (ref_u.referral_count or 0) + 1
            return True

    def has_bonus_test(self, user_id: int) -> bool:
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            return bool(u and (u.bonus_tests or 0) > 0)

    def use_bonus_test(self, user_id: int) -> bool:
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if u and (u.bonus_tests or 0) > 0:
                u.bonus_tests -= 1
                return True
            return False

    def get_bonus_tests(self, user_id: int) -> int:
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            return (u.bonus_tests or 0) if u else 0

    def get_referral_stats(self, user_id: int) -> dict:
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if not u:
                return {"code": None, "count": 0, "bonus": 0}
            return {
                "code": u.referral_code,
                "count": u.referral_count or 0,
                "bonus": u.bonus_tests or 0,
            }

    # ─── Security / Auto-ban ──────────────────────────────────────────────────

    def track_blocked_attempt(self, user_id: int, url: str, reason: str,
                               user_ip: str = "unknown") -> dict:
        """Log a blocked URL attempt. Auto-ban after 3 violations. Returns action info."""
        action = "warned"
        with get_session() as session:
            u = session.query(User).filter_by(telegram_id=user_id).first()
            if u:
                u.blocked_attempts = (u.blocked_attempts or 0) + 1
                attempts = u.blocked_attempts
                if attempts >= 3 and not u.banned:
                    u.banned = True
                    action = "banned"
                elif u.banned:
                    action = "already_banned"
            else:
                attempts = 1

            session.add(SecurityLog(
                user_id=user_id,
                user_ip=user_ip,
                attempted_url=url,
                reason=reason,
                action_taken=action,
            ))
        return {"action": action, "attempts": attempts}

    def get_security_logs(self, limit: int = 20) -> list[dict]:
        with get_session() as session:
            rows = (
                session.query(SecurityLog)
                .order_by(SecurityLog.timestamp.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "user_id": r.user_id,
                    "user_ip": r.user_ip,
                    "attempted_url": r.attempted_url,
                    "reason": r.reason,
                    "action_taken": r.action_taken,
                    "timestamp": r.timestamp.strftime("%d.%m.%Y %H:%M") if r.timestamp else "",
                }
                for r in rows
            ]

    # ─── User reports history ─────────────────────────────────────────────────

    def get_user_reports(self, user_id: int, limit: int = 10) -> list[dict]:
        with get_session() as session:
            rows = (
                session.query(Report)
                .filter_by(user_id=user_id)
                .order_by(Report.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "report_id": r.report_id,
                    "report_type": r.report_type,
                    "target_url": r.target_url or "—",
                    "created_at": r.created_at.strftime("%d.%m %H:%M") if r.created_at else "",
                }
                for r in rows
            ]

    # ─── Scheduled tasks ──────────────────────────────────────────────────────

    def can_use_test(self, user_id: int) -> bool:
        """True if user can run a test (free slot OR bonus test OR PRO)."""
        if self.has_active_sub(user_id):
            return True
        if self.has_bonus_test(user_id):
            return True
        return self.can_analyze(user_id)

    # ─── Reports ──────────────────────────────────────────────────────────────

    def get_report(self, report_id: str) -> Optional[dict]:
        with get_session() as session:
            r = session.query(Report).filter_by(report_id=report_id).first()
            if not r:
                return None
            return {
                "report_id": r.report_id,
                "user_id": r.user_id,
                "report_type": r.report_type,
                "target_url": r.target_url,
                "data": r.data,
                "created_at": r.created_at.strftime("%d.%m.%Y %H:%M") if r.created_at else "",
            }


storage = Storage()
