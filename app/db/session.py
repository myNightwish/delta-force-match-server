from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

connect_args = {}
if settings.DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    # MVP：启动时建表。生产用 Alembic 迁移。
    from app.db.base import Base  # noqa: F401
    import app.models  # noqa: F401  注册所有模型

    Base.metadata.create_all(bind=engine)
