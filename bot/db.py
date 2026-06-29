import os
import logging
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from bot.models import Base

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

engine = create_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_recycle=300,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def init_db():
    try:
        Base.metadata.create_all(bind=engine, checkfirst=True)
    except Exception as e:
        logger.warning("create_all had an issue (tables may already exist): %s", e)
        try:
            with engine.connect() as conn:
                for table in reversed(Base.metadata.sorted_tables):
                    try:
                        conn.execute(text(
                            f"CREATE TABLE IF NOT EXISTS {table.name} "
                            f"(LIKE {table.name} INCLUDING ALL)"
                        ))
                    except Exception:
                        pass
        except Exception:
            pass


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
