"""
データベース設定 (SQLite + SQLAlchemy)
"""
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone
import enum
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/sns_poster.db")

if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

if "sqlite" in DATABASE_URL:
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# 【修正10】datetime.utcnow() は非推奨のため timezone-aware な関数に統一
def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    """ユーザーテーブル"""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(200), unique=True, nullable=False)
    hashed_password = Column(String(200), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=_utcnow)


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
    platforms = Column(JSON, nullable=False)
    image_urls = Column(JSON, default=list)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    posted_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), default=PostStatus.PENDING)
    error_message = Column(Text, nullable=True)
    platform_post_ids = Column(JSON, default=dict)
    # 【修正11】ForeignKey 制約を追加してデータ整合性を保証
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    repeat = Column(String(10), nullable=True)
    weekdays = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Settings(Base):
    """APIキー設定テーブル"""
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    platform = Column(String(50), nullable=False)
    # 【修正11】ForeignKey 制約を追加
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    # 【修正2】APIキーは暗号化して保存するため、raw テキストを直接入れない
    # config カラムは暗号化済みの文字列を格納する (poster.py / settings.py で復号)
    config = Column(JSON, default=dict)
    is_connected = Column(Boolean, default=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()