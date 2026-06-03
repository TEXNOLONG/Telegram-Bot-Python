import json
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Optional

DATA_FILE = Path("bot_data.json")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


class Storage:
    def __init__(self):
        self._data = self._load()
        self._ensure_defaults()

    def _load(self) -> dict:
        if DATA_FILE.exists():
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _ensure_defaults(self):
        self._data.setdefault("users", {})
        self._data.setdefault("history", {})
        self._data.setdefault("pending_invoices", [])
        self._data.setdefault("payments", [])
        self._data.setdefault("total_analyses", 0)
        self._data.setdefault("settings", {
            "banner_file_id": None,
            "prices": {"week": 2.99, "month": 7.99, "quarter": 19.99},
            "free_limit": 3,
        })
        self._save()

    def _save(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    # ─── Users ────────────────────────────────────────────────────────────────

    def _default_user(self) -> dict:
        return {
            "first_name": "",
            "username": None,
            "first_seen": _now(),
            "last_active": _now(),
            "banned": False,
            "sub_expires": None,
            "sub_plan": None,
            "free_uses_today": 0,
            "free_uses_date": "",
            "total_analyses": 0,
        }

    def get_user(self, user_id: int) -> dict:
        return self._data["users"].get(str(user_id), self._default_user())

    def upsert_user(self, user_id: int, first_name: str, username: Optional[str]):
        key = str(user_id)
        if key not in self._data["users"]:
            self._data["users"][key] = self._default_user()
        self._data["users"][key]["first_name"] = first_name
        self._data["users"][key]["username"] = username
        self._data["users"][key]["last_active"] = _now()
        self._save()

    def touch_user(self, user_id: int):
        key = str(user_id)
        if key in self._data["users"]:
            self._data["users"][key]["last_active"] = _now()
            self._save()

    def get_all_user_ids(self) -> list[int]:
        return [int(k) for k in self._data["users"]]

    def get_all_users_list(self) -> list[dict]:
        result = []
        for uid, u in self._data["users"].items():
            result.append({"id": int(uid), **u})
        result.sort(key=lambda x: x.get("last_active", ""), reverse=True)
        return result

    def total_users(self) -> int:
        return len(self._data["users"])

    # ─── Ban ──────────────────────────────────────────────────────────────────

    def is_banned(self, user_id: int) -> bool:
        return self.get_user(user_id).get("banned", False)

    def ban_user(self, user_id: int):
        key = str(user_id)
        if key in self._data["users"]:
            self._data["users"][key]["banned"] = True
            self._save()

    def unban_user(self, user_id: int):
        key = str(user_id)
        if key in self._data["users"]:
            self._data["users"][key]["banned"] = False
            self._save()

    def banned_count(self) -> int:
        return sum(1 for u in self._data["users"].values() if u.get("banned"))

    # ─── Subscription ─────────────────────────────────────────────────────────

    def has_active_sub(self, user_id: int) -> bool:
        u = self.get_user(user_id)
        exp = u.get("sub_expires")
        if not exp:
            return False
        try:
            return datetime.fromisoformat(exp) > datetime.now()
        except Exception:
            return False

    def sub_expires_str(self, user_id: int) -> Optional[str]:
        u = self.get_user(user_id)
        exp = u.get("sub_expires")
        if not exp:
            return None
        try:
            dt = datetime.fromisoformat(exp)
            return dt.strftime("%d.%m.%Y")
        except Exception:
            return None

    def activate_subscription(self, user_id: int, plan: str, days: int):
        key = str(user_id)
        if key not in self._data["users"]:
            self._data["users"][key] = self._default_user()
        u = self._data["users"][key]
        existing = u.get("sub_expires")
        if existing:
            try:
                base = max(datetime.fromisoformat(existing), datetime.now())
            except Exception:
                base = datetime.now()
        else:
            base = datetime.now()
        new_exp = base + timedelta(days=days)
        u["sub_expires"] = new_exp.isoformat(timespec="seconds")
        u["sub_plan"] = plan
        self._save()

    def subscribed_count(self) -> int:
        now = datetime.now()
        count = 0
        for u in self._data["users"].values():
            exp = u.get("sub_expires")
            if exp:
                try:
                    if datetime.fromisoformat(exp) > now:
                        count += 1
                except Exception:
                    pass
        return count

    # ─── Free uses ────────────────────────────────────────────────────────────

    def get_free_uses_today(self, user_id: int) -> int:
        u = self.get_user(user_id)
        if u.get("free_uses_date") != _today():
            return 0
        return u.get("free_uses_today", 0)

    def use_free_analysis(self, user_id: int):
        key = str(user_id)
        if key not in self._data["users"]:
            self._data["users"][key] = self._default_user()
        u = self._data["users"][key]
        if u.get("free_uses_date") != _today():
            u["free_uses_today"] = 0
            u["free_uses_date"] = _today()
        u["free_uses_today"] = u.get("free_uses_today", 0) + 1
        self._save()

    def can_analyze(self, user_id: int) -> bool:
        if self.has_active_sub(user_id):
            return True
        limit = self._data["settings"].get("free_limit", 3)
        return self.get_free_uses_today(user_id) < limit

    def free_left(self, user_id: int) -> int:
        limit = self._data["settings"].get("free_limit", 3)
        used = self.get_free_uses_today(user_id)
        return max(0, limit - used)

    # ─── Analyses ─────────────────────────────────────────────────────────────

    def record_analysis(self, user_id: int):
        self._data["total_analyses"] += 1
        key = str(user_id)
        if key in self._data["users"]:
            self._data["users"][key]["total_analyses"] = (
                self._data["users"][key].get("total_analyses", 0) + 1
            )
        self._save()

    def total_analyses(self) -> int:
        return self._data.get("total_analyses", 0)

    # ─── History ──────────────────────────────────────────────────────────────

    def add_history(self, user_id: int, url: str, score: int):
        key = str(user_id)
        self._data["history"].setdefault(key, [])
        self._data["history"][key].insert(0, {
            "url": url,
            "score": score,
            "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
        })
        self._data["history"][key] = self._data["history"][key][:10]
        self._save()

    def get_history(self, user_id: int) -> list[dict]:
        return self._data.get("history", {}).get(str(user_id), [])

    # ─── Pending invoices ─────────────────────────────────────────────────────

    def add_pending_invoice(self, invoice_id: int, user_id: int, plan: str):
        self._data["pending_invoices"].append({
            "invoice_id": invoice_id,
            "user_id": user_id,
            "plan": plan,
            "created_at": _now(),
        })
        self._save()

    def remove_pending_invoice(self, invoice_id: int):
        self._data["pending_invoices"] = [
            inv for inv in self._data["pending_invoices"]
            if inv["invoice_id"] != invoice_id
        ]
        self._save()

    def get_pending_invoices(self) -> list[dict]:
        return list(self._data.get("pending_invoices", []))

    # ─── Payments ─────────────────────────────────────────────────────────────

    def add_payment(self, user_id: int, plan: str, amount: float, currency: str):
        self._data["payments"].insert(0, {
            "user_id": user_id,
            "plan": plan,
            "amount": amount,
            "currency": currency,
            "paid_at": _now(),
        })
        self._save()

    def get_payments(self) -> list[dict]:
        return list(self._data.get("payments", []))

    def total_revenue(self) -> float:
        return sum(p.get("amount", 0) for p in self._data.get("payments", []))

    # ─── Settings ─────────────────────────────────────────────────────────────

    def get_settings(self) -> dict:
        return self._data.get("settings", {})

    def set_banner(self, file_id: Optional[str]):
        self._data["settings"]["banner_file_id"] = file_id
        self._save()

    def get_banner(self) -> Optional[str]:
        return self._data["settings"].get("banner_file_id")

    def set_price(self, plan: str, price: float):
        self._data["settings"]["prices"][plan] = price
        self._save()

    def get_prices(self) -> dict:
        return self._data["settings"].get("prices", {"week": 2.99, "month": 7.99, "quarter": 19.99})

    def set_free_limit(self, limit: int):
        self._data["settings"]["free_limit"] = limit
        self._save()

    def get_free_limit(self) -> int:
        return self._data["settings"].get("free_limit", 3)

    # ─── Today stats ──────────────────────────────────────────────────────────

    def new_users_today(self) -> int:
        today = _today()
        return sum(
            1 for u in self._data["users"].values()
            if u.get("first_seen", "")[:10] == today
        )


storage = Storage()
