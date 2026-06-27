"""
投稿履歴 / 統計 ルーター
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
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
        "platforms": post.platforms or [],
        "image_urls": post.image_urls or [],
        "scheduled_at": _fmt(post.scheduled_at),
        "posted_at": _fmt(post.posted_at),
        "status": post.status,
        "error_message": post.error_message,
        "created_at": _fmt(post.created_at),
        "repeat": post.repeat,
        "weekdays": post.weekdays,
    }


@router.get("/")
def get_history(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """投稿履歴（下書き・投稿済み・失敗）を返す"""
    posts = (
        db.query(Post)
        .filter(
            Post.user_id == current_user.id,
            Post.status.in_([PostStatus.POSTED, PostStatus.FAILED, PostStatus.DRAFT]),
        )
        .order_by(Post.created_at.desc())
        .limit(200)
        .all()
    )
    return [serialize_post(p) for p in posts]


@router.get("/stats")
def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """投稿統計を返す"""
    uid = current_user.id
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    total_posted = (
        db.query(Post)
        .filter(Post.user_id == uid, Post.status == PostStatus.POSTED)
        .count()
    )
    scheduled = (
        db.query(Post)
        .filter(
            Post.user_id == uid,
            Post.status == PostStatus.PENDING,
            Post.scheduled_at.isnot(None),
        )
        .count()
    )
    drafts = (
        db.query(Post)
        .filter(Post.user_id == uid, Post.status == PostStatus.DRAFT)
        .count()
    )
    weekly_posts = (
        db.query(Post)
        .filter(
            Post.user_id == uid,
            Post.status == PostStatus.POSTED,
            Post.posted_at >= week_ago,
        )
        .count()
    )

    # プラットフォーム別投稿数（SQLite JSON配列検索）
    posted_posts = (
        db.query(Post)
        .filter(Post.user_id == uid, Post.status == PostStatus.POSTED)
        .all()
    )
    by_platform = {"x": 0, "facebook": 0, "threads": 0}
    for p in posted_posts:
        for pl in (p.platforms or []):
            if pl in by_platform:
                by_platform[pl] += 1

    return {
        "total_posted": total_posted,
        "scheduled": scheduled,
        "drafts": drafts,
        "weekly_posts": weekly_posts,
        "by_platform": by_platform,
    }
