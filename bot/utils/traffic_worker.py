import asyncio
import logging
import uuid
from datetime import datetime

from bot.db import get_session
from bot.models import Task, Report
from bot.utils.stress_profile import run_load_test, build_stress_profile
from bot.utils.site_analyzer import analyze_site

logger = logging.getLogger(__name__)


async def process_task(task_id: str, bot=None):
    user_id = None
    try:
        with get_session() as session:
            task = session.query(Task).filter_by(task_id=task_id).first()
            if not task:
                logger.warning("Task %s not found", task_id)
                return
            task.status = "running"
            task.started_at = datetime.utcnow()
            params = dict(task.params or {})
            task_type = task.task_type
            user_id = task.user_id
    except Exception as e:
        logger.error("Failed to load task %s: %s", task_id, e)
        return

    try:
        if task_type == "load_test":
            method_type = params.get("method_type", "auto")
            if method_type == "auto":
                try:
                    from bot.utils.stress_profile import auto_detect_method
                    method_type = await auto_detect_method(params["target_url"])
                    logger.info("Auto-detected method: %s for %s", method_type, params["target_url"])
                except Exception as e:
                    logger.warning("Auto-detect failed, using http_flood: %s", e)
                    method_type = "http_flood"
            profile = build_stress_profile(
                target_url=params["target_url"],
                mode=params.get("mode", "pro"),
                duration=params.get("duration", 60),
                concurrency=params.get("concurrency", 50),
                intensity=params.get("intensity", "medium"),
                method_type=method_type,
            )
            result = await run_load_test(profile)
            data = {
                "mode": profile.mode,
                "method_type": profile.method_type,
                "target_url": params["target_url"],
                "duration": round(result.elapsed, 1),
                "total_requests": result.total_requests,
                "rps": round(result.rps, 1),
                "success_rate": result.success_rate,
                "p95": result.p95,
                "p99": result.p99,
                "avg_latency": result.avg_latency,
                "status_codes": result.status_codes,
                "rps_timeline": result.rps_timeline,
                "protection": result.protection_info,
                "session_cookies_used": result.session_cookies_used,
                "error_breakdown": result.error_breakdown,
            }

        elif task_type == "analysis":
            raw = await analyze_site(params["target_url"])
            data = raw
            data["target_url"] = params["target_url"]

        else:
            raise ValueError(f"Unknown task type: {task_type}")

        report_id = str(uuid.uuid4())

        with get_session() as session:
            report = Report(
                report_id=report_id,
                user_id=user_id,
                report_type=task_type,
                target_url=params.get("target_url"),
                data=data,
            )
            session.add(report)
            task_obj = session.query(Task).filter_by(task_id=task_id).first()
            if task_obj:
                task_obj.status = "done"
                task_obj.finished_at = datetime.utcnow()
                task_obj.result = {"report_id": report_id}

        logger.info("Task %s done, report %s", task_id, report_id)

        if bot and user_id:
            import os
            domain = os.getenv("REPLIT_DEV_DOMAIN", "localhost:5000")
            report_url = f"https://{domain}/report/{report_id}"
            try:
                if task_type == "load_test":
                    mode_labels = {
                        "lite": "LITE-тест",
                        "pro": "PRO-тест",
                        "flood": "Flood-тест",
                    }
                    mode = params.get("mode", "lite")
                    label = mode_labels.get(mode, "Тест")
                    method = data.get("method_type", "auto").upper()
                    rps = data.get("rps", 0)
                    sr = data.get("success_rate", 0)
                    total = data.get("total_requests", 0)
                    text = (
                        f"⚡ <b>{label} завершён!</b>\n\n"
                        f"🌐 <code>{params.get('target_url', '')}</code>\n"
                        f"🔫 Метод: <b>{method}</b>\n"
                        f"📊 RPS: <b>{rps}</b> | Успех: <b>{sr}%</b>\n"
                        f"📦 Всего запросов: <b>{total}</b>\n\n"
                        f"📋 <a href='{report_url}'>Открыть отчёт</a>"
                    )
                else:
                    score = data.get("score", 0)
                    text = (
                        f"🔍 <b>Анализ завершён!</b>\n\n"
                        f"🌐 <code>{params.get('target_url', '')}</code>\n"
                        f"⭐ Оценка: <b>{score}/100</b>\n\n"
                        f"📋 <a href='{report_url}'>Открыть отчёт</a>"
                    )
                await bot.send_message(
                    user_id,
                    text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )
            except Exception as e:
                logger.warning("Failed to notify user %s: %s", user_id, e)

        return report_id

    except Exception as e:
        logger.error("Task %s failed: %s", task_id, e, exc_info=True)
        try:
            with get_session() as session:
                task_obj = session.query(Task).filter_by(task_id=task_id).first()
                if task_obj:
                    task_obj.status = "failed"
                    task_obj.finished_at = datetime.utcnow()
                    task_obj.result = {"error": str(e)}
        except Exception:
            pass

        if bot and user_id:
            try:
                await bot.send_message(
                    user_id,
                    f"❌ <b>Задача завершилась с ошибкой:</b>\n<code>{e}</code>",
                    parse_mode="HTML",
                )
            except Exception:
                pass


async def task_queue_worker(bot=None):
    logger.info("Task queue worker started")
    while True:
        try:
            with get_session() as session:
                pending = (
                    session.query(Task)
                    .filter_by(status="pending")
                    .order_by(Task.created_at)
                    .limit(3)
                    .all()
                )
                task_ids = [t.task_id for t in pending]
                for t in pending:
                    t.status = "queued"

            for task_id in task_ids:
                asyncio.create_task(process_task(task_id, bot=bot))

        except Exception as e:
            logger.error("Queue worker error: %s", e)

        await asyncio.sleep(5)


def enqueue_task(user_id: int, task_type: str, params: dict) -> str:
    task_id = str(uuid.uuid4())
    with get_session() as session:
        task = Task(
            task_id=task_id,
            user_id=user_id,
            task_type=task_type,
            status="pending",
            params=params,
        )
        session.add(task)
    return task_id
