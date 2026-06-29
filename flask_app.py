import os
import uuid
import logging

from flask import Flask, render_template, request, jsonify, abort
from bot.db import init_db, get_session
from bot.models import User, Report, Setting
from bot.storage import storage

logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-change-me")

DOMAIN = os.getenv("REPLIT_DEV_DOMAIN", "localhost:5000")


def _ensure_db():
    try:
        init_db()
    except Exception:
        pass


_ensure_db()


def get_report_url(report_id: str) -> str:
    return f"https://{DOMAIN}/report/{report_id}"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ─── Registration ─────────────────────────────────────────────────────────────

@app.route("/register/<int:user_id>", methods=["GET", "POST"])
def register(user_id: int):
    try:
        user = storage.get_user(user_id)
    except Exception as e:
        logger.error("DB error in register: %s", e)
        return render_template("register.html", user_id=user_id, already_registered=False, success=False, error="Сервер временно недоступен.")

    if not user:
        abort(404)

    if user.get("web_registered"):
        return render_template("register.html", user_id=user_id, already_registered=True, success=False, error=None)

    if request.method == "POST":
        ip = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr
            or "unknown"
        )
        try:
            storage.complete_web_registration(user_id, ip)
            _notify_user(user_id)
            return render_template("register.html", user_id=user_id, already_registered=False, success=True, error=None)
        except Exception as e:
            logger.error("Registration error for %s: %s", user_id, e)
            return render_template("register.html", user_id=user_id, already_registered=False, success=False, error="Ошибка, попробуйте позже.")

    return render_template("register.html", user_id=user_id, already_registered=False, success=False, error=None)


def _notify_user(user_id: int):
    try:
        import asyncio
        from bot.config import BOT_TOKEN
        from aiogram import Bot

        bot = Bot(token=BOT_TOKEN)

        async def _send():
            await bot.send_message(user_id, "Регистрация завершена")
            await bot.session.close()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_send())
        loop.close()
    except Exception as e:
        logger.warning("Could not notify user %s: %s", user_id, e)


# ─── Report ───────────────────────────────────────────────────────────────────

@app.route("/report/<report_id>")
def view_report(report_id: str):
    try:
        report = storage.get_report(report_id)
    except Exception as e:
        logger.error("DB error loading report %s: %s", report_id, e)
        abort(500)

    if not report:
        abort(404)

    data = report.get("data") or {}
    return render_template("report.html", report=report, data=data)


# ─── LITE report submission (legacy endpoint — kept for compatibility) ─────────

@app.route("/api/report", methods=["POST"])
def submit_report():
    payload = request.get_json(silent=True)
    if not payload:
        return jsonify({"error": "No JSON"}), 400

    user_id = payload.get("user_id")
    report_token = payload.get("report_token")

    if not user_id or not report_token:
        return jsonify({"error": "Missing fields"}), 400

    try:
        with get_session() as session:
            token_key = f"lite_token_{user_id}"
            expected = session.query(Setting).filter_by(key=token_key).first()
            if not expected or expected.value != report_token:
                return jsonify({"error": "Invalid token"}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    rid = str(uuid.uuid4())
    data = {
        "mode": "lite",
        "target_url": payload.get("target_url"),
        "duration": payload.get("duration"),
        "total_requests": payload.get("total_requests"),
        "rps": payload.get("rps"),
        "success_rate": payload.get("success_rate"),
        "p95": payload.get("p95"),
        "p99": payload.get("p99"),
        "avg_latency": round(((payload.get("p95") or 0) + (payload.get("p99") or 0)) / 2, 1),
        "status_codes": {},
        "rps_timeline": [],
        "protection": {"detected": False},
        "session_cookies_used": 0,
    }

    try:
        with get_session() as session:
            session.add(Report(
                report_id=rid,
                user_id=user_id,
                report_type="load_test",
                target_url=payload.get("target_url"),
                data=data,
            ))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    report_url = get_report_url(rid)
    _notify_user_report(user_id, report_url)
    return jsonify({"ok": True, "report_url": report_url})


def _notify_user_report(user_id: int, report_url: str):
    try:
        import asyncio
        from bot.config import BOT_TOKEN
        from aiogram import Bot
        from aiogram.enums import ParseMode

        bot = Bot(token=BOT_TOKEN)

        async def _send():
            await bot.send_message(
                user_id,
                f"Тест завершён\n\n<a href='{report_url}'>Открыть отчёт</a>",
                parse_mode=ParseMode.HTML,
            )
            await bot.session.close()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(_send())
        loop.close()
    except Exception as e:
        logger.warning("Could not notify user %s: %s", user_id, e)
