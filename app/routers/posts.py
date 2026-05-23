"""
投稿 API ルーター
"""

from fastapi import APIRouter, Depends, HTTPException, Body
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from ..database import get_db, Post, PostStatus
from ..scheduler import schedule_post, cancel_schedule, execute_post

router = APIRouter()


class PostCreate(BaseModel):
    text: str
    platforms: List[str]
    image_urls: Optional[List[str]] = []
    scheduled_at: Optional[str] = None  # ISO8601 or None (即時)
    repeat: Optional[str] = None        # "daily" | "weekly" | None
    weekdays: Optional[List[int]] = None  # [0,1,4] 毎週の場合


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
        "platform_post_ids": post.platform_post_ids or {},
        "created_at": post.created_at.isoformat(),
        "repeat": post.repeat,
        "weekdays": post.weekdays,
    }


@router.post("/")
def create_post(body: PostCreate, db: Session = Depends(get_db)):
    """投稿作成（即時 or スケジュール）"""
    CHAR_LIMITS = {"x": 280, "facebook": 63206, "threads": 500}
    for platform in body.platforms:
        limit = CHAR_LIMITS.get(platform, 999999)
        if len(body.text) > limit:
            raise HTTPException(400, f"{platform} の文字数制限({limit}字)を超えています")

    scheduled_dt = None
    if body.scheduled_at:
        try:
            dt_str = body.scheduled_at.replace("Z", "").split("+")[0].split(".")[0]
            scheduled_dt = datetime.fromisoformat(dt_str)
        except ValueError:
            raise HTTPException(400, "scheduled_at の形式が不正です (ISO8601)")

    post = Post(
        text=body.text,
        platforms=body.platforms,
        image_urls=body.image_urls or [],
        scheduled_at=scheduled_dt,
        status=PostStatus.PENDING if scheduled_dt else PostStatus.PENDING,
        repeat=body.repeat,
        weekdays=body.weekdays
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    if scheduled_dt:
        # スケジュール登録
        schedule_post(post.id, scheduled_dt)
        return {"message": "スケジュール登録完了", "post": serialize_post(post)}
    else:
        # 即時投稿
        execute_post(post.id)
        db.refresh(post)
        return {"message": "投稿完了", "post": serialize_post(post)}


@router.get("/")
def list_posts(status: Optional[str] = None, db: Session = Depends(get_db)):
    """投稿一覧取得"""
    query = db.query(Post)
    if status:
        query = query.filter(Post.status == status)
    posts = query.order_by(Post.created_at.desc()).all()
    return [serialize_post(p) for p in posts]


@router.get("/scheduled")
def list_scheduled(db: Session = Depends(get_db)):
    """スケジュール済み投稿一覧"""
    posts = db.query(Post).filter(
        Post.status == PostStatus.PENDING,
        Post.scheduled_at.isnot(None)
    ).order_by(Post.scheduled_at.asc()).all()
    return [serialize_post(p) for p in posts]


@router.get("/drafts")
def list_drafts(db: Session = Depends(get_db)):
    """下書き一覧"""
    posts = db.query(Post).filter(Post.status == PostStatus.DRAFT).all()
    return [serialize_post(p) for p in posts]


@router.get("/{post_id}")
def get_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "投稿が見つかりません")
    return serialize_post(post)


@router.put("/{post_id}")
def update_post(post_id: int, body: PostUpdate, db: Session = Depends(get_db)):
    """投稿更新（スケジュール前のみ）"""
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "投稿が見つかりません")
    if post.status == PostStatus.POSTED:
        raise HTTPException(400, "投稿済みの記事は編集できません")

    if body.text is not None:
        post.text = body.text
    if body.platforms is not None:
        post.platforms = body.platforms
    if body.image_urls is not None:
        post.image_urls = body.image_urls
    if body.scheduled_at is not None:
        new_dt = datetime.fromisoformat(body.scheduled_at)
        post.scheduled_at = new_dt
        cancel_schedule(post_id)
        schedule_post(post_id, new_dt)

    db.commit()
    db.refresh(post)
    return serialize_post(post)


@router.delete("/{post_id}")
def delete_post(post_id: int, db: Session = Depends(get_db)):
    post = db.query(Post).filter(Post.id == post_id).first()
    if not post:
        raise HTTPException(404, "投稿が見つかりません")
    cancel_schedule(post_id)
    db.delete(post)
    db.commit()
    return {"message": "削除完了"}


@router.post("/draft")
def save_draft(body: PostCreate, db: Session = Depends(get_db)):
    """下書き保存"""
    post = Post(
        text=body.text,
        platforms=body.platforms,
        image_urls=body.image_urls or [],
        status=PostStatus.DRAFT
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return {"message": "下書き保存完了", "post": serialize_post(post)}