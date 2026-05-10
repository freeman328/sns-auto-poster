"""
投稿履歴 API ルーター
"""

from fastapi import APIRouter, Depends, Query
from typing import Optional, List
from sqlalchemy.orm import Session
from datetime import datetime, timedelta

from ..database import get_db, Post, PostStatus

router = APIRouter()


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
    }


@router.get("/")
def get_history(
    platform: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """
    投稿履歴取得
    - platform: x / facebook / threads でフィルタ
    - status: posted / failed でフィルタ
    - days: 何日前まで遡るか (デフォルト30日)
    """
    since = datetime.utcnow() - timedelta(days=days)
    query = db.query(Post).filter(
        Post.status.in_([PostStatus.POSTED, PostStatus.FAILED]),
        Post.created_at >= since
    )

    if status:
        query = query.filter(Post.status == status)

    posts = query.order_by(Post.posted_at.desc()).offset(offset).limit(limit).all()

    # プラットフォームフィルタ（JSONカラムのためPython側で処理）
    if platform:
        posts = [p for p in posts if platform in (p.platforms or [])]

    return {
        "total": len(posts),
        "posts": [serialize_post(p) for p in posts]
    }


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """投稿統計サマリー"""
    total = db.query(Post).filter(Post.status == PostStatus.POSTED).count()
    failed = db.query(Post).filter(Post.status == PostStatus.FAILED).count()
    scheduled = db.query(Post).filter(
        Post.status == PostStatus.PENDING,
        Post.scheduled_at.isnot(None)
    ).count()
    drafts = db.query(Post).filter(Post.status == PostStatus.DRAFT).count()

    # 直近7日の投稿数
    week_ago = datetime.utcnow() - timedelta(days=7)
    weekly = db.query(Post).filter(
        Post.status == PostStatus.POSTED,
        Post.posted_at >= week_ago
    ).count()

    # プラットフォーム別
    all_posted = db.query(Post).filter(Post.status == PostStatus.POSTED).all()
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


@router.get("/search")
def search_history(
    q: str = Query(..., min_length=1),
    db: Session = Depends(get_db)
):
    """投稿テキスト検索"""
    posts = db.query(Post).filter(
        Post.text.contains(q),
        Post.status.in_([PostStatus.POSTED, PostStatus.FAILED, PostStatus.DRAFT])
    ).order_by(Post.created_at.desc()).limit(20).all()
    return [serialize_post(p) for p in posts]
