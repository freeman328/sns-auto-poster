"""
データベース設定 (SQLite + SQLAlchemy)
"""

from sqlalchemy import (
    create_engine, Column, Integer, String, Text,
    DateTime, Boolean, JSON, Enum
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import enum

import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/sns_poster.db")

# SupabaseはPostgreSQLなのでpsycopg2を使う
# SQLAlchemy 1.4以降は postgresql:// を postgresql+psycopg2:// に変換が必要
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

# SQLiteの場合のみcheck_same_threadが必要
if "sqlite" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class User(Base):
    """ユーザーテーブル"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(200), unique=True, nullable=False)
    hashed_password = Column(String(200), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PostStatus(str, enum.Enum):
    PENDING = "pending"
    POSTED = "posted"
    FAILED = "failed"
    DRAFT = "draft"


class Post(Base):
    """投稿スケジュール & 履歴テーブル"""
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    text = Column(Text, nullable=False)
    platforms = Column(JSON, nullable=False)          # ["x", "facebook", "threads"]
    image_urls = Column(JSON, default=[])             # ["/uploads/xxx.jpg"]
    scheduled_at = Column(DateTime, nullable=True)    # None = 即時投稿
    posted_at = Column(DateTime, nullable=True)
    status = Column(String(20), default=PostStatus.PENDING)
    error_message = Column(Text, nullable=True)
    platform_post_ids = Column(JSON, default={})      # {"x": "tweet_id", ...}
    user_id = Column(Integer, nullable=True)
    repeat = Column(String(10), nullable=True)       # None / "daily" / "weekly"
    weekdays = Column(JSON, nullable=True)             # [0,1,4] 毎週の場合
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Settings(Base):
    """APIキー設定テーブル"""
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True)
    platform = Column(String(50), nullable=False)
    user_id = Column(Integer, nullable=True)  # "x", "facebook", "threads"
    config = Column(JSON, default={})   # 暗号化推奨: APIキーなど
    is_connected = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()