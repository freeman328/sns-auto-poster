"""
投稿 API ルーター
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional

from ..database import get_db, Post, PostStatus, User
from ..scheduler import schedule_post, cancel_schedule
from ..auth import get_current_user
from ..poster import post_to_platforms

router = APIRouter()


class PostCreate(BaseModel):
    text: str
    platforms: List[str]
    image_urls: Optional[List[str]] = []
    scheduled_at: Optional[str] = None
    repeat: Optional[str] = None
    weekdays: Optional[List[int]] = None


class PostUpdate(BaseModel):
    text: Optional[str] = None
    platforms: Optional[List[str]] = None
    image_urls: Optional[List[str]] = None
    scheduled_at: Optional[str] = None


def serialize_post(post: Post) -> dict:
    """フロントエンドが確実にパースできる日時形式に変換する"""
    def _fmt(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")

    return {
        "id": post.id,
        "text": post.text,
        "platforms": post.platforms,
        "image_urls": post.image_urls or [],
        "scheduled_at": _fmt(post.scheduled_at),
        "posted_at": _fmt(post.posted_at),
        "status": post.status,
        "error_message": post.error_message,
        "created_at": _fmt(post.created_at),
        "repeat": post.repeat,
        "weekdays": post.weekdays,
    }


@router.get("/scheduled")
def get_scheduled(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    posts = (
        db.query(Post)
        .filter(
            Post.status == PostStatus.PENDING,
            Post.scheduled_at.isnot(None),
            Post.user_id == current_user.id,
        )
        .order_by(Post.scheduled_at.asc())
        .all()
    )
    return [serialize_post(p) for p in posts]


@router.get("/")
def get_posts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 【修正8】デバッグ用 print・余分なクエリを削除
    posts = (
        db.query(Post)
        .filter(Post.status == PostStatus.PENDING, Post.user_id == current_user.id)
        .order_by(Post.scheduled_at.asc())
        .all()
    )
    return [serialize_post(p) for p in posts]


@router.post("/")
def create_post(
    body: PostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """新規投稿作成"""
    sched_dt = None
    if body.scheduled_at:
        try:
            sched_dt = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "日時の形式が不正です")

    # 【修正5】即時投稿でも初期ステータスは PENDING にして、結果確定後に更新
    post = Post(
        text=body.text,
        platforms=body.platforms,
        image_urls=body.image_urls,
        scheduled_at=sched_dt,
        status=PostStatus.PENDING,
        user_id=current_user.id,
        repeat=body.repeat,
        weekdays=body.weekdays,
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    if sched_dt:
        schedule_post(post.id, sched_dt)
    else:
        results = post_to_platforms(
            post.text, post.platforms, post.image_urls, db, current_user.id
        )
        all_success = all(r.get("success") for r in results.values())
        post.status = PostStatus.POSTED if all_success else PostStatus.FAILED
        post.posted_at = datetime.now(timezone.utc)
        post.platform_post_ids = {
            p: r.get("post_id") for p, r in results.items() if r.get("success")
        }
        if not all_success:
            errors = {p: r.get("error") for p, r in results.items() if not r.get("success")}
            post.error_message = str(errors)
        db.commit()

    return serialize_post(post)


@router.post("/draft")
def save_draft(
    body: PostCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = Post(
        text=body.text,
        platforms=body.platforms,
        image_urls=body.image_urls,
        status=PostStatus.DRAFT,
        user_id=current_user.id,
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return serialize_post(post)


@router.delete("/{post_id}")
def delete_post(
    post_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    post = db.query(Post).filter(
        Post.id == post_id, Post.user_id == current_user.id
    ).first()
    if not post:
        raise HTTPException(404, "投稿が見つかりません")
    cancel_schedule(post_id)
    db.delete(post)
    db.commit()
    return {"message": "削除完了"}


class RepostBody(BaseModel):
    scheduled_at: Optional[str] = None
    repeat: Optional[str] = None
    weekdays: Optional[List[int]] = None


@router.post("/{post_id}/repost")
def repost(
    post_id: int,
    body: RepostBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    original = db.query(Post).filter(
        Post.id == post_id, Post.user_id == current_user.id
    ).first()
    if not original:
        raise HTTPException(404, "投稿が見つかりません")

    sched_dt = None
    if body.scheduled_at:
        try:
            sched_dt = datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "日時の形式が不正です")

    # 【修正5】即時再投稿も PENDING で作成してから結果を反映
    new_post = Post(
        text=original.text,
        platforms=original.platforms,
        image_urls=original.image_urls,
        scheduled_at=sched_dt,
        status=PostStatus.PENDING,
        user_id=current_user.id,
        repeat=body.repeat,
        weekdays=body.weekdays,
    )
    db.add(new_post)
    db.commit()
    db.refresh(new_post)

    if sched_dt:
        schedule_post(new_post.id, sched_dt)
    else:
        results = post_to_platforms(
            new_post.text, new_post.platforms, new_post.image_urls, db, current_user.id
        )
        all_success = all(r.get("success") for r in results.values())
        new_post.status = PostStatus.POSTED if all_success else PostStatus.FAILED
        new_post.posted_at = datetime.now(timezone.utc)
        new_post.platform_post_ids = {
            p: r.get("post_id") for p, r in results.items() if r.get("success")
        }
        if not all_success:
            errors = {p: r.get("error") for p, r in results.items() if not r.get("success")}
            new_post.error_message = str(errors)
        db.commit()

    return serialize_post(new_post)