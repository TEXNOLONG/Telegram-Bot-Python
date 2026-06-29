from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float,
    Integer, JSON, String, Text
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    first_name = Column(String(255), default="")
    username = Column(String(255), nullable=True)
    ip_address = Column(String(64), nullable=True)
    registration_date = Column(DateTime, nullable=True)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_active = Column(DateTime, default=datetime.utcnow)
    tier = Column(String(20), default="lite")
    subscription_until = Column(DateTime, nullable=True)
    sub_plan = Column(String(50), nullable=True)
    banned = Column(Boolean, default=False)
    web_registered = Column(Boolean, default=False)
    free_uses_today = Column(Integer, default=0)
    free_uses_date = Column(String(20), default="")
    total_analyses = Column(Integer, default=0)


class Report(Base):
    __tablename__ = "reports"

    id = Column(Integer, primary_key=True)
    report_id = Column(String(36), unique=True, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False)
    report_type = Column(String(50), nullable=False)
    target_url = Column(String(1000), nullable=True)
    data = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class History(Base):
    __tablename__ = "history"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    target_url = Column(String(1000))
    test_type = Column(String(50))
    report_id = Column(String(36), nullable=True)
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    rps = Column(Float, nullable=True)
    success_rate = Column(Float, nullable=True)
    p95 = Column(Float, nullable=True)
    p99 = Column(Float, nullable=True)
    score = Column(Integer, nullable=True)


class AdminLog(Base):
    __tablename__ = "admin_log"

    id = Column(Integer, primary_key=True)
    admin_id = Column(BigInteger, nullable=False)
    action = Column(String(1000))
    timestamp = Column(DateTime, default=datetime.utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)
    task_id = Column(String(36), unique=True, nullable=False, index=True)
    user_id = Column(BigInteger, nullable=False)
    task_type = Column(String(50))
    status = Column(String(20), default="pending", index=True)
    params = Column(JSON)
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


class PendingInvoice(Base):
    __tablename__ = "pending_invoices"

    id = Column(Integer, primary_key=True)
    invoice_id = Column(BigInteger, unique=True, nullable=False)
    user_id = Column(BigInteger, nullable=False)
    plan = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    plan = Column(String(50))
    amount = Column(Float)
    currency = Column(String(20))
    paid_at = Column(DateTime, default=datetime.utcnow)


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text)
