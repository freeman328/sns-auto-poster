"""
投稿 API ルーター (修正完全版)
"""
from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from ..database import get_db, Post, PostStatus, User
from ..scheduler import schedule_post, cancel_schedule
from ..auth import get_current_user

router = APIRouter()

class PostCreate(BaseModel):
    text: str
    platforms: List[str]
    image_urls: Optional[List[str]] = []
    scheduled_at: Optional[str] = None  # ISO8601 or None (即時)
    repeat: Optional[str] = None        # "daily" | "weekly" | None
    weekdays: Optional[List[int]] = None

class PostUpdate(BaseModel):
    text: Optional[str] = None
    platforms: Optional[List[str]] = None
    image_urls: Optional[List[str]] = None
    scheduled_at: Optional[str] = None

def serialize_post(post: Post) -> dict:
    return {
        "id": post.id,
        "text": post.text,
        "platforms": post.platforms,
        "image_urls": post.image_urls or [],
        "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None,
        "posted_at": post.posted_at.isoformat() if post.posted_at else None,
        "status": post.status,
        "error_message": post.error_message,
        "created_at": post.created_at.isoformat(),
    }

@router.get("/")
def get_posts(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """スケジュール中（待機中）の投稿一覧"""
    posts = db.query(Post).filter(
        Post.status == PostStatus.PENDING,
        Post.user_id == current_user.id
    ).order_by(Post.scheduled_at.asc()).all()
    return [serialize_post(p) for p in posts]

@router.post("/")
def create_post(body: PostCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    sched_dt = None
    if body.scheduled_at:
        try:
            # フロントから送られる Z（UTC）やタイムゾーン付きの文字列を安全にパース
            sched_dt = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "日時の形式が不正です")

    post = Post(
        text=body.text,
        platforms=body.platforms,
        image_urls=body.image_urls,
        scheduled_at=sched_dt,
        status=PostStatus.PENDING if sched_dt else PostStatus.POSTED,
        user_id=current_user.id,
        repeat=body.repeat,
        weekdays=body.weekdays
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    if sched_dt and post.status == PostStatus.PENDING:
        schedule_post(post.id, sched_dt)
    
    return serialize_post(post)

@router.post("/draft")
def save_draft(body: PostCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """下書き保存"""
    post = Post(
        text=body.text,
        platforms=body.platforms,
        image_urls=body.image_urls,
        status=PostStatus.DRAFT,
        user_id=current_user.id
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return serialize_post(post)

@router.delete("/{post_id}")
def delete_post(post_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """投稿削除（自身のもののみ）"""
    post = db.query(Post).filter(Post.id == post_id, Post.user_id == current_user.id).first()
    if not post:
        raise HTTPException(404, "投稿が見つかりません")
    cancel_schedule(post_id)
    db.delete(post)
    db.commit()
    return {"message": "削除完了"}