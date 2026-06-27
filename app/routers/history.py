"""
投稿履歴 API ルーター
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db, Post, PostStatus, User
from ..auth import get_current_user

router = APIRouter()


def serialize_post(post: Post) -> dict:
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
        "platform_post_ids": post.platform_post_ids or {},
        "created_at": _fmt(post.created_at),
    }


@router.get("/")
def get_history(
    platform: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Post).filter(Post.user_id == current_user.id)
    if platform:
        query = query.filter(Post.platforms.contains(platform))
    if status:
        query = query.filter(Post.status == status)

    posts = query.order_by(Post.created_at.desc()).all()
    return [serialize_post(p) for p in posts]


@router.get("/stats")
def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    uid = current_user.id
    total = db.query(Post).filter(Post.status == PostStatus.POSTED, Post.user_id == uid).count()
    failed = db.query(Post).filter(Post.status == PostStatus.FAILED, Post.user_id == uid).count()
    scheduled = db.query(Post).filter(Post.status == PostStatus.PENDING, Post.user_id == uid).count()
    drafts = db.query(Post).filter(Post.status == PostStatus.DRAFT, Post.user_id == uid).count()

    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    weekly = db.query(Post).filter(
        Post.status == PostStatus.POSTED,
        Post.posted_at >= week_ago,
        Post.user_id == uid,
    ).count()

    all_posted = db.query(Post).filter(Post.status == PostStatus.POSTED, Post.user_id == uid).all()
    platform_counts = {"x": 0, "facebook": 0, "threads": 0}
    for post in all_posted:
        for p in (post.platforms or []):
            if p in platform_counts:
                platform_counts[p] += 1

    return {
        "total_posted": total,
        "total_failed": failed,
        "scheduled": scheduled,
        "drafts": drafts,
        "weekly_posts": weekly,
        "by_platform": platform_counts,
    }