import os
import logging
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import QueuePool
from app.core.config import settings

logger = logging.getLogger(__name__)

Base = declarative_base()

_engine = None
SessionLocal = None


def get_sqlalchemy_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.DATABASE_URL,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_pre_ping=True,
        )
    return _engine


def get_sqlalchemy_session() -> Generator[Session, None, None]:
    global SessionLocal
    if SessionLocal is None:
        SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=get_sqlalchemy_engine(),
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    yield from get_sqlalchemy_session()


def init_db():
    engine = get_sqlalchemy_engine()
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created/verified")
